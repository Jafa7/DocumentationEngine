"""Internal retrieval application services shared by command adapters.

This module deliberately exposes no supported public Python API.  It adapts
verified projections and direct Markdown catalogs to one shared view model,
performs deterministic graph selection, and builds packet delivery plans.
Adapters remain responsible for argument parsing, presentation and exit codes.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import PurePosixPath

from docsystem.catalog import MarkdownCatalog, build_catalog, build_dependency_graph
from docsystem.config import ContextView, ProjectConfig, is_historical_snapshot
from docsystem.projection import LoadedProjection, load_verified_projection
from docsystem.sections import MarkdownSection


@dataclass(frozen=True)
class RetrievalDiagnostic:
    """One adapter-neutral diagnostic produced while loading retrieval state."""

    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class EdgeView:
    """One dependency edge as served to a retrieval consumer."""

    relation: str
    peer_id: str
    expected_revision: int | None


@dataclass(frozen=True)
class DocumentView:
    """Document data shared by direct-Markdown and projection-backed reads."""

    document_id: str
    path: PurePosixPath
    content: str
    source_sha256: str
    sections: tuple[MarkdownSection, ...]
    revision: int
    document_type: str | None
    status: str | None
    outgoing: tuple[EdgeView, ...]
    migrations: tuple[tuple[str, str, str], ...]
    boundaries: tuple[tuple[str, str, str], ...]
    related_values: tuple[str, ...]


Views = dict[str, DocumentView]
Incoming = dict[str, tuple[EdgeView, ...]]


@dataclass(frozen=True)
class RetrievalState:
    """Loaded retrieval state plus diagnostics for the presentation adapter."""

    views: Views
    incoming: Incoming
    catalog: MarkdownCatalog | None
    serving_path: str
    diagnostics: tuple[RetrievalDiagnostic, ...] = ()


@dataclass(frozen=True, order=True)
class ContextInclusionReason:
    """One exact reason a document entered a context selection."""

    via_id: str
    direction: str
    relation: str


ContextReasons = dict[str, set[ContextInclusionReason]]


@dataclass(frozen=True)
class ContextViewOmission:
    """One authored edge a purpose view deliberately did not traverse."""

    source_id: str
    direction: str
    relation: str
    peer_id: str
    reason: str


@dataclass(frozen=True)
class DocumentPacketPlan:
    """Per-document delivery plan for assumed-known and generation deltas."""

    omit_navigation: bool = False
    content_omitted: dict[str, object] | None = None
    coverage_state: str = "normal"
    declared_revision: int | None = None
    generation_short: str | None = None
    changed_sections: tuple[str, ...] = ()
    removed_sections: tuple[str, ...] = ()
    metadata_changes: tuple[tuple[str, object, object], ...] = ()
    source_changed_outside_sections: bool = False
    changed_document: bool = False


def views_from_catalog(catalog: MarkdownCatalog) -> tuple[Views, Incoming]:
    """Adapt an already-built Markdown catalog to retrieval views."""

    graph = build_dependency_graph(catalog)
    migrations: dict[str, list[tuple[str, str, str]]] = {}
    for item in catalog.relation_migrations:
        migrations.setdefault(item.source_id, []).append(
            (item.relation, item.value, item.target_id)
        )
    boundaries: dict[str, list[tuple[str, str, str]]] = {}
    for item in catalog.relation_boundaries:
        boundaries.setdefault(item.source_id, []).append(
            (item.relation, item.value, item.reason)
        )
    views: Views = {}
    incoming: Incoming = {}
    for document in catalog.documents:
        metadata = document.metadata
        if metadata is None:
            continue
        document_id = metadata.document_id
        boundaries.setdefault(document_id, []).extend(
            (
                reference.relation,
                reference.target,
                "requires workspace federation",
            )
            for reference in metadata.federated_references
        )
        related_values = [
            value
            for relation, value in metadata.legacy_references
            if relation == "related"
        ]
        related_values.extend(
            reference.target_id
            for reference in metadata.references
            if reference.relation == "related"
        )
        related_values.extend(
            reference.target
            for reference in metadata.federated_references
            if reference.relation == "related"
        )
        views[document_id] = DocumentView(
            document_id=document_id,
            path=document.path,
            content=document.content,
            source_sha256=document.source_sha256,
            sections=document.sections,
            revision=metadata.revision,
            document_type=metadata.document_type,
            status=metadata.status,
            outgoing=tuple(
                EdgeView(edge.relation, edge.target_id, edge.expected_revision)
                for edge in graph.outgoing(document_id)
            ),
            migrations=tuple(migrations.get(document_id, ())),
            boundaries=tuple(boundaries.get(document_id, ())),
            related_values=tuple(related_values),
        )
        incoming[document_id] = tuple(
            EdgeView(edge.relation, edge.source_id, edge.expected_revision)
            for edge in graph.incoming(document_id)
        )
    return views, incoming


def _views_from_projection(loaded: LoadedProjection) -> tuple[Views, Incoming]:
    views: Views = {}
    incoming: Incoming = {}
    for document_id, shard in loaded.documents.items():
        sections = tuple(
            MarkdownSection(
                title=str(record["title"]),
                anchor=anchor,
                level=int(record["level"]),
                start_line=int(record["start_line"]),
                end_line=int(record["end_line"]),
                anchor_kind=str(record.get("anchor_kind", "generated")),
            )
            for anchor, record in sorted(
                shard["sections"].items(),
                key=lambda item: item[1]["start_line"],
            )
        )
        path = str(shard["path"])
        views[document_id] = DocumentView(
            document_id=document_id,
            path=PurePosixPath(path),
            content=loaded.contents[path],
            source_sha256=str(shard["source_sha256"]),
            sections=sections,
            revision=int(shard["revision"]),
            document_type=shard.get("type"),
            status=shard.get("status"),
            outgoing=tuple(
                EdgeView(
                    record["relation"],
                    record["target"],
                    record.get("expected_revision"),
                )
                for record in shard.get("dependencies", ())
            ),
            migrations=tuple(
                (record["relation"], record["value"], record["target"])
                for record in shard.get("migrations", ())
            ),
            boundaries=tuple(
                (record["relation"], record["value"], record["reason"])
                for record in shard.get("boundaries", ())
            ),
            related_values=tuple(str(value) for value in shard.get("related_values", ())),
        )
        incoming[document_id] = tuple(
            EdgeView(
                record["relation"],
                record["source"],
                record.get("expected_revision"),
            )
            for record in loaded.reverse.get(document_id, ())
        )
    return views, incoming


def load_retrieval_state(config: ProjectConfig) -> RetrievalState:
    """Load one verified retrieval state without writing to terminal streams."""

    loaded, reason = load_verified_projection(config)
    if loaded is not None:
        views, incoming = _views_from_projection(loaded)
        return RetrievalState(views, incoming, None, "projection")
    catalog = build_catalog(config)
    views, incoming = views_from_catalog(catalog)
    return RetrievalState(
        views,
        incoming,
        catalog,
        "direct",
        (
            RetrievalDiagnostic(
                "warning",
                "projection-fallback",
                f"{reason}; using direct Markdown",
            ),
        ),
    )


def parse_selection(raw: str) -> tuple[str, str | None]:
    """Parse an ``ID`` or ``ID#anchor`` explicit context selection."""

    document_id, separator, anchor = raw.partition("#")
    if not document_id or (separator and not anchor):
        raise ValueError(f"invalid include selection: {raw!r}")
    return document_id, anchor if separator else None


def context_selection(
    views: Views,
    document_id: str,
    *,
    depth: int,
    include_related: bool,
) -> tuple[dict[str, set[str]], ContextReasons]:
    """Select forward semantic context with deterministic inclusion evidence."""

    included: dict[str, set[str]] = {document_id: {"target"}}
    reasons: ContextReasons = {
        document_id: {ContextInclusionReason(document_id, "self", "target")}
    }
    queue = deque([(document_id, 0)])
    expanded: set[str] = set()
    allowed = {"derived_from", "depends_on", "validated_against"}
    if include_related:
        allowed.update({"related", "supersedes"})
    while queue:
        source_id, current_depth = queue.popleft()
        if source_id in expanded or current_depth >= depth:
            continue
        expanded.add(source_id)
        for edge in views[source_id].outgoing:
            if edge.relation not in allowed:
                continue
            included.setdefault(edge.peer_id, set()).add(edge.relation)
            reasons.setdefault(edge.peer_id, set()).add(
                ContextInclusionReason(source_id, "forward", edge.relation)
            )
            queue.append((edge.peer_id, current_depth + 1))
    return included, reasons


def purpose_context_selection(
    views: Views,
    incoming: Incoming,
    document_id: str,
    purpose_view: ContextView,
) -> tuple[dict[str, set[str]], ContextReasons, tuple[ContextViewOmission, ...]]:
    """Traverse one authored purpose view and retain filtered/stopped edges."""

    included: dict[str, set[str]] = {document_id: {"target"}}
    reasons: ContextReasons = {
        document_id: {ContextInclusionReason(document_id, "self", "target")}
    }
    queue = deque([(document_id, 0)])
    expanded: set[str] = set()
    omissions: set[ContextViewOmission] = set()
    allowed = set(purpose_view.relations)
    while queue:
        source_id, current_depth = queue.popleft()
        if source_id in expanded:
            continue
        expanded.add(source_id)
        candidates: list[tuple[str, EdgeView]] = []
        if purpose_view.direction in {"forward", "both"}:
            candidates.extend(("forward", edge) for edge in views[source_id].outgoing)
        if purpose_view.direction in {"reverse", "both"}:
            candidates.extend(("reverse", edge) for edge in incoming.get(source_id, ()))
        for direction, edge in sorted(
            candidates,
            key=lambda item: (
                item[0],
                item[1].relation,
                item[1].peer_id,
                item[1].expected_revision or 0,
            ),
        ):
            if edge.relation not in allowed:
                omissions.add(
                    ContextViewOmission(
                        source_id,
                        direction,
                        edge.relation,
                        edge.peer_id,
                        "relation-filter",
                    )
                )
                continue
            if current_depth >= purpose_view.depth:
                if edge.peer_id not in included:
                    omissions.add(
                        ContextViewOmission(
                            source_id,
                            direction,
                            edge.relation,
                            edge.peer_id,
                            "depth-limit",
                        )
                    )
                continue
            if edge.peer_id == document_id:
                continue
            reason = edge.relation if direction == "forward" else f"reverse:{edge.relation}"
            included.setdefault(edge.peer_id, set()).add(reason)
            reasons.setdefault(edge.peer_id, set()).add(
                ContextInclusionReason(source_id, direction, edge.relation)
            )
            queue.append((edge.peer_id, current_depth + 1))
    return (
        included,
        reasons,
        tuple(
            sorted(
                omissions,
                key=lambda item: (
                    item.source_id,
                    item.direction,
                    item.relation,
                    item.peer_id,
                    item.reason,
                ),
            )
        ),
    )


def ordered_selection(included: dict[str, set[str]], document_id: str) -> list[str]:
    """Put the target first and all other selected IDs in stable order."""

    return [document_id, *sorted(item for item in included if item != document_id)]


def freshness_rows(
    config: ProjectConfig,
    views: Views,
    ordered: list[str],
) -> list[dict[str, object]]:
    """Return deterministic stale and historical snapshot pin observations."""

    rows: list[dict[str, object]] = []
    for selected_id in ordered:
        view = views[selected_id]
        for edge in view.outgoing:
            if edge.expected_revision is None:
                continue
            dependency = views.get(edge.peer_id)
            if dependency is None or dependency.revision == edge.expected_revision:
                continue
            rows.append(
                {
                    "source_id": selected_id,
                    "target_id": edge.peer_id,
                    "pinned_revision": edge.expected_revision,
                    "current_revision": dependency.revision,
                    "classification": (
                        "historical snapshot"
                        if is_historical_snapshot(
                            config, view.document_type, view.status
                        )
                        else "stale"
                    ),
                }
            )
    return rows


def _section_sha(view: DocumentView, section: MarkdownSection) -> str:
    lines = view.content.splitlines()
    slice_text = "\n".join(lines[section.start_line - 1 : section.end_line])
    return hashlib.sha256(slice_text.encode()).hexdigest()


def _changed_section_anchors(
    view: DocumentView, previous_sections: dict[str, object]
) -> tuple[str, ...]:
    return tuple(
        section.anchor
        for section in view.sections
        if not isinstance(previous_sections.get(section.anchor), dict)
        or previous_sections[section.anchor].get("sha256") != _section_sha(view, section)
    )


def _removed_section_anchors(
    view: DocumentView, previous_sections: dict[str, object]
) -> tuple[str, ...]:
    current = {section.anchor for section in view.sections}

    def previous_line(item: tuple[str, object]) -> tuple[int, str]:
        anchor, record = item
        if isinstance(record, dict) and isinstance(record.get("start_line"), int):
            return int(record["start_line"]), anchor
        return sys.maxsize, anchor

    return tuple(
        anchor
        for anchor, _ in sorted(previous_sections.items(), key=previous_line)
        if anchor not in current
    )


def _metadata_changes(
    view: DocumentView, previous: dict[str, object]
) -> tuple[tuple[str, object, object], ...]:
    current: dict[str, object] = {
        "path": view.path.as_posix(),
        "revision": view.revision,
        "type": view.document_type,
        "status": view.status,
        "dependencies": [
            {
                "relation": edge.relation,
                "target": edge.peer_id,
                "expected_revision": edge.expected_revision,
            }
            for edge in view.outgoing
        ],
        "boundaries": [
            {"relation": relation, "value": value, "reason": reason}
            for relation, value, reason in view.boundaries
        ],
        "migrations": [
            {"relation": relation, "value": value, "target": target}
            for relation, value, target in view.migrations
        ],
        "related_values": list(view.related_values),
    }
    return tuple(
        (field, previous.get(field), value)
        for field, value in current.items()
        if previous.get(field) != value
    )


def build_packet_plans(
    views: Views,
    ordered: list[str],
    *,
    assumed: dict[str, int],
    since_manifest: dict[str, object] | None,
    generation_short: str | None,
) -> tuple[dict[str, DocumentPacketPlan], list[dict[str, object]], list[str], int]:
    """Build per-document delta plans and shared structured diagnostics."""

    plans: dict[str, DocumentPacketPlan] = {}
    mismatches: list[dict[str, object]] = []
    notes: list[str] = []
    assumed_known_omitted = 0
    changed_count = 0
    unchanged_omitted_count = 0
    for selected_id in ordered:
        view = views[selected_id]
        if since_manifest is not None:
            manifest_documents = since_manifest["documents"]
            previous = manifest_documents.get(selected_id)
            if not isinstance(previous, dict):
                plans[selected_id] = DocumentPacketPlan(
                    generation_short=generation_short,
                    changed_sections=_changed_section_anchors(view, {}),
                    changed_document=True,
                )
                notes.append(f"{selected_id}: new since {generation_short}")
                changed_count += 1
            elif view.source_sha256 == previous.get("source_sha256"):
                plans[selected_id] = DocumentPacketPlan(
                    omit_navigation=True,
                    content_omitted={
                        "reason": "unchanged-since",
                        "generation": generation_short,
                    },
                    coverage_state="unchanged-since",
                    generation_short=generation_short,
                )
                unchanged_omitted_count += 1
            else:
                previous_sections = previous.get("sections", {})
                if not isinstance(previous_sections, dict):
                    previous_sections = {}
                changed_sections = _changed_section_anchors(view, previous_sections)
                removed_sections = _removed_section_anchors(view, previous_sections)
                metadata_changes = _metadata_changes(view, previous)
                plans[selected_id] = DocumentPacketPlan(
                    generation_short=generation_short,
                    changed_sections=changed_sections,
                    removed_sections=removed_sections,
                    metadata_changes=metadata_changes,
                    source_changed_outside_sections=(
                        not changed_sections and not removed_sections
                    ),
                    changed_document=True,
                )
                if removed_sections:
                    notes.append(
                        f"{selected_id}: removed sections since {generation_short}: "
                        + ", ".join(removed_sections)
                    )
                for field, before, after in metadata_changes:
                    before_json = json.dumps(
                        before,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    after_json = json.dumps(
                        after,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    notes.append(
                        f"{selected_id}: metadata {field} changed: "
                        f"{before_json} -> {after_json}"
                    )
                if not changed_sections and not removed_sections:
                    notes.append(
                        f"{selected_id}: source changed outside addressable sections"
                    )
                changed_count += 1
        elif selected_id in assumed:
            declared = assumed[selected_id]
            if view.revision == declared:
                plans[selected_id] = DocumentPacketPlan(
                    omit_navigation=True,
                    content_omitted={
                        "reason": "assumed-known",
                        "declared_revision": declared,
                    },
                    coverage_state="assumed-known",
                    declared_revision=declared,
                )
                assumed_known_omitted += 1
            else:
                mismatches.append(
                    {
                        "id": selected_id,
                        "declared_revision": declared,
                        "current_revision": view.revision,
                    }
                )
                notes.append(
                    f"{selected_id}: assumed known at revision {declared}, "
                    f"current {view.revision} — content included"
                )
    if since_manifest is not None:
        notes.append(
            f"Delta vs generation {generation_short}: {changed_count} changed, "
            f"{unchanged_omitted_count} unchanged omitted"
        )
    return plans, mismatches, notes, assumed_known_omitted


def packet_sections(
    config: ProjectConfig,
    view: DocumentView,
    user_selected: list[str],
    plan: DocumentPacketPlan | None,
) -> tuple[list[str], set[str], list[str]]:
    """Select rendered and explicitly omitted H2 blocks for one document."""

    user = list(dict.fromkeys(user_selected))
    changed = plan.changed_sections if plan is not None else ()
    h2_anchors = {item.anchor for item in view.sections if item.level == 2}
    changed_blocks = [
        anchor
        for anchor in changed
        if anchor in h2_anchors and anchor not in config.navigation_extend_through
    ]
    extra = [anchor for anchor in changed_blocks if anchor not in user]
    explicit_anchors = user + extra
    omitted = [
        item.anchor
        for item in view.sections
        if item.level == 2
        and item.anchor not in config.navigation_extend_through
        and item.anchor not in explicit_anchors
    ]
    return explicit_anchors, set(extra), omitted
