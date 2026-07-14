"""Deterministic, read-only graph-health inventory and advisory signals."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from docsystem.catalog import MarkdownCatalog
from docsystem.config import ProjectConfig, is_historical_snapshot
from docsystem.graph import build_reference_graph
from docsystem.projection import LoadedProjection


@dataclass(frozen=True)
class HealthDocument:
    document_id: str
    revision: int
    document_type: str | None
    status: str | None
    section_count: int


@dataclass(frozen=True)
class HealthEdge:
    source_id: str
    target_id: str
    relation: str
    authority: str
    expected_revision: int | None = None


@dataclass(frozen=True)
class HealthBoundary:
    source_id: str
    category: str


@dataclass(frozen=True)
class HealthFacts:
    documents: tuple[HealthDocument, ...]
    edges: tuple[HealthEdge, ...]
    boundaries: tuple[HealthBoundary, ...]


@dataclass(frozen=True)
class HealthSignal:
    code: str
    documents: tuple[str, ...]
    value: int
    threshold: int | None
    detail: str


@dataclass(frozen=True)
class GraphHealthReport:
    document_count: int
    section_count: int
    edge_count: int
    edges_by_authority: tuple[tuple[str, int], ...]
    edges_by_relation: tuple[tuple[str, int], ...]
    boundary_count: int
    boundaries_by_category: tuple[tuple[str, int], ...]
    weak_component_sizes: tuple[int, ...]
    orphan_documents: tuple[str, ...]
    stale_pin_count: int
    historical_pin_count: int
    missing_metadata: tuple[tuple[str, int], ...]
    signals: tuple[HealthSignal, ...]


def _semantic_boundary_category(reason: str) -> str:
    return "external" if reason == "external URL" else "resource"


def facts_from_catalog(
    catalog: MarkdownCatalog, config: ProjectConfig
) -> HealthFacts:
    graph = build_reference_graph(catalog, config)
    documents = tuple(
        sorted(
            (
                HealthDocument(
                    document.metadata.document_id,
                    document.metadata.revision,
                    document.metadata.document_type,
                    document.metadata.status,
                    len(document.sections),
                )
                for document in catalog.documents
                if document.metadata is not None
            ),
            key=lambda item: item.document_id,
        )
    )
    edges = tuple(
        HealthEdge(
            edge.source.document_id,
            edge.target.document_id,
            edge.relation,
            edge.authority,
            edge.pin,
        )
        for edge in graph.edges
    )
    boundaries = [
        HealthBoundary(boundary.source.document_id, boundary.category)
        for boundary in graph.boundaries
    ]
    boundaries.extend(
        HealthBoundary(item.source_id, _semantic_boundary_category(item.reason))
        for item in catalog.relation_boundaries
        if item.reason != "self reference"
    )
    return HealthFacts(
        documents,
        edges,
        tuple(sorted(boundaries, key=lambda item: (item.source_id, item.category))),
    )


def facts_from_projection(loaded: LoadedProjection) -> HealthFacts:
    documents = tuple(
        HealthDocument(
            document_id,
            int(shard["revision"]),
            shard.get("type"),
            shard.get("status"),
            len(shard.get("sections", {})),
        )
        for document_id, shard in sorted(loaded.documents.items())
    )
    edges: list[HealthEdge] = []
    boundaries: list[HealthBoundary] = []
    for document_id, shard in sorted(loaded.documents.items()):
        edges.extend(
            HealthEdge(
                document_id,
                str(record["target"]),
                str(record["relation"]),
                "authored",
                record.get("expected_revision"),
            )
            for record in shard.get("dependencies", ())
        )
        edges.extend(
            HealthEdge(document_id, document_id, "contains", "generated")
            for _ in shard.get("sections", {})
        )
        boundaries.extend(
            HealthBoundary(
                document_id,
                _semantic_boundary_category(str(record["reason"])),
            )
            for record in shard.get("boundaries", ())
            if record.get("reason") != "self reference"
        )
        reference_shard = loaded.references.get(document_id, {})
        edges.extend(
            HealthEdge(
                document_id,
                str(record["target"]),
                str(record["relation"]),
                str(record["authority"]),
            )
            for record in reference_shard.get("forward", ())
        )
        boundaries.extend(
            HealthBoundary(document_id, str(record["category"]))
            for record in reference_shard.get("boundaries", ())
        )
    return HealthFacts(
        documents,
        tuple(edges),
        tuple(sorted(boundaries, key=lambda item: (item.source_id, item.category))),
    )


def _weak_components(
    document_ids: tuple[str, ...], edges: tuple[HealthEdge, ...]
) -> tuple[tuple[str, ...], ...]:
    adjacency = {document_id: set() for document_id in document_ids}
    for edge in edges:
        if edge.authority == "generated" or edge.source_id == edge.target_id:
            continue
        if edge.source_id not in adjacency or edge.target_id not in adjacency:
            continue
        adjacency[edge.source_id].add(edge.target_id)
        adjacency[edge.target_id].add(edge.source_id)
    remaining = set(document_ids)
    components: list[tuple[str, ...]] = []
    while remaining:
        start = min(remaining)
        frontier = [start]
        members: set[str] = set()
        while frontier:
            current = frontier.pop()
            if current in members:
                continue
            members.add(current)
            frontier.extend(sorted(adjacency[current] - members, reverse=True))
        remaining -= members
        components.append(tuple(sorted(members)))
    return tuple(sorted(components, key=lambda item: (-len(item), item)))


def evaluate_graph_health(
    facts: HealthFacts, config: ProjectConfig
) -> GraphHealthReport:
    policy = config.graph_health_policy
    documents = {item.document_id: item for item in facts.documents}
    non_generated = tuple(
        edge for edge in facts.edges if edge.authority != "generated"
    )
    incoming: dict[str, set[str]] = {item: set() for item in documents}
    outgoing: dict[str, set[str]] = {item: set() for item in documents}
    for edge in non_generated:
        if edge.source_id == edge.target_id:
            continue
        if edge.source_id in outgoing and edge.target_id in incoming:
            outgoing[edge.source_id].add(edge.target_id)
            incoming[edge.target_id].add(edge.source_id)

    boundary_counts = Counter(item.source_id for item in facts.boundaries)
    stale_counts: Counter[str] = Counter()
    stale_total = 0
    historical_total = 0
    for edge in facts.edges:
        if edge.expected_revision is None:
            continue
        target = documents.get(edge.target_id)
        source = documents.get(edge.source_id)
        if target is None or source is None or target.revision == edge.expected_revision:
            continue
        if is_historical_snapshot(config, source.document_type, source.status):
            historical_total += 1
        else:
            stale_total += 1
            stale_counts[source.document_id] += 1

    components = _weak_components(tuple(sorted(documents)), facts.edges)
    orphans = tuple(
        sorted(
            document_id
            for document_id in documents
            if not incoming[document_id] and not outgoing[document_id]
        )
    )
    missing_counts: Counter[str] = Counter()
    signals: list[HealthSignal] = []
    for document in facts.documents:
        missing = tuple(
            field
            for field in policy.required_metadata
            if (
                (field == "type" and document.document_type is None)
                or (field == "status" and document.status is None)
            )
        )
        for field in missing:
            missing_counts[field] += 1
        if missing:
            signals.append(
                HealthSignal(
                    "missing-metadata",
                    (document.document_id,),
                    len(missing),
                    None,
                    "missing " + ", ".join(missing),
                )
            )

    for document_id in sorted(documents):
        checks = (
            (
                "high-in-degree",
                len(incoming[document_id]),
                policy.hub_in_degree,
                "distinct incoming document neighbors",
            ),
            (
                "high-out-degree",
                len(outgoing[document_id]),
                policy.hub_out_degree,
                "distinct outgoing document neighbors",
            ),
            (
                "boundary-concentration",
                boundary_counts[document_id],
                policy.boundary_count,
                "explicit unresolved graph boundaries",
            ),
            (
                "stale-pin-concentration",
                stale_counts[document_id],
                policy.stale_pin_count,
                "stale freshness pins owned by the document",
            ),
        )
        for code, value, threshold, detail in checks:
            if threshold is not None and value >= threshold:
                signals.append(
                    HealthSignal(code, (document_id,), value, threshold, detail)
                )
        if policy.report_orphans and document_id in orphans:
            signals.append(
                HealthSignal(
                    "orphan-document",
                    (document_id,),
                    1,
                    None,
                    "no authored or observed document edge",
                )
            )

    if (
        policy.max_weak_components is not None
        and len(components) > policy.max_weak_components
    ):
        outside_largest = tuple(
            sorted(member for component in components[1:] for member in component)
        )
        signals.append(
            HealthSignal(
                "weak-components",
                outside_largest,
                len(components),
                policy.max_weak_components,
                "component sizes: " + ", ".join(str(len(item)) for item in components),
            )
        )
    dead_references = Counter(
        boundary.source_id
        for boundary in facts.boundaries
        if boundary.category == "missing-anchor"
    )
    for document_id, count in sorted(dead_references.items()):
        signals.append(
            HealthSignal(
                "dead-reference",
                (document_id,),
                count,
                None,
                "Markdown links target missing section anchors",
            )
        )

    signals.sort(
        key=lambda item: (item.code, item.documents, item.value, item.detail)
    )
    return GraphHealthReport(
        document_count=len(facts.documents),
        section_count=sum(item.section_count for item in facts.documents),
        edge_count=len(facts.edges),
        edges_by_authority=tuple(
            sorted(Counter(edge.authority for edge in facts.edges).items())
        ),
        edges_by_relation=tuple(
            sorted(Counter(edge.relation for edge in facts.edges).items())
        ),
        boundary_count=len(facts.boundaries),
        boundaries_by_category=tuple(
            sorted(Counter(item.category for item in facts.boundaries).items())
        ),
        weak_component_sizes=tuple(len(item) for item in components),
        orphan_documents=orphans,
        stale_pin_count=stale_total,
        historical_pin_count=historical_total,
        missing_metadata=tuple(sorted(missing_counts.items())),
        signals=tuple(signals),
    )
