"""Read-only federation over independent workspace-owned Markdown catalogs."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

from docsystem.catalog import (
    MarkdownCatalog,
    MarkdownDocument,
    build_catalog,
    build_dependency_graph,
    validate_catalog,
)
from docsystem.config import ProjectConfig, is_historical_snapshot, load_config
from docsystem.graph import build_reference_graph
from docsystem.metadata import DOCUMENT_ID_PATTERN
from docsystem.sections import extract_navigation, extract_section
from docsystem.workspace import Workspace, evaluate_source

_SOURCE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


class FederationError(ValueError):
    """A complete federated answer cannot be proven."""


@dataclass(frozen=True, order=True)
class QualifiedAddress:
    """A workspace-qualified `source::ID[#anchor]` address."""

    source: str
    document_id: str
    anchor: str | None = None

    @property
    def document(self) -> str:
        return f"{self.source}::{self.document_id}"

    @property
    def text(self) -> str:
        return self.document if self.anchor is None else f"{self.document}#{self.anchor}"


def parse_qualified_address(raw: str, *, allow_anchor: bool = True) -> QualifiedAddress:
    """Parse one qualified address without consulting a workspace."""

    document, marker, anchor = raw.partition("#")
    if marker and (not allow_anchor or not anchor or "#" in anchor):
        raise FederationError(f"malformed qualified address: {raw!r}")
    source, separator, document_id = document.partition("::")
    if (
        separator != "::"
        or not _SOURCE_PATTERN.fullmatch(source)
        or DOCUMENT_ID_PATTERN.fullmatch(document_id) is None
        or "::" in document_id
    ):
        raise FederationError(f"malformed qualified address: {raw!r}")
    return QualifiedAddress(source, document_id, anchor if marker else None)


@dataclass(frozen=True)
class FederatedDocument:
    """One document with identity qualified by its owning workspace source."""

    address: QualifiedAddress
    visibility: str
    role: str
    path: str
    revision: int
    document_type: str | None
    status: str | None
    historical_snapshot: bool
    content: str
    source: MarkdownDocument
    navigation_extend_through: tuple[str, ...]


@dataclass(frozen=True, order=True)
class FederatedEdge:
    """One authored semantic edge in the federated graph."""

    relation: str
    source: QualifiedAddress
    target: QualifiedAddress
    expected_revision: int | None = None


@dataclass(frozen=True, order=True)
class FederationBoundary:
    """A visible resource/external relation that cannot become a document edge."""

    source: QualifiedAddress
    relation: str
    raw_target: str
    reason: str


@dataclass(frozen=True, order=True)
class FederationMigration:
    """One resolved legacy path retained as visible adoption evidence."""

    source: QualifiedAddress
    relation: str
    value: str
    target: QualifiedAddress


@dataclass(frozen=True, order=True)
class FederatedReferenceEdge:
    """A qualified authored, observed or generated section-graph edge."""

    relation: str
    authority: str
    source: QualifiedAddress
    target: QualifiedAddress
    origin: str
    reason: str | None = None
    pin: int | None = None


@dataclass(frozen=True, order=True)
class FederatedReferenceBoundary:
    """A visible qualified structural-reference boundary."""

    source: QualifiedAddress
    raw_target: str
    category: str
    reason: str


@dataclass(frozen=True)
class FederatedCatalog:
    """A deterministic, immutable snapshot of every workspace source."""

    documents: tuple[FederatedDocument, ...]
    edges: tuple[FederatedEdge, ...]
    boundaries: tuple[FederationBoundary, ...]
    migrations: tuple[FederationMigration, ...]
    reference_edges: tuple[FederatedReferenceEdge, ...]
    reference_boundaries: tuple[FederatedReferenceBoundary, ...]

    @property
    def by_address(self) -> dict[str, FederatedDocument]:
        return {item.address.document: item for item in self.documents}

    def find(self, address: QualifiedAddress) -> FederatedDocument:
        document = self.by_address.get(address.document)
        if document is None:
            raise FederationError(f"federated document not found: {address.document}")
        if address.anchor is not None and address.anchor not in {
            section.anchor for section in document.source.sections
        }:
            raise FederationError(f"anchor not found in {address.document}: {address.anchor}")
        return document

    def outgoing(self, address: QualifiedAddress) -> tuple[FederatedEdge, ...]:
        return tuple(edge for edge in self.edges if edge.source.document == address.document)

    def incoming(self, address: QualifiedAddress) -> tuple[FederatedEdge, ...]:
        return tuple(edge for edge in self.edges if edge.target.document == address.document)


def _catalog_error(source: str, path: str, message: str) -> FederationError:
    return FederationError(f"{source}::{path}: {message}")


def _cyclic_members(
    edges: set[FederatedEdge], relation: str
) -> tuple[str, ...]:
    """Return every node that can reach itself through one relation."""

    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        if edge.relation == relation:
            adjacency.setdefault(edge.source.document, set()).add(edge.target.document)
    cyclic: list[str] = []
    for start in sorted(adjacency):
        pending = list(sorted(adjacency[start], reverse=True))
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == start:
                cyclic.append(start)
                break
            if current in visited:
                continue
            visited.add(current)
            pending.extend(
                sorted(adjacency.get(current, ()), reverse=True)
            )
    return tuple(cyclic)


def build_federated_catalog(workspace: Workspace) -> FederatedCatalog:
    """Build a complete direct-Markdown federation or fail closed.

    Every registered source must be available and every source catalog must be
    structurally valid. Cross-source relations remain authored Markdown values
    in ordinary relation fields, using `source::DOCUMENT-ID`.
    """

    documents: list[FederatedDocument] = []
    local_catalogs: dict[str, tuple[ProjectConfig, MarkdownCatalog]] = {}
    errors: list[str] = []
    if not workspace.sources:
        raise FederationError("workspace federation requires at least one source")
    for workspace_source in workspace.sources:
        status = evaluate_source(workspace_source)
        if not status.available:
            errors.append(
                f"workspace source is unavailable: {status.name} ({status.reason})"
            )
            continue
        try:
            config = load_config(workspace_source.project_root)
            catalog = build_catalog(config)
        except (OSError, UnicodeError, ValueError):
            errors.append(
                f"workspace source catalog is unreadable: {workspace_source.name}"
            )
            continue
        local_catalogs[workspace_source.name] = (config, catalog)
        for issue in validate_catalog(catalog, config):
            if issue.severity == "warning":
                continue
            errors.append(
                str(
                    _catalog_error(
                        workspace_source.name, issue.path.as_posix(), issue.message
                    )
                )
            )
        for document in catalog.documents:
            if document.metadata is None:
                continue
            documents.append(
                FederatedDocument(
                    address=QualifiedAddress(
                        workspace_source.name, document.metadata.document_id
                    ),
                    visibility=workspace_source.visibility,
                    role=document.role,
                    path=document.path.as_posix(),
                    revision=document.metadata.revision,
                    document_type=document.metadata.document_type,
                    status=document.metadata.status,
                    historical_snapshot=is_historical_snapshot(
                        config,
                        document.metadata.document_type,
                        document.metadata.status,
                    ),
                    content=document.content,
                    source=document,
                    navigation_extend_through=config.navigation_extend_through,
                )
            )
    if errors:
        raise FederationError("\n".join(sorted(set(errors))))

    by_address = {document.address.document: document for document in documents}
    edges: set[FederatedEdge] = set()
    boundaries: set[FederationBoundary] = set()
    migrations: set[FederationMigration] = set()
    reference_edges: set[FederatedReferenceEdge] = set()
    reference_boundaries: set[FederatedReferenceBoundary] = set()
    blocking_boundaries: set[FederationBoundary] = set()
    for source_name, (_, catalog_value) in sorted(local_catalogs.items()):
        config = local_catalogs[source_name][0]
        local_reference_graph = build_reference_graph(catalog_value, config)
        for edge in local_reference_graph.edges:
            reference_edges.add(
                FederatedReferenceEdge(
                    edge.relation,
                    edge.authority,
                    QualifiedAddress(
                        source_name, edge.source.document_id, edge.source.anchor
                    ),
                    QualifiedAddress(
                        source_name, edge.target.document_id, edge.target.anchor
                    ),
                    edge.origin,
                    edge.reason,
                    edge.pin,
                )
            )
        for boundary in local_reference_graph.boundaries:
            if boundary.category == "federated":
                continue
            reference_boundaries.add(
                FederatedReferenceBoundary(
                    QualifiedAddress(
                        source_name,
                        boundary.source.document_id,
                        boundary.source.anchor,
                    ),
                    boundary.raw_target,
                    boundary.category,
                    boundary.reason,
                )
            )
        for edge in build_dependency_graph(catalog_value).edges:
            edges.add(
                FederatedEdge(
                    edge.relation,
                    QualifiedAddress(source_name, edge.source_id),
                    QualifiedAddress(source_name, edge.target_id),
                    edge.expected_revision,
                )
            )
        for item in catalog_value.relation_boundaries:
            boundaries.add(
                FederationBoundary(
                    QualifiedAddress(source_name, item.source_id),
                    item.relation,
                    item.value,
                    item.reason,
                )
            )
        for item in catalog_value.relation_migrations:
            migrations.add(
                FederationMigration(
                    QualifiedAddress(source_name, item.source_id),
                    item.relation,
                    item.value,
                    QualifiedAddress(source_name, item.target_id),
                )
            )
        for document in catalog_value.documents:
            if document.metadata is None:
                continue
            source = QualifiedAddress(source_name, document.metadata.document_id)
            for reference in document.metadata.federated_references:
                relation = reference.relation
                raw_target = reference.target
                try:
                    target = parse_qualified_address(raw_target, allow_anchor=False)
                except FederationError:
                    blocking_boundaries.add(
                        FederationBoundary(source, relation, raw_target, "malformed-address")
                    )
                    continue
                if target.document == source.document:
                    blocking_boundaries.add(
                        FederationBoundary(source, relation, raw_target, "self-reference")
                    )
                elif workspace.find(target.source) is None:
                    blocking_boundaries.add(
                        FederationBoundary(source, relation, raw_target, "unknown-source")
                    )
                elif target.document not in by_address:
                    blocking_boundaries.add(
                        FederationBoundary(source, relation, raw_target, "unknown-document")
                    )
                else:
                    candidate = FederatedEdge(
                        relation,
                        source,
                        target,
                        reference.expected_revision,
                    )
                    if candidate in edges:
                        blocking_boundaries.add(
                            FederationBoundary(
                                source,
                                relation,
                                raw_target,
                                "duplicate-semantic-edge",
                            )
                        )
                    else:
                        edges.add(candidate)
                        reference_edges.add(
                            FederatedReferenceEdge(
                                relation,
                                "authored",
                                source,
                                target,
                                "metadata",
                                pin=reference.expected_revision,
                            )
                        )
    graph_errors: list[str] = []
    revisions_by_pin: dict[tuple[str, str, str], set[int]] = {}
    for edge in edges:
        if edge.expected_revision is not None:
            revisions_by_pin.setdefault(
                (edge.relation, edge.source.document, edge.target.document), set()
            ).add(edge.expected_revision)
    for (relation, source, target), revisions in sorted(revisions_by_pin.items()):
        if len(revisions) > 1:
            graph_errors.append(
                f"federated {relation} pin conflict: {source} -> {target} "
                f"uses revisions {', '.join(str(item) for item in sorted(revisions))}"
            )
    for relation in ("depends_on", "derived_from", "supersedes"):
        members = _cyclic_members(edges, relation)
        if members:
            graph_errors.append(
                f"federated {relation} cycle: {', '.join(members)}"
            )
    if blocking_boundaries or graph_errors:
        boundary_details = [
            f"{item.source.document}: metadata.{item.relation} reference "
            f"{item.raw_target!r}: {item.reason}"
            for item in sorted(blocking_boundaries)
        ]
        raise FederationError("\n".join([*boundary_details, *sorted(graph_errors)]))
    return FederatedCatalog(
        documents=tuple(sorted(documents, key=lambda item: item.address.document)),
        edges=tuple(sorted(edges)),
        boundaries=tuple(sorted(boundaries)),
        migrations=tuple(sorted(migrations)),
        reference_edges=tuple(sorted(reference_edges)),
        reference_boundaries=tuple(sorted(reference_boundaries)),
    )


def context_selection(
    catalog: FederatedCatalog,
    target: QualifiedAddress,
    *,
    depth: int,
    include_related: bool,
    includes: tuple[QualifiedAddress, ...] = (),
) -> tuple[tuple[FederatedDocument, tuple[str, ...], tuple[str, ...]], ...]:
    """Select target-sized federated navigation and explicit sections."""

    catalog.find(target)
    included: dict[str, set[str]] = {target.document: {"target"}}
    explicit: dict[str, set[str]] = {
        target.document: {target.anchor} if target.anchor is not None else set()
    }
    queue = deque([(target.document, 0)])
    expanded: set[str] = set()
    allowed = {"derived_from", "depends_on", "validated_against"}
    if include_related:
        allowed.update({"related", "supersedes"})
    by_address = catalog.by_address
    while queue:
        source, distance = queue.popleft()
        if source in expanded or distance >= depth:
            continue
        expanded.add(source)
        for edge in catalog.edges:
            if edge.source.document != source or edge.relation not in allowed:
                continue
            included.setdefault(edge.target.document, set()).add(edge.relation)
            explicit.setdefault(edge.target.document, set())
            queue.append((edge.target.document, distance + 1))
    for address in includes:
        catalog.find(address)
        included.setdefault(address.document, set()).add("explicit")
        if address.anchor is not None:
            explicit.setdefault(address.document, set()).add(address.anchor)
    ordered = [target.document, *sorted(set(included) - {target.document})]
    return tuple(
        (
            by_address[address],
            tuple(sorted(included[address])),
            tuple(sorted(explicit.get(address, set()))),
        )
        for address in ordered
    )


def document_payload(
    document: FederatedDocument,
    relations: tuple[str, ...],
    anchors: tuple[str, ...],
) -> dict[str, object]:
    """Render one body-preserving task-sized document payload."""

    explicit = []
    for anchor in anchors:
        section = next(item for item in document.source.sections if item.anchor == anchor)
        explicit.append(
            {
                "anchor": anchor,
                "content": extract_section(document.content, section).rstrip(),
            }
        )
    omitted = [
        section.anchor
        for section in document.source.sections
        if section.level == 2 and section.anchor not in anchors
    ]
    return {
        "address": document.address.document,
        "visibility": document.visibility,
        "path": document.path,
        "revision": document.revision,
        "relations": list(relations),
        "navigation": extract_navigation(
            document.content,
            document.source.sections,
            document.navigation_extend_through,
        ).rstrip(),
        "explicit_sections": explicit,
        "omitted_h2": omitted,
    }
