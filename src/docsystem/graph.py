"""Read-only section/reference graph: identities, edges and traversal.

This module builds a deterministic, explainable graph over the existing
Markdown catalog and section parser. It never edits Markdown and never
promotes an observed or generated edge to write authority. The public contract
is documented in `docs/agent-contract.md`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from docsystem.catalog import (
    INDEX_NAMES,
    INLINE_LINK_PATTERN,
    REFERENCE_DEFINITION_PATTERN,
    REFERENCE_USE_PATTERN,
    MarkdownCatalog,
    MarkdownDocument,
    ValidationIssue,
)
from docsystem.config import ProjectConfig
from docsystem.sections import FENCE_PATTERN, MarkdownSection

# Authority layers, in order of decreasing write-adjacent trust. None of them
# grants write permission by itself.
AUTHORED = "authored"
OBSERVED = "observed"
GENERATED = "generated"

# Relation-specific cycle policy.
_BLOCKING_RELATIONS = frozenset({"depends_on"})
_ERROR_RELATIONS = frozenset({"derived_from", "supersedes"})
_ALLOWED_CYCLE_RELATIONS = frozenset({"related", "contains", "validated_against"})
# `references` cycles are observed navigation evidence, never a semantic error.
_INFORMATIONAL_CYCLE_RELATIONS = frozenset({"references"})

BOUNDARY_CATEGORIES = frozenset(
    {
        "external",
        "resource",
        "outside-root",
        "unknown-document",
        "missing-anchor",
        "malformed",
        "federated",
    }
)


@dataclass(frozen=True)
class Address:
    """A stable graph address: `DOCUMENT-ID` or `DOCUMENT-ID#canonical-anchor`."""

    document_id: str
    anchor: str | None = None

    @property
    def text(self) -> str:
        return self.document_id if self.anchor is None else f"{self.document_id}#{self.anchor}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.text


def parse_address(raw: str) -> Address:
    """Parse `ID` or `ID#anchor`, rejecting empty document IDs or anchors."""

    if "#" in raw:
        document_id, anchor = raw.split("#", 1)
        if not document_id or not anchor:
            raise ValueError(f"malformed graph address: {raw!r}")
        return Address(document_id, anchor)
    if not raw:
        raise ValueError("graph address must not be empty")
    return Address(raw, None)


@dataclass(frozen=True)
class GraphEdge:
    """A typed edge between two graph addresses.

    `authority` distinguishes authored metadata, observed Markdown references
    and generated containment; none of them implies
    write permission on its own.
    """

    source: Address
    target: Address
    relation: str
    authority: str
    origin: str
    reason: str | None = None
    pin: int | None = None


@dataclass(frozen=True)
class Boundary:
    """A visible boundary where a raw target cannot become a graph edge."""

    source: Address
    raw_target: str
    category: str
    reason: str


@dataclass(frozen=True)
class TraversalPath:
    """One deterministic minimal proving path from the query address."""

    addresses: tuple[Address, ...]


@dataclass(frozen=True)
class TraversalResult:
    """One reachable address with the edge that first proves reachability."""

    address: Address
    relation: str
    authority: str
    origin: str
    distance: int
    direct: bool
    path: TraversalPath
    reason: str | None = None


@dataclass(frozen=True)
class CycleDiagnostic:
    relation: str
    severity: str
    members: tuple[str, ...]


@dataclass(frozen=True)
class SectionReferenceGraph:
    """Deterministic forward/reverse indexes over authored/observed/generated edges."""

    edges: tuple[GraphEdge, ...]
    boundaries: tuple[Boundary, ...]

    @cached_property
    def _forward(self) -> dict[str, tuple[GraphEdge, ...]]:
        grouped: dict[str, list[GraphEdge]] = {}
        for edge in self.edges:
            grouped.setdefault(edge.source.text, []).append(edge)
        return {key: tuple(value) for key, value in grouped.items()}

    @cached_property
    def _reverse(self) -> dict[str, tuple[GraphEdge, ...]]:
        grouped: dict[str, list[GraphEdge]] = {}
        for edge in self.edges:
            grouped.setdefault(edge.target.text, []).append(edge)
        return {key: tuple(value) for key, value in grouped.items()}

    def forward(self, address: Address) -> tuple[GraphEdge, ...]:
        return self._forward.get(address.text, ())

    def reverse_edges(self, address: Address) -> tuple[GraphEdge, ...]:
        return self._reverse.get(address.text, ())

    def boundaries_from(self, address: Address) -> tuple[Boundary, ...]:
        return tuple(
            sorted(
                (
                    boundary
                    for boundary in self.boundaries
                    if boundary.source == address
                ),
                key=lambda boundary: (boundary.category, boundary.raw_target),
            )
        )

    def known_addresses(self) -> frozenset[str]:
        return frozenset(self._forward) | frozenset(self._reverse)

    def traverse(
        self, start: Address, *, reverse: bool = False, transitive: bool = False
    ) -> tuple[TraversalResult, ...]:
        """Deterministic BFS: one minimal-distance result per reachable address."""

        return traverse(
            start,
            forward=self.forward,
            reverse_edges=self.reverse_edges,
            reverse=reverse,
            transitive=transitive,
        )


class ProjectionUnavailable(Exception):
    """Raised by a lazy shard-backed edge provider when a shard fails verification.

    The `references` CLI command treats this as an atomic fallback signal: the
    whole query is recomputed from direct Markdown rather than reporting a
    partially projected result.
    """


def traverse(
    start: Address,
    *,
    forward: Callable[[Address], tuple[GraphEdge, ...]],
    reverse_edges: Callable[[Address], tuple[GraphEdge, ...]],
    reverse: bool = False,
    transitive: bool = False,
) -> tuple[TraversalResult, ...]:
    """Deterministic BFS over any forward/reverse edge provider.

    `forward`/`reverse_edges` are callables `Address -> tuple[GraphEdge, ...]`;
    this lets an in-memory `SectionReferenceGraph` and a lazy, shard-backed
    provider (see `projection.targeted_forward_edges`) share one traversal
    implementation. Ties among multiple edges reaching the same address at the
    same distance are broken by `(relation, authority, address)` so the chosen
    proving path is stable across runs. Direct-only unless `transitive`.
    """

    start_key = start.text
    visited: dict[str, TraversalResult] = {}
    frontier: list[tuple[Address, tuple[Address, ...]]] = [(start, (start,))]
    distance = 0
    while frontier:
        distance += 1
        candidates: list[
            tuple[str, str, str, str | None, Address, tuple[Address, ...]]
        ] = []
        for address, path in frontier:
            incident = reverse_edges(address) if reverse else forward(address)
            for edge in incident:
                neighbor = edge.source if reverse else edge.target
                candidates.append(
                    (
                        edge.relation,
                        edge.authority,
                        edge.origin,
                        edge.reason,
                        neighbor,
                        (*path, neighbor),
                    )
                )
        candidates.sort(key=lambda item: (item[0], item[1], item[4].text))
        next_frontier: list[tuple[Address, tuple[Address, ...]]] = []
        for relation, authority, origin, reason, neighbor, path in candidates:
            key = neighbor.text
            if key == start_key or key in visited:
                continue
            visited[key] = TraversalResult(
                address=neighbor,
                relation=relation,
                authority=authority,
                origin=origin,
                distance=distance,
                direct=distance == 1,
                path=TraversalPath(path),
                reason=reason,
            )
            next_frontier.append((neighbor, path))
        if not transitive:
            break
        frontier = next_frontier
    return tuple(
        sorted(
            visited.values(),
            key=lambda result: (
                result.distance,
                result.relation,
                result.authority,
                result.address.text,
            ),
        )
    )


def traverse_reasons(
    start: Address,
    *,
    forward: Callable[[Address], tuple[GraphEdge, ...]],
    reverse_edges: Callable[[Address], tuple[GraphEdge, ...]],
    reverse: bool = False,
    transitive: bool = False,
) -> tuple[TraversalResult, ...]:
    """Deterministic BFS keeping every distinct edge reaching an address.

    `traverse` keeps exactly one deterministic-minimal edge per address, so an
    alternate authored or observed edge reaching the same address at the same
    minimal distance is discarded once BFS finds the first one. This variant
    keeps every distinct `(relation, authority, origin, reason)` signature
    that first reaches an address, at that address's minimal distance -- used
    by `change-plan` (see `docsystem.change_plan`) to aggregate every
    deterministic inclusion reason instead of reporting only one. Expansion
    beyond an address still happens exactly once, at the round it is first
    reached, so results stay bounded on a cyclic graph.
    """

    start_key = start.text
    collected: dict[
        str,
        dict[tuple[str, str, str, str | None, tuple[str, ...]], TraversalResult],
    ] = {}
    expanded: set[str] = {start_key}
    frontier: list[tuple[Address, tuple[Address, ...]]] = [(start, (start,))]
    distance = 0
    while frontier:
        distance += 1
        by_neighbor: dict[str, list[tuple[tuple[Address, ...], GraphEdge]]] = {}
        for address, path in frontier:
            incident = reverse_edges(address) if reverse else forward(address)
            for edge in incident:
                neighbor = edge.source if reverse else edge.target
                key = neighbor.text
                if key == start_key or key in expanded:
                    continue
                by_neighbor.setdefault(key, []).append((path, edge))
        next_frontier: list[tuple[Address, tuple[Address, ...]]] = []
        for key in sorted(by_neighbor):
            entries = sorted(
                by_neighbor[key],
                key=lambda item: (
                    item[1].relation,
                    item[1].authority,
                    item[1].origin,
                    item[1].reason or "",
                    tuple(step.text for step in item[0]),
                ),
            )
            bucket = collected.setdefault(key, {})
            best_path: tuple[Address, ...] | None = None
            best_neighbor: Address | None = None
            for path, edge in entries:
                neighbor = edge.source if reverse else edge.target
                new_path = (*path, neighbor)
                signature = (
                    edge.relation,
                    edge.authority,
                    edge.origin,
                    edge.reason,
                    tuple(step.text for step in new_path),
                )
                if signature not in bucket:
                    bucket[signature] = TraversalResult(
                        address=neighbor,
                        relation=edge.relation,
                        authority=edge.authority,
                        origin=edge.origin,
                        distance=distance,
                        direct=distance == 1,
                        path=TraversalPath(new_path),
                        reason=edge.reason,
                    )
                if best_path is None or tuple(step.text for step in new_path) < tuple(
                    step.text for step in best_path
                ):
                    best_path = new_path
                    best_neighbor = neighbor
            expanded.add(key)
            assert best_neighbor is not None and best_path is not None
            next_frontier.append((best_neighbor, best_path))
        if not transitive:
            break
        frontier = next_frontier
    flat = [result for bucket in collected.values() for result in bucket.values()]
    return tuple(
        sorted(
            flat,
            key=lambda result: (
                result.address.text,
                result.distance,
                result.relation,
                result.authority,
                result.origin,
                result.reason or "",
            ),
        )
    )


def _mask_fenced(text: str) -> str:
    """Blank fenced-code line contents while preserving line count/offsets.

    Reuses `sections.FENCE_PATTERN` (the same fence definition the section
    parser uses) so link discovery and section parsing agree on what counts
    as fenced code; this is link masking, not a second section parser.
    """

    lines = text.splitlines()
    masked: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    for line in lines:
        fence = FENCE_PATTERN.match(line)
        if fence_character is None and fence:
            marker = fence.group(1)
            if marker[0] == "`" and "`" in line[fence.end() :]:
                masked.append(line)
                continue
            fence_character = marker[0]
            fence_length = len(marker)
            masked.append("")
            continue
        if fence_character is not None:
            masked.append("")
            if (
                fence is not None
                and fence.group(1)[0] == fence_character
                and len(fence.group(1)) >= fence_length
                and not line[fence.end() :].strip()
            ):
                fence_character = None
                fence_length = 0
            continue
        masked.append(line)
    return "\n".join(masked)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _iter_raw_links(masked_text: str) -> tuple[tuple[str, int], ...]:
    """Return every non-fenced, non-image link target with its source line."""

    definitions = {
        match.group(1).casefold(): match.group(2) or match.group(3)
        for match in REFERENCE_DEFINITION_PATTERN.finditer(masked_text)
    }
    results: list[tuple[str, int]] = []
    for match in INLINE_LINK_PATTERN.finditer(masked_text):
        raw = match.group(1) or match.group(2)
        if raw:
            results.append((raw, _line_of(masked_text, match.start())))
    for match in REFERENCE_USE_PATTERN.finditer(masked_text):
        label = (match.group(2) or match.group(1)).casefold()
        target = definitions.get(label)
        if target:
            results.append((target, _line_of(masked_text, match.start())))
    return tuple(sorted(results, key=lambda item: item[1]))


def _containing_section(
    sections: tuple[MarkdownSection, ...], line: int
) -> MarkdownSection | None:
    candidates = [
        section for section in sections if section.start_line <= line <= section.end_line
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda section: section.start_line)


def _resolve_reference(
    document: MarkdownDocument,
    raw: str,
    line: int,
    root: Path,
    documents_by_path: dict[PurePosixPath, MarkdownDocument],
) -> GraphEdge | Boundary | None:
    assert document.metadata is not None
    source_section = _containing_section(document.sections, line)
    source = Address(
        document.metadata.document_id,
        source_section.anchor if source_section is not None else None,
    )
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc:
        return Boundary(source, raw, "external", "external URL")

    if not parsed.path:
        fragment = unquote(parsed.fragment)
        if not fragment:
            return Boundary(
                source,
                raw,
                "malformed",
                "link target has neither a document path nor a section fragment",
            )
        target_document = document
    else:
        decoded = unquote(parsed.path).replace("\\", "/")
        try:
            candidate = (root / document.path.parent / decoded).resolve()
            relative = PurePosixPath(candidate.relative_to(root.resolve()).as_posix())
        except ValueError:
            return Boundary(source, raw, "outside-root", "path resolves outside documentation root")
        if candidate.is_dir():
            index = next(
                (
                    item
                    for item in sorted(candidate.iterdir(), key=lambda item: item.name.lower())
                    if item.is_file() and item.name.lower() in INDEX_NAMES
                ),
                None,
            )
            if index is None:
                return Boundary(
                    source, raw, "unknown-document",
                    "directory has no index Markdown file",
                )
            relative = PurePosixPath(index.relative_to(root.resolve()).as_posix())
        if relative.suffix.lower() != ".md":
            return Boundary(source, raw, "resource", "non-document resource")
        target_document = documents_by_path.get(relative)
        if target_document is None or target_document.metadata is None:
            return Boundary(
                source, raw, "unknown-document",
                f"{relative.as_posix()} is not a cataloged document with valid metadata",
            )
        fragment = unquote(parsed.fragment)

    target_id = target_document.metadata.document_id
    if fragment:
        anchors = {section.anchor for section in target_document.sections}
        if fragment not in anchors:
            return Boundary(
                source, raw, "missing-anchor",
                f"{target_id}#{fragment} has no matching section anchor",
            )
        anchor: str | None = fragment
    else:
        anchor = None
    return GraphEdge(
        source=source,
        target=Address(target_id, anchor),
        relation="references",
        authority=OBSERVED,
        origin="markdown-link",
        reason=f"line {line}",
    )


def build_reference_graph(
    catalog: MarkdownCatalog, config: ProjectConfig
) -> SectionReferenceGraph:
    """Build the full graph: authored metadata, observed links, generated containment."""

    root = config.documentation_root
    counts = Counter(
        document.metadata.document_id
        for document in catalog.documents
        if document.metadata is not None
    )
    known_ids = {document_id for document_id, count in counts.items() if count == 1}
    documents_by_path = {
        document.path: document
        for document in catalog.documents
        if document.metadata is not None and document.metadata.document_id in known_ids
    }

    edges: list[GraphEdge] = []
    boundaries: list[Boundary] = []
    for document in catalog.documents:
        if document.metadata is None or document.metadata.document_id not in known_ids:
            continue
        source_id = document.metadata.document_id
        document_address = Address(source_id, None)
        for section in document.sections:
            edges.append(
                GraphEdge(
                    document_address,
                    Address(source_id, section.anchor),
                    "contains",
                    GENERATED,
                    "section-parser",
                )
            )
        for reference in document.metadata.references:
            if reference.target_id in known_ids and reference.target_id != source_id:
                edges.append(
                    GraphEdge(
                        document_address,
                        Address(reference.target_id, None),
                        reference.relation,
                        AUTHORED,
                        "metadata",
                        pin=reference.expected_revision,
                    )
                )
        for reference in document.metadata.federated_references:
            boundaries.append(
                Boundary(
                    document_address,
                    reference.target,
                    "federated",
                    "requires workspace federation",
                )
            )
        masked = _mask_fenced(document.content)
        for raw, line in _iter_raw_links(masked):
            result = _resolve_reference(document, raw, line, root, documents_by_path)
            if result is None:
                continue
            if isinstance(result, Boundary):
                boundaries.append(result)
            else:
                edges.append(result)

    for legacy in catalog.legacy_edges:
        if legacy.source_id in known_ids and legacy.target_id in known_ids:
            edges.append(
                GraphEdge(
                    Address(legacy.source_id, None),
                    Address(legacy.target_id, None),
                    legacy.relation,
                    AUTHORED,
                    "legacy-metadata",
                )
            )

    return SectionReferenceGraph(tuple(edges), tuple(boundaries))


# --- Cycle diagnostics ------------------------------------------------------


def _strongly_connected_components(
    nodes: frozenset[str], adjacency: dict[str, set[str]]
) -> tuple[tuple[str, ...], ...]:
    """Deterministic iterative Tarjan SCC over a relation-specific subgraph."""

    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    result: list[tuple[str, ...]] = []

    def strongconnect(root: str) -> None:
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, child_index = work[-1]
            if child_index == 0:
                indices[node] = index_counter[0]
                lowlink[node] = index_counter[0]
                index_counter[0] += 1
                stack.append(node)
                on_stack.add(node)
            neighbors = sorted(adjacency.get(node, ()))
            if child_index < len(neighbors):
                work[-1] = (node, child_index + 1)
                neighbor = neighbors[child_index]
                if neighbor not in indices:
                    work.append((neighbor, 0))
                    continue
                if neighbor in on_stack:
                    lowlink[node] = min(lowlink[node], indices[neighbor])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == indices[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                result.append(tuple(sorted(component)))

    for node in sorted(nodes):
        if node not in indices:
            strongconnect(node)
    return tuple(result)


def detect_cycles(edges: tuple[GraphEdge, ...]) -> tuple[CycleDiagnostic, ...]:
    """Classify cycles according to each relation's public graph policy."""

    by_relation: dict[str, list[GraphEdge]] = {}
    for edge in edges:
        by_relation.setdefault(edge.relation, []).append(edge)

    diagnostics: list[CycleDiagnostic] = []
    for relation, relation_edges in by_relation.items():
        if (
            relation in _ALLOWED_CYCLE_RELATIONS
            or relation in _INFORMATIONAL_CYCLE_RELATIONS
        ):
            continue
        if relation not in _BLOCKING_RELATIONS and relation not in _ERROR_RELATIONS:
            continue
        adjacency: dict[str, set[str]] = {}
        nodes: set[str] = set()
        self_loops: set[str] = set()
        for edge in relation_edges:
            source = edge.source.text
            target = edge.target.text
            nodes.add(source)
            nodes.add(target)
            if source == target:
                self_loops.add(source)
                continue
            adjacency.setdefault(source, set()).add(target)
        # Both blocking (`depends_on`) and error (`derived_from`, `supersedes`)
        # relations report as "error" severity; the message text below still
        # distinguishes "blocks dependency ordering" from "is an error".
        for component in _strongly_connected_components(frozenset(nodes), adjacency):
            if len(component) > 1:
                diagnostics.append(CycleDiagnostic(relation, "error", component))
        for node in sorted(self_loops):
            diagnostics.append(CycleDiagnostic(relation, "error", (node,)))
    return tuple(
        sorted(diagnostics, key=lambda diagnostic: (diagnostic.relation, diagnostic.members))
    )


def graph_validation_issues(
    catalog: MarkdownCatalog, config: ProjectConfig
) -> tuple[ValidationIssue, ...]:
    """Relation-specific cycle and dead-anchor diagnostics for validate/doctor.

    Cycle diagnostics are reported against every document participating in the
    cycle; `graph_issues` compatible severity so `depends_on`/`derived_from`/
    `supersedes` cycles behave like existing blocking metadata errors, while
    `missing-anchor` boundaries surface as non-blocking warnings (observed
    observed references never gain write or semantic authority).
    """

    paths_by_id = {
        document.metadata.document_id: document.path
        for document in catalog.documents
        if document.metadata is not None
    }
    graph = build_reference_graph(catalog, config)
    issues: list[ValidationIssue] = []
    for cycle in detect_cycles(graph.edges):
        members = ", ".join(cycle.members)
        verb = (
            "blocks dependency ordering"
            if cycle.relation in _BLOCKING_RELATIONS
            else "is an error"
        )
        for member in cycle.members:
            document_id = member.split("#", 1)[0]
            path = paths_by_id.get(document_id)
            if path is None:
                continue
            issues.append(
                ValidationIssue(
                    path,
                    f"{cycle.relation} cycle {verb}: {members}",
                    severity=cycle.severity,
                    affects_graph=True,
                )
            )
    for boundary in graph.boundaries:
        if boundary.category != "missing-anchor":
            continue
        path = paths_by_id.get(boundary.source.document_id)
        if path is None:
            continue
        issues.append(
            ValidationIssue(
                path,
                f"reference {boundary.raw_target!r} from {boundary.source.text}: "
                f"{boundary.reason}",
                severity="warning",
                category="dead-reference",
            )
        )
    return tuple(
        sorted(
            issues,
            key=lambda issue: (issue.path.as_posix(), issue.severity, issue.message),
        )
    )
