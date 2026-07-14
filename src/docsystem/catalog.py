"""Provider-neutral Markdown discovery, metadata and dependency graphs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from docsystem.config import ProjectConfig, is_historical_snapshot
from docsystem.metadata import DocumentMetadata, parse_front_matter
from docsystem.sections import (
    MarkdownSection,
    navigation_issues,
    parse_sections_result,
)

INDEX_NAMES = frozenset({"readme.md", "index.md"})
INLINE_LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*]\(\s*(?:<([^>]+)>|([^) \t]+))")
REFERENCE_DEFINITION_PATTERN = re.compile(
    r"^\s{0,3}\[([^\]]+)]:\s*(?:<([^>]+)>|(\S+))", re.MULTILINE
)
REFERENCE_USE_PATTERN = re.compile(r"(?<!!)\[([^\]]+)]\[([^\]]*)]")


@dataclass(frozen=True)
class ValidationIssue:
    """A deterministic, human-readable catalog validation result."""

    path: PurePosixPath
    message: str
    severity: str = "error"
    affects_graph: bool = False
    target_id: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class CatalogMembership:
    """The explicit catalog classification of one Markdown source."""

    state: str
    path: PurePosixPath
    role: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class RelationBoundary:
    """A visible legacy relation that cannot become a graph edge."""

    source_id: str
    relation: str
    value: str
    reason: str


@dataclass(frozen=True)
class RelationMigration:
    """A deterministic legacy path to canonical ID mapping."""

    source_id: str
    relation: str
    value: str
    target_id: str


@dataclass(frozen=True)
class MarkdownDocument:
    """A Markdown source file and its parsed context-engine data."""

    role: str
    path: PurePosixPath
    links: tuple[PurePosixPath, ...]
    is_index: bool
    content: str
    metadata: DocumentMetadata | None
    sections: tuple[MarkdownSection, ...]
    section_issues: tuple[str, ...]
    metadata_issues: tuple[str, ...]
    graph_issues: tuple[str, ...]


@dataclass(frozen=True)
class MarkdownCatalog:
    """A deterministic snapshot of configured Markdown source files."""

    documents: tuple[MarkdownDocument, ...]
    memberships: tuple[CatalogMembership, ...] = ()
    legacy_edges: tuple[DependencyEdge, ...] = ()
    relation_boundaries: tuple[RelationBoundary, ...] = ()
    relation_migrations: tuple[RelationMigration, ...] = ()


@dataclass(frozen=True)
class DependencyEdge:
    """A typed, normalized edge between stable document IDs."""

    relation: str
    source_id: str
    target_id: str
    expected_revision: int | None = None


@dataclass(frozen=True)
class DependencyGraph:
    """Deterministically ordered forward and reverse dependency edges."""

    edges: tuple[DependencyEdge, ...]

    @cached_property
    def _outgoing_by_source(self) -> dict[str, tuple[DependencyEdge, ...]]:
        grouped: dict[str, list[DependencyEdge]] = {}
        for edge in self.edges:
            grouped.setdefault(edge.source_id, []).append(edge)
        return {source: tuple(edges) for source, edges in grouped.items()}

    @cached_property
    def _incoming_by_target(self) -> dict[str, tuple[DependencyEdge, ...]]:
        grouped: dict[str, list[DependencyEdge]] = {}
        for edge in self.edges:
            grouped.setdefault(edge.target_id, []).append(edge)
        return {target: tuple(edges) for target, edges in grouped.items()}

    def outgoing(self, document_id: str) -> tuple[DependencyEdge, ...]:
        return self._outgoing_by_source.get(document_id, ())

    def incoming(self, document_id: str) -> tuple[DependencyEdge, ...]:
        return self._incoming_by_target.get(document_id, ())


def _area_for(path: PurePosixPath, config: ProjectConfig) -> str | None:
    matches = [
        (len(area.parts), role)
        for role, area in config.areas.items()
        if path == area or area in path.parents
    ]
    if not matches:
        return None
    return max(matches)[1]


def _normalize_link(
    source: PurePosixPath, raw_target: str, documentation_root: Path
) -> PurePosixPath | None:
    target = urlsplit(raw_target)
    if target.scheme or target.netloc or not target.path:
        return None
    decoded = unquote(target.path).replace("\\", "/")
    candidate = (documentation_root / source.parent / decoded).resolve()
    try:
        relative = candidate.relative_to(documentation_root.resolve())
    except ValueError:
        return None
    if candidate.is_dir():
        for index in sorted(candidate.iterdir(), key=lambda item: item.name.lower()):
            if index.is_file() and index.name.lower() in INDEX_NAMES:
                relative = index.relative_to(documentation_root.resolve())
                break
    return PurePosixPath(relative.as_posix())


def _markdown_links(
    content: str, relative: PurePosixPath, root: Path
) -> tuple[PurePosixPath, ...]:
    definitions = {
        match.group(1).casefold(): match.group(2) or match.group(3)
        for match in REFERENCE_DEFINITION_PATTERN.finditer(content)
    }
    inline_links = (
        match.group(1) or match.group(2)
        for match in INLINE_LINK_PATTERN.finditer(content)
    )
    reference_links = (
        definitions[label.casefold()]
        for match in REFERENCE_USE_PATTERN.finditer(content)
        if (label := match.group(2) or match.group(1)).casefold() in definitions
    )
    links = {
        normalized
        for raw in (*inline_links, *reference_links)
        if (normalized := _normalize_link(relative, raw, root)) is not None
        and normalized.suffix.lower() == ".md"
    }
    return tuple(sorted(links, key=PurePosixPath.as_posix))


def _excluded_paths(
    root: Path, patterns: tuple[str, ...]
) -> dict[PurePosixPath, str]:
    excluded: dict[PurePosixPath, str] = {}
    for pattern in patterns:
        for path in sorted(root.glob(pattern), key=lambda item: item.as_posix()):
            if not path.is_file() or path.suffix.lower() != ".md":
                continue
            relative = PurePosixPath(path.relative_to(root).as_posix())
            excluded.setdefault(relative, pattern)
    return excluded


def included_source_paths(config: ProjectConfig) -> tuple[PurePosixPath, ...]:
    """List included Markdown source paths without parsing any content.

    Applies the same exclusion and area-mapping rules as `build_catalog`, so
    the result is exactly the catalog's document set; it exists so projection
    freshness checks can enumerate sources without paying for Markdown,
    metadata or link parsing.
    """

    root = config.documentation_root
    if not root.is_dir():
        return ()
    excluded = _excluded_paths(root, config.catalog_exclusions)
    included: list[PurePosixPath] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if relative in excluded or _area_for(relative, config) is None:
            continue
        included.append(relative)
    return tuple(included)


def build_catalog(config: ProjectConfig) -> MarkdownCatalog:
    """Classify every Markdown file and parse included documents."""

    root = config.documentation_root
    if not root.is_dir():
        return MarkdownCatalog(documents=(), memberships=())

    documents: list[MarkdownDocument] = []
    memberships: list[CatalogMembership] = []
    excluded_paths = _excluded_paths(root, config.catalog_exclusions)
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        relative = PurePosixPath(path.relative_to(root).as_posix())
        exclusion = excluded_paths.get(relative)
        if exclusion is not None:
            memberships.append(
                CatalogMembership("excluded", relative, reason=exclusion)
            )
            continue
        role = _area_for(relative, config)
        if role is None:
            memberships.append(
                CatalogMembership(
                    "unmapped", relative, reason="no configured area"
                )
            )
            continue
        memberships.append(CatalogMembership("included", relative, role=role))
        content = path.read_text(encoding="utf-8")
        front_matter = parse_front_matter(content, frozenset(config.identifiers.values()))
        section_result = parse_sections_result(content)
        documents.append(
            MarkdownDocument(
                role=role,
                path=relative,
                links=_markdown_links(content, relative, root),
                is_index=path.name.lower() in INDEX_NAMES,
                content=content,
                metadata=front_matter.metadata,
                sections=section_result.sections,
                section_issues=section_result.issues,
                metadata_issues=front_matter.issues,
                graph_issues=front_matter.graph_issues,
            )
        )
    by_path = {
        (root / document.path).resolve(): document
        for document in documents
        if document.metadata is not None
    }
    legacy_edges: list[DependencyEdge] = []
    boundaries: list[RelationBoundary] = []
    migrations: list[RelationMigration] = []
    for document in documents:
        if document.metadata is None:
            continue
        for relation, value in document.metadata.legacy_references:
            parsed = urlsplit(value)
            external = bool(parsed.scheme or parsed.netloc)
            candidate = (
                root / document.path.parent / unquote(parsed.path)
            ).resolve()
            target = by_path.get(candidate) if not external else None
            if target is not None and target.metadata is not None:
                if target.metadata.document_id == document.metadata.document_id:
                    boundaries.append(
                        RelationBoundary(
                            document.metadata.document_id,
                            relation,
                            value,
                            "self reference",
                        )
                    )
                    continue
                if config.legacy_relation_mode == "resolve-with-warning":
                    legacy_edges.append(
                        DependencyEdge(
                            relation,
                            document.metadata.document_id,
                            target.metadata.document_id,
                        )
                    )
                # `relation_migrations` stays mode-independent: it is the
                # deterministic inventory `migration-report`/`migrate`/
                # `readiness` rely on, even before a project opts into
                # `resolve-with-warning`.
                migrations.append(
                    RelationMigration(
                        document.metadata.document_id,
                        relation,
                        value,
                        target.metadata.document_id,
                    )
                )
            else:
                reason = "external URL" if external else "resource/outside catalog"
                boundaries.append(
                    RelationBoundary(
                        document.metadata.document_id, relation, value, reason
                    )
                )
    return MarkdownCatalog(
        documents=tuple(documents),
        memberships=tuple(memberships),
        legacy_edges=tuple(
            sorted(
                set(legacy_edges),
                key=lambda edge: (edge.relation, edge.source_id, edge.target_id),
            )
        ),
        relation_boundaries=tuple(
            sorted(
                boundaries,
                key=lambda item: (item.source_id, item.relation, item.value),
            )
        ),
        relation_migrations=tuple(
            sorted(
                migrations,
                key=lambda item: (item.source_id, item.relation, item.value),
            )
        ),
    )


def validate_membership(catalog: MarkdownCatalog) -> tuple[ValidationIssue, ...]:
    """Reject Markdown that is neither included nor explicitly excluded."""

    return tuple(
        ValidationIssue(
            membership.path,
            "Markdown is not mapped to a configured area or catalog exclusion",
            affects_graph=True,
        )
        for membership in catalog.memberships
        if membership.state == "unmapped"
    )


def document_section_issues(
    document: MarkdownDocument, config: ProjectConfig
) -> tuple[str, ...]:
    """Return parser and navigation-policy diagnostics for one document."""

    return (
        *document.section_issues,
        *navigation_issues(document.sections, config.navigation_extend_through),
    )


def validate_sections(
    catalog: MarkdownCatalog, config: ProjectConfig
) -> tuple[ValidationIssue, ...]:
    """Validate stable section addressing and navigation anchors."""

    return tuple(
        ValidationIssue(document.path, message)
        for document in catalog.documents
        for message in document_section_issues(document, config)
    )


def validate_metadata(
    catalog: MarkdownCatalog, config: ProjectConfig | None = None
) -> tuple[ValidationIssue, ...]:
    """Validate stable IDs, revisions and semantic references."""

    issues = [
        ValidationIssue(
            document.path,
            message,
            affects_graph=message in document.graph_issues,
        )
        for document in catalog.documents
        for message in document.metadata_issues
    ]
    documents_by_id: dict[str, list[MarkdownDocument]] = {}
    for document in catalog.documents:
        if document.metadata is not None:
            documents_by_id.setdefault(document.metadata.document_id, []).append(document)

    for document_id, documents in documents_by_id.items():
        if len(documents) > 1:
            paths = ", ".join(item.path.as_posix() for item in documents)
            issues.extend(
                ValidationIssue(
                    document.path,
                    f"duplicate document ID {document_id}; also used by: {paths}",
                    affects_graph=True,
                )
                for document in documents
            )

    unique_by_id = {
        document_id: documents[0]
        for document_id, documents in documents_by_id.items()
        if len(documents) == 1
    }
    for document in catalog.documents:
        metadata = document.metadata
        if metadata is None:
            continue
        for reference in metadata.federated_references:
            issues.append(
                ValidationIssue(
                    document.path,
                    f"metadata.{reference.relation} federated reference "
                    f"{reference.target!r} requires a workspace federation query",
                    severity="warning",
                    affects_graph=True,
                    target_id=reference.target,
                    category="federation-boundary",
                )
            )
        for reference in metadata.references:
            if reference.target_id == metadata.document_id:
                issues.append(
                    ValidationIssue(
                        document.path,
                        f"metadata.{reference.relation} cannot reference its own ID",
                        affects_graph=True,
                    )
                )
                continue
            target = unique_by_id.get(reference.target_id)
            if target is None:
                issues.append(
                    ValidationIssue(
                        document.path,
                        f"metadata.{reference.relation} references unknown ID "
                        f"{reference.target_id}",
                        affects_graph=True,
                    )
                )
                continue
            if (
                reference.expected_revision is not None
                and target.metadata is not None
                and reference.expected_revision != target.metadata.revision
            ):
                if config is not None and is_historical_snapshot(
                    config, metadata.document_type, metadata.status
                ):
                    continue
                issues.append(
                    ValidationIssue(
                        document.path,
                        f"metadata.{reference.relation} pin "
                        f"{reference.target_id}@{reference.expected_revision} is stale; "
                        f"current revision is {target.metadata.revision}",
                        severity="warning",
                        target_id=reference.target_id,
                    )
                )

    return tuple(
        sorted(
            issues,
            key=lambda issue: (issue.path.as_posix(), issue.severity, issue.message),
        )
    )


def validate_adoption(
    catalog: MarkdownCatalog, config: ProjectConfig
) -> tuple[ValidationIssue, ...]:
    """Expose every opt-in legacy mapping and unresolved boundary.

    Boundaries (external URLs and resources/paths outside the catalog) are
    never document relations, so they remain non-blocking warnings in both
    `strict` and `resolve-with-warning` mode. A legacy path that resolves to
    an in-catalog document is a real document relation: in
    `resolve-with-warning` mode it is a migratable warning and a graph edge;
    in `strict` mode it remains a blocking error until it is migrated to a
    stable ID (see `docsystem migrate`) or the project opts into
    `resolve-with-warning`.
    """

    paths = {
        document.metadata.document_id: document.path
        for document in catalog.documents
        if document.metadata is not None
    }
    issues: list[ValidationIssue] = []
    for item in catalog.relation_migrations:
        if config.legacy_relation_mode == "strict":
            issues.append(
                ValidationIssue(
                    paths[item.source_id],
                    f"metadata.{item.relation} entry {item.value!r} must use a "
                    "configured stable ID (it resolves to "
                    f"{item.target_id}; migrate it with `docsystem migrate` or "
                    "enable relations.legacy_paths = resolve-with-warning)",
                    severity="error",
                    affects_graph=True,
                )
            )
        else:
            issues.append(
                ValidationIssue(
                    paths[item.source_id],
                    f"legacy metadata.{item.relation} value {item.value!r} "
                    f"resolves to {item.target_id}",
                    severity="warning",
                    category="adoption-resolved",
                )
            )
    for item in catalog.relation_boundaries:
        self_reference = item.reason == "self reference"
        issues.append(
            ValidationIssue(
                paths[item.source_id],
                f"legacy metadata.{item.relation} value {item.value!r}: "
                f"{item.reason}",
                severity="error" if self_reference else "warning",
                affects_graph=self_reference,
                category=None if self_reference else "adoption-boundary",
            )
        )
    return tuple(
        sorted(
            issues,
            key=lambda issue: (issue.path.as_posix(), issue.severity, issue.message),
        )
    )


def build_dependency_graph(catalog: MarkdownCatalog) -> DependencyGraph:
    """Build a graph from valid references whose targets are unambiguous."""

    counts: dict[str, int] = {}
    for document in catalog.documents:
        if document.metadata is not None:
            document_id = document.metadata.document_id
            counts[document_id] = counts.get(document_id, 0) + 1
    known = {document_id for document_id, count in counts.items() if count == 1}

    edges = {
        DependencyEdge(
            relation=reference.relation,
            source_id=document.metadata.document_id,
            target_id=reference.target_id,
            expected_revision=reference.expected_revision,
        )
        for document in catalog.documents
        if document.metadata is not None
        and document.metadata.document_id in known
        for reference in document.metadata.references
        if reference.target_id in known
        and reference.target_id != document.metadata.document_id
    }
    edges.update(
        edge
        for edge in catalog.legacy_edges
        if edge.source_id in known
        and edge.target_id in known
        and edge.source_id != edge.target_id
    )
    return DependencyGraph(
        tuple(
            sorted(
                edges,
                key=lambda edge: (
                    edge.relation,
                    edge.source_id,
                    edge.target_id,
                    edge.expected_revision or 0,
                ),
            )
        )
    )


def find_document(catalog: MarkdownCatalog, document_id: str) -> MarkdownDocument:
    """Resolve one unique document by stable ID."""

    matches = [
        document
        for document in catalog.documents
        if document.metadata is not None
        and document.metadata.document_id == document_id
    ]
    if not matches:
        raise ValueError(f"document ID not found: {document_id}")
    if len(matches) > 1:
        raise ValueError(f"document ID is not unique: {document_id}")
    return matches[0]


def validate_reachability(
    catalog: MarkdownCatalog, config: ProjectConfig
) -> tuple[ValidationIssue, ...]:
    """Require every document to be listed by its nearest hierarchical index."""

    indexes_by_directory: dict[PurePosixPath, list[MarkdownDocument]] = {}
    for document in catalog.documents:
        if document.is_index:
            indexes_by_directory.setdefault(document.path.parent, []).append(document)

    issues: list[ValidationIssue] = []
    for directory, indexes in indexes_by_directory.items():
        if len(indexes) > 1:
            names = ", ".join(index.path.name for index in indexes)
            issues.append(
                ValidationIssue(directory, f"multiple navigation indexes found: {names}")
            )

    for document in catalog.documents:
        area_root = config.areas[document.role]
        if document.is_index and document.path.parent == area_root:
            continue
        directory = document.path.parent if not document.is_index else document.path.parent.parent
        nearest: MarkdownDocument | None = None
        while directory == area_root or area_root in directory.parents:
            indexes = indexes_by_directory.get(directory, [])
            if len(indexes) == 1:
                nearest = indexes[0]
                break
            if directory == area_root:
                break
            directory = directory.parent

        if nearest is None:
            issues.append(
                ValidationIssue(
                    document.path,
                    f"no README.md or index.md found between document and area '{document.role}'",
                )
            )
        elif document.path not in nearest.links:
            issues.append(
                ValidationIssue(
                    document.path,
                    f"not linked from nearest index {nearest.path.as_posix()}",
                )
            )

    return tuple(sorted(issues, key=lambda issue: (issue.path.as_posix(), issue.message)))


def validate_catalog(
    catalog: MarkdownCatalog, config: ProjectConfig
) -> tuple[ValidationIssue, ...]:
    """Validate metadata and hierarchical human navigation."""

    return tuple(
        sorted(
            (
                *validate_membership(catalog),
                *validate_metadata(catalog, config),
                *validate_adoption(catalog, config),
                *validate_sections(catalog, config),
                *validate_reachability(catalog, config),
            ),
            key=lambda issue: (issue.path.as_posix(), issue.severity, issue.message),
        )
    )
