"""Command-line interface for Documentation Engine."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from docsystem import __version__
from docsystem.catalog import (
    MarkdownCatalog,
    RelationBoundary,
    RelationMigration,
    ValidationIssue,
    build_catalog,
    build_dependency_graph,
    document_section_issues,
    find_document,
    validate_adoption,
    validate_catalog,
    validate_membership,
    validate_metadata,
)
from docsystem.change_plan import (
    ChangePlan,
    Completeness,
    InclusionReason,
    PlanItem,
    build_change_plan,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, ProjectConfig, load_config
from docsystem.graph import (
    Address,
    Boundary,
    GraphEdge,
    ProjectionUnavailable,
    TraversalResult,
    build_reference_graph,
    graph_validation_issues,
    parse_address,
)
from docsystem.graph import (
    traverse as graph_traverse,
)
from docsystem.graph import (
    traverse_reasons as graph_traverse_reasons,
)
from docsystem.maintenance import (
    CLEAN,
    CURRENT,
    DRIFTED,
    EXCLUDED,
    MANAGED,
    SOURCE,
    block_lines,
    block_text,
    resolve_marker,
    scan_markers,
    sha256_text,
    span_within_section,
    unified_block_diff,
)
from docsystem.migration import apply_migration_plan, build_migration_plan, validate_plan
from docsystem.projection import (
    LoadedProjection,
    build_projection,
    evaluate_changes,
    load_verified_projection,
    open_targeted_projection,
    projection_status,
    resolve_generation_manifest,
    targeted_forward_edges,
    targeted_reverse_edges,
    write_projection,
)
from docsystem.projection import (
    changes as projection_changes,
)
from docsystem.readiness import evaluate_readiness
from docsystem.sections import MarkdownSection, extract_navigation, extract_section
from docsystem.workspace import (
    WorkspaceError,
    resolve_source_root,
    resolve_workspace,
    source_statuses,
)

# Version of every `--json` root object. Bump only on a breaking change to
# an existing field; adding new fields is compatible and does not bump it.
JSON_SCHEMA_VERSION = 1

REPORT_TYPES = {
    "runtime-report": "Runtime Report",
    "adoption-finding": "Adoption Finding",
    "core-bug": "Core Bug",
    "docs-pattern-request": "Docs Pattern Request",
}
REPORT_SOURCES = ("codex", "claude", "vscode", "other")
REPORT_COMPONENTS = (
    "catalog",
    "metadata",
    "sections",
    "relations",
    "graph",
    "navigation",
    "anchors",
    "projection",
    "context",
    "mcp",
    "cli",
    "adoption",
    "profiles",
    "readiness",
    "reporting",
    "setup",
    "local-state",
    "privacy",
)


@dataclass(frozen=True)
class _Selection:
    """The one project a command runs against, and how it was addressed.

    Without `--source` this is exactly the positional project root and every
    command behaves as before. With `--source` the root comes from the local
    workspace registry, and `selector` replaces the root in any output a
    reader could paste elsewhere, so a private workspace path never leaves the
    machine that holds it.
    """

    project_root: Path
    source: str | None = None
    discovery_root: Path | None = None

    @property
    def project_argument(self) -> Path:
        """Return the caller's public/discovery root, not the private source."""

        return self.discovery_root or self.project_root

    @property
    def selector(self) -> str:
        if self.source is None:
            return str(self.project_argument)
        return f"{self.project_argument} --source {self.source}"

    @property
    def report_selector(self) -> str:
        """Render source selection without colliding with report host source."""

        if self.source is None:
            return str(self.project_argument)
        return f"{self.project_argument} --workspace-source {self.source}"


@dataclass(frozen=True)
class _ReferencesOutput:
    results: tuple[TraversalResult, ...]
    boundaries: tuple[Boundary, ...]
    observed_completeness: str


class _ReferenceGraphInvalid(Exception):
    """Graph-affecting diagnostics that make a query unsafe to answer."""

    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        super().__init__("reference graph is invalid")
        self.issues = issues


def _resolve_selection(args: argparse.Namespace) -> _Selection | None:
    """Resolve the effective project, or print one diagnostic and fail closed.

    Workspace state is neither loaded nor validated when `--source` is absent,
    so an unrelated broken workspace can never affect a plain project command.
    """

    source = getattr(args, "workspace_source", None)
    if source is None:
        if getattr(args, "workspace", None) is not None:
            print(
                "ERROR: --workspace requires --source/--workspace-source",
                file=sys.stderr,
            )
            return None
        return _Selection(args.project)
    try:
        project_root = resolve_source_root(
            source,
            workspace_option=getattr(args, "workspace", None),
            project_root=args.project,
        )
    except WorkspaceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return None
    return _Selection(project_root, source, args.project)


def _print_json(payload: dict[str, object]) -> None:
    print(
        json.dumps(
            {"schema_version": JSON_SCHEMA_VERSION, **payload},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def _write_text_or_stdout(text: str, output: Path | None) -> int:
    if output is None:
        sys.stdout.write(text)
        return 0
    try:
        output.write_text(text, encoding="utf-8")
    except OSError as error:
        print(f"ERROR: failed to write report draft: {error}", file=sys.stderr)
        return 1
    print(f"Report draft written: {output}")
    return 0


def _label_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "unknown"


def _migration_json(item: RelationMigration) -> dict[str, object]:
    return {
        "source_id": item.source_id,
        "relation": item.relation,
        "value": item.value,
        "target_id": item.target_id,
    }


def _boundary_json(item: RelationBoundary) -> dict[str, object]:
    return {
        "source_id": item.source_id,
        "relation": item.relation,
        "value": item.value,
        "reason": item.reason,
    }


def _with_graph_issues(
    issues: tuple[ValidationIssue, ...],
    markdown_catalog: MarkdownCatalog,
    config: ProjectConfig,
) -> tuple[ValidationIssue, ...]:
    """Fold relation-specific cycle and dead-reference diagnostics into validate/doctor."""

    return tuple(
        sorted(
            (*issues, *graph_validation_issues(markdown_catalog, config)),
            key=lambda issue: (issue.path.as_posix(), issue.severity, issue.message),
        )
    )


def _print_validation_issues(
    issues: tuple[ValidationIssue, ...],
    *,
    verbose_adoption: bool,
) -> bool:
    adoption_counts = {
        "adoption-resolved": 0,
        "adoption-boundary": 0,
    }
    visible: list[ValidationIssue] = []
    for issue in issues:
        compactable = (
            issue.severity == "warning" and issue.category in adoption_counts
        )
        if compactable and not verbose_adoption:
            adoption_counts[issue.category] += 1
        else:
            visible.append(issue)

    summaries = (
        (
            adoption_counts["adoption-resolved"],
            "legacy relation values resolve to stable IDs",
        ),
        (
            adoption_counts["adoption-boundary"],
            "legacy relation values remain resource/outside boundaries",
        ),
    )
    for count, description in summaries:
        if count:
            print(
                f"WARNING: {count} {description}; run "
                "`docsystem migration-report PROJECT` for row-level details.",
                file=sys.stderr,
            )
    for issue in visible:
        level = "WARNING" if issue.severity == "warning" else "ERROR"
        print(
            f"{level}: {issue.path.as_posix()}: {issue.message}",
            file=sys.stderr,
        )
    return any(issue.severity != "warning" for issue in issues)


def _workspace_status_rows(
    project_root: Path, workspace_option: Path | None
) -> list[dict[str, object]]:
    workspace = resolve_workspace(
        workspace_option=workspace_option, project_root=project_root
    )
    return [
        {
            "name": status.name,
            "visibility": status.visibility,
            "available": status.available,
            "reason": status.reason,
        }
        for status in source_statuses(workspace)
    ]


def workspace_list(
    project_root: Path,
    *,
    workspace_option: Path | None = None,
    json_output: bool = False,
) -> int:
    """List registered sources, sorted by name, with availability.

    Read-only and body-free: only the registered name, its declared
    visibility, whether it can be selected and a fixed reason slug are
    reported. No local path and no document content is ever emitted, so a
    listing is safe to share.
    """

    try:
        rows = _workspace_status_rows(project_root, workspace_option)
    except WorkspaceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if json_output:
        _print_json({"sources": rows})
        return 0
    for row in rows:
        state = "available" if row["available"] else "unavailable"
        print(f"{row['name']}\t{row['visibility']}\t{state}\t{row['reason'] or '-'}")
    return 0


def workspace_doctor(
    project_root: Path,
    *,
    workspace_option: Path | None = None,
    json_output: bool = False,
) -> int:
    """Report whether every registered source is currently selectable."""

    try:
        rows = _workspace_status_rows(project_root, workspace_option)
    except WorkspaceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    unavailable = [row for row in rows if not row["available"]]
    if json_output:
        _print_json({"sources": rows, "ready": not unavailable})
        return 1 if unavailable else 0
    print(f"Workspace manifest is valid: {len(rows)} source(s).")
    print(f"- Available sources: {len(rows) - len(unavailable)}")
    print(f"- Unavailable sources: {len(unavailable)}")
    for row in unavailable:
        print(
            f"ERROR: workspace source is unavailable: {row['name']} "
            f"({row['reason']})",
            file=sys.stderr,
        )
    return 1 if unavailable else 0


def initialize(project_root: Path) -> int:
    root = project_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / CONFIG_FILENAME
    if config_path.exists():
        print(f"Refusing to overwrite existing configuration: {config_path}", file=sys.stderr)
        return 1
    config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    config = load_config(root)
    config.documentation_root.mkdir(parents=True, exist_ok=True)
    print(f"Created {config_path}")
    print(f"Created documentation root: {config.documentation_root}")
    return 0


def doctor(project_root: Path, *, verbose_adoption: bool = False) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if not config.documentation_root.is_dir():
        print(
            f"ERROR: documentation root does not exist: "
            f"{config.documentation_root}",
            file=sys.stderr,
        )
        return 1
    markdown_catalog = build_catalog(config)
    issues = _with_graph_issues(
        validate_catalog(markdown_catalog, config), markdown_catalog, config
    )
    if _print_validation_issues(
        issues, verbose_adoption=verbose_adoption
    ):
        return 1
    print("Configuration is valid.")
    print(f"Documentation root: {config.documentation_root}")
    print(f"Language: {config.language}")
    print(f"Projection: {config.projection_format}")
    return 0


def catalog(project_root: Path, *, explain: bool = False, json_output: bool = False) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    markdown_catalog = build_catalog(config)
    if explain:
        if json_output:
            _print_json(
                {
                    "memberships": [
                        {
                            "state": membership.state,
                            "path": membership.path.as_posix(),
                            "role": membership.role,
                            "reason": membership.reason,
                        }
                        for membership in markdown_catalog.memberships
                    ]
                }
            )
            return 0
        for membership in markdown_catalog.memberships:
            detail = membership.role or membership.reason or "-"
            print(f"{membership.state}\t{detail}\t{membership.path.as_posix()}")
        return 0
    if json_output:
        _print_json(
            {
                "documents": [
                    {"role": document.role, "path": document.path.as_posix()}
                    for document in markdown_catalog.documents
                ]
            }
        )
        return 0
    for document in markdown_catalog.documents:
        print(f"{document.role}\t{document.path.as_posix()}")
    return 0


def validate(project_root: Path, *, verbose_adoption: bool = False) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    markdown_catalog = build_catalog(config)
    issues = _with_graph_issues(
        validate_catalog(markdown_catalog, config), markdown_catalog, config
    )
    if _print_validation_issues(
        issues, verbose_adoption=verbose_adoption
    ):
        return 1
    print("Markdown navigation is valid.")
    return 0


def read_document(
    project_root: Path,
    document_id: str,
    *,
    anchor: str | None = None,
    navigation: bool = False,
    list_sections: bool = False,
) -> int:
    try:
        config = load_config(project_root)
        views, _, catalog_value = _load_views(config)
        if catalog_value is not None:
            document = find_document(catalog_value, document_id)
            section_issues = document_section_issues(document, config)
            if section_issues:
                for message in section_issues:
                    print(
                        f"ERROR: {document.path.as_posix()}: {message}",
                        file=sys.stderr,
                    )
                return 1
        view = views.get(document_id)
        if view is None:
            raise ValueError(f"document ID not found: {document_id}")
        if list_sections:
            output = "".join(
                f"{section.anchor}\tH{section.level}\t"
                f"{section.start_line}:{section.end_line}\t{section.title}\n"
                for section in view.sections
            )
        elif anchor is not None:
            section = next(
                (item for item in view.sections if item.anchor == anchor), None
            )
            if section is None:
                raise ValueError(f"anchor not found in {document_id}: {anchor}")
            output = extract_section(view.content, section)
        elif navigation:
            output = extract_navigation(
                view.content,
                view.sections,
                config.navigation_extend_through,
            )
        else:
            output = (
                view.content
                if view.content.endswith("\n")
                else f"{view.content}\n"
            )
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0


@dataclass(frozen=True)
class _EdgeView:
    """One dependency edge as served to a read command."""

    relation: str
    peer_id: str
    expected_revision: int | None


@dataclass(frozen=True)
class _DocumentView:
    """Document data required by `read`, `context` and `impact`.

    Both the direct-Markdown path and the verified-projection path reduce to
    this shape, so command output is byte-identical regardless of which path
    served it. `migrations` and `boundaries` are `(relation, value, target)`
    and `(relation, value, reason)` triples; `related_values` preserves the
    document-order raw values used by the "Related omitted" note.
    """

    document_id: str
    path: PurePosixPath
    content: str
    sections: tuple[MarkdownSection, ...]
    revision: int
    document_type: str | None
    status: str | None
    outgoing: tuple[_EdgeView, ...]
    migrations: tuple[tuple[str, str, str], ...]
    boundaries: tuple[tuple[str, str, str], ...]
    related_values: tuple[str, ...]


_Views = dict[str, _DocumentView]
_Incoming = dict[str, tuple[_EdgeView, ...]]


def _freshness_rows(
    config,
    views: _Views,
    ordered: list[str],
) -> list[dict[str, object]]:
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
                        if view.document_type in config.snapshot_document_types
                        else "stale"
                    ),
                }
            )
    return rows


def _context_selection(
    views: _Views,
    document_id: str,
    *,
    depth: int,
    include_related: bool,
) -> dict[str, set[str]]:
    included: dict[str, set[str]] = {document_id: {"target"}}
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
            queue.append((edge.peer_id, current_depth + 1))
    return included


def _ordered_selection(included: dict[str, set[str]], document_id: str) -> list[str]:
    return [document_id, *sorted(item for item in included if item != document_id)]


def _views_from_catalog(catalog_value: MarkdownCatalog) -> tuple[_Views, _Incoming]:
    graph = build_dependency_graph(catalog_value)
    migrations: dict[str, list[tuple[str, str, str]]] = {}
    for item in catalog_value.relation_migrations:
        migrations.setdefault(item.source_id, []).append(
            (item.relation, item.value, item.target_id)
        )
    boundaries: dict[str, list[tuple[str, str, str]]] = {}
    for item in catalog_value.relation_boundaries:
        boundaries.setdefault(item.source_id, []).append(
            (item.relation, item.value, item.reason)
        )
    views: _Views = {}
    incoming: _Incoming = {}
    for document in catalog_value.documents:
        metadata = document.metadata
        if metadata is None:
            continue
        document_id = metadata.document_id
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
        views[document_id] = _DocumentView(
            document_id=document_id,
            path=document.path,
            content=document.content,
            sections=document.sections,
            revision=metadata.revision,
            document_type=metadata.document_type,
            status=metadata.status,
            outgoing=tuple(
                _EdgeView(edge.relation, edge.target_id, edge.expected_revision)
                for edge in graph.outgoing(document_id)
            ),
            migrations=tuple(migrations.get(document_id, ())),
            boundaries=tuple(boundaries.get(document_id, ())),
            related_values=tuple(related_values),
        )
        incoming[document_id] = tuple(
            _EdgeView(edge.relation, edge.source_id, edge.expected_revision)
            for edge in graph.incoming(document_id)
        )
    return views, incoming


def _views_from_projection(loaded: LoadedProjection) -> tuple[_Views, _Incoming]:
    views: _Views = {}
    incoming: _Incoming = {}
    for document_id, shard in loaded.documents.items():
        # Shard JSON is written with sorted keys, so section order is
        # restored from line numbers rather than mapping order.
        sections = tuple(
            MarkdownSection(
                title=str(record["title"]),
                anchor=anchor,
                level=int(record["level"]),
                start_line=int(record["start_line"]),
                end_line=int(record["end_line"]),
            )
            for anchor, record in sorted(
                shard["sections"].items(),
                key=lambda item: item[1]["start_line"],
            )
        )
        path = str(shard["path"])
        views[document_id] = _DocumentView(
            document_id=document_id,
            path=PurePosixPath(path),
            content=loaded.contents[path],
            sections=sections,
            revision=int(shard["revision"]),
            document_type=shard.get("type"),
            status=shard.get("status"),
            outgoing=tuple(
                _EdgeView(
                    record["relation"], record["target"], record.get("expected_revision")
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
            _EdgeView(
                record["relation"], record["source"], record.get("expected_revision")
            )
            for record in loaded.reverse.get(document_id, ())
        )
    return views, incoming


def _load_views(config) -> tuple[_Views, _Incoming, MarkdownCatalog | None]:
    """Serve reads from the verified projection, else direct Markdown.

    Returns the catalog only on the direct path; callers use its presence to
    run the validation that a verified projection already guarantees (a
    generation is only written for a tree with no blocking errors, and the
    loader proves the sources are byte-identical to that tree).
    """

    loaded, reason = load_verified_projection(config)
    if loaded is not None:
        views, incoming = _views_from_projection(loaded)
        return views, incoming, None
    print(f"WARNING: {reason}; using direct Markdown", file=sys.stderr)
    catalog_value = build_catalog(config)
    views, incoming = _views_from_catalog(catalog_value)
    return views, incoming, catalog_value


def _selection(raw: str) -> tuple[str, str | None]:
    document_id, separator, anchor = raw.partition("#")
    if not document_id or (separator and not anchor):
        raise ValueError(f"invalid include selection: {raw!r}")
    return document_id, anchor if separator else None


def _section_size_maps(view: _DocumentView) -> list[dict[str, object]]:
    """Return per-section `{anchor, title, level, lines, bytes}` in order.

    `bytes` is the exact UTF-8 size of the slice `extract_section` hashes
    (before its trailing-newline normalization), so the value is identical
    on both serving paths regardless of trailing whitespace handling.
    """

    lines = view.content.splitlines()
    maps: list[dict[str, object]] = []
    for section in view.sections:
        slice_text = "\n".join(lines[section.start_line - 1 : section.end_line])
        maps.append(
            {
                "anchor": section.anchor,
                "title": section.title,
                "level": section.level,
                "lines": section.end_line - section.start_line + 1,
                "bytes": len(slice_text.encode("utf-8")),
            }
        )
    return maps


def _source_sha(view: _DocumentView) -> str:
    """Return the sha256 of a document's full source, matching the manifest."""

    return hashlib.sha256(view.content.encode()).hexdigest()


def _section_sha(view: _DocumentView, section: MarkdownSection) -> str:
    """Return a section's sha256 over the exact slice the manifest hashes."""

    lines = view.content.splitlines()
    slice_text = "\n".join(lines[section.start_line - 1 : section.end_line])
    return hashlib.sha256(slice_text.encode()).hexdigest()


def _changed_section_anchors(
    view: _DocumentView, previous_sections: dict[str, object]
) -> tuple[str, ...]:
    """Return every anchor whose content changed since a generation, in doc order.

    A section is changed when its per-section sha256 differs from the recorded
    one or when the section is new — any level, no filtering. This is the
    complete truth signal reported as `changed_sections`: an H1's slice spans
    everything beneath it and an H2's slice spans its H3+ descendants, so a
    change anywhere always bubbles up through every enclosing anchor as well.
    Which of these anchors are actually re-emitted as `### Changed section`
    content blocks is decided separately in `_packet_sections`, since the H1
    and any `navigation.extend_through` H2 are already served by navigation.
    """

    return tuple(
        section.anchor
        for section in view.sections
        if not isinstance(previous_sections.get(section.anchor), dict)
        or previous_sections[section.anchor].get("sha256") != _section_sha(view, section)
    )


def _removed_section_anchors(
    view: _DocumentView, previous_sections: dict[str, object]
) -> tuple[str, ...]:
    """Return removed anchors in their previous document order."""

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
    view: _DocumentView, previous: dict[str, object]
) -> tuple[tuple[str, object, object], ...]:
    """Return deterministic semantic projection-field changes."""

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


@dataclass(frozen=True)
class _DocPlan:
    """Per-document rendering plan for `--assume-known` / `--since` packets.

    Documents without a plan (neither flag active for them) render exactly as
    before, so the flagless packet stays byte-identical. `coverage_state`
    selects the coverage-line wording; `content_omitted` is the JSON marker
    and is present precisely when navigation is omitted; `changed_sections`
    is the complete truth signal — every anchor at any level whose slice
    changed — reported verbatim as the JSON `changed_sections` key; only the
    subset that `_packet_sections` selects (changed H2s outside
    `navigation.extend_through`) is actually rendered as content.
    `removed_sections` and `metadata_changes` make non-current-section
    changes explicit instead of forcing a client to infer them from an empty
    changed-section list.
    """

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


def _build_packet_plans(
    views: _Views,
    ordered: list[str],
    *,
    assumed: dict[str, int],
    since_manifest: dict[str, object] | None,
    generation_short: str | None,
) -> tuple[dict[str, _DocPlan], list[dict[str, object]], list[str], int]:
    """Compute per-document plans plus shared diagnostics for the new flags.

    Returns `(plans, mismatches, notes, assumed_known_omitted)`. `mismatches`
    feeds the JSON `assume_known_mismatches`; `notes` are extra text
    diagnostics (revision mismatches, `new since` and the delta summary).
    """

    plans: dict[str, _DocPlan] = {}
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
                plans[selected_id] = _DocPlan(
                    generation_short=generation_short,
                    changed_sections=_changed_section_anchors(view, {}),
                    changed_document=True,
                )
                notes.append(f"{selected_id}: new since {generation_short}")
                changed_count += 1
            elif _source_sha(view) == previous.get("source_sha256"):
                plans[selected_id] = _DocPlan(
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
                plans[selected_id] = _DocPlan(
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
                        before, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    )
                    after_json = json.dumps(
                        after, ensure_ascii=False, sort_keys=True, separators=(",", ":")
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
                plans[selected_id] = _DocPlan(
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


def _packet_sections(
    config,
    view: _DocumentView,
    user_selected: list[str],
    plan: _DocPlan | None,
) -> tuple[list[str], set[str], list[str]]:
    """Return `(explicit_anchors, changed_set, omitted)` for one document.

    `explicit_anchors` is the ordered, de-duplicated set of section blocks to
    render: user `--anchor`/`--include` selections first, then auto-added
    `--since` changed sections. Auto-added anchors are restricted to changed
    H2s that are not already inside the navigation prefix
    (`navigation.extend_through`) — an H1 is always covered by the lead-in
    navigation serves, and an `extend_through` H2 is already inside it, so
    re-emitting either would duplicate content navigation already sent.
    `changed_set` marks the auto-added delta anchors so the text form can
    title them `### Changed section`. `omitted` is the usual coverage list of
    H2 anchors that are neither navigation extensions nor shown, computed
    against what the document actually renders, which makes it truthful by
    construction: every changed H2 is either emitted here or listed there.
    """

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


def _coverage_line(
    plan: _DocPlan | None, explicit_anchors: list[str], omitted: list[str]
) -> str:
    """Build the per-document `_Coverage_` line, honouring omitted content."""

    omitted_text = ", ".join(omitted) if omitted else "none"
    if plan is not None and plan.coverage_state == "assumed-known":
        body = (
            f"content omitted — declared known at revision "
            f"{plan.declared_revision} (current)"
        )
    elif plan is not None and plan.coverage_state == "unchanged-since":
        body = f"content omitted — unchanged since {plan.generation_short}"
    else:
        body = "navigation" + (" + explicit sections" if explicit_anchors else "")
    return f"_Coverage: {body}. Omitted H2: {omitted_text}._"


def _context_migrations_json(
    views: _Views, ordered: list[str]
) -> list[dict[str, object]]:
    return [
        {
            "source_id": selected_id,
            "relation": relation,
            "value": value,
            "target_id": target_id,
        }
        for selected_id in ordered
        for relation, value, target_id in views[selected_id].migrations
    ]


def _context_boundaries_json(
    views: _Views, ordered: list[str]
) -> list[dict[str, object]]:
    return [
        {
            "source_id": selected_id,
            "relation": relation,
            "value": value,
            "reason": reason,
        }
        for selected_id in ordered
        for relation, value, reason in views[selected_id].boundaries
    ]


def _context_related_omitted_json(
    views: _Views, document_id: str, *, include_related: bool
) -> list[str]:
    return [] if include_related else list(views[document_id].related_values)


def _emit_context_json(
    config,
    views: _Views,
    included: dict[str, set[str]],
    selected_anchors: dict[str, list[str]],
    ordered: list[str],
    document_id: str,
    *,
    depth: int,
    include_related: bool,
    plans: dict[str, _DocPlan] | None = None,
    mismatches: list[dict[str, object]] | None = None,
    assumed_known_omitted: int = 0,
    assume_known_used: bool = False,
) -> int:
    """Print the context packet as one structured JSON object.

    The JSON form carries the same selection, coverage and diagnostics data
    as the Markdown packet, but structured (typed lists instead of prose
    notes) so a machine client never parses packet text. Declared-cache and
    delta documents drop `navigation` for a typed `content_omitted` marker;
    `--since` changed documents additionally carry `changed_sections`. Extra
    top-level keys and stats appear only when the matching flag is used, so
    every flagless payload stays byte-identical.
    """

    plans = plans or {}
    documents: list[dict[str, object]] = []
    explicit_count = 0
    omitted_count = 0
    for selected_id in ordered:
        view = views[selected_id]
        plan = plans.get(selected_id)
        explicit_anchors, _, omitted = _packet_sections(
            config, view, selected_anchors[selected_id], plan
        )
        explicit_sections = []
        for selected_anchor in explicit_anchors:
            section = next(
                item for item in view.sections if item.anchor == selected_anchor
            )
            explicit_sections.append(
                {
                    "anchor": selected_anchor,
                    "content": extract_section(view.content, section).rstrip(),
                }
            )
        explicit_count += len(explicit_sections)
        omitted_count += len(omitted)
        entry: dict[str, object] = {
            "id": selected_id,
            "path": view.path.as_posix(),
            "revision": view.revision,
            "relations": sorted(included[selected_id]),
            "explicit_sections": explicit_sections,
            "omitted_h2": omitted,
            "sections": _section_size_maps(view),
        }
        if plan is not None and plan.omit_navigation:
            entry["content_omitted"] = plan.content_omitted
        else:
            entry["navigation"] = extract_navigation(
                view.content,
                view.sections,
                config.navigation_extend_through,
            ).rstrip()
        if plan is not None and plan.changed_document:
            entry["changed_sections"] = list(plan.changed_sections)
            entry["removed_sections"] = list(plan.removed_sections)
            entry["metadata_changes"] = [
                {"field": field, "before": before, "after": after}
                for field, before, after in plan.metadata_changes
            ]
            entry["source_changed_outside_sections"] = (
                plan.source_changed_outside_sections
            )
        documents.append(entry)
    freshness = _freshness_rows(config, views, ordered)
    stats: dict[str, object] = {
        "included_documents": len(ordered),
        "explicit_sections": explicit_count,
        "omitted_h2_sections": omitted_count,
    }
    if assume_known_used:
        stats["assumed_known_omitted"] = assumed_known_omitted
    payload: dict[str, object] = {
        "target": document_id,
        "depth": depth,
        "include_related": include_related,
        "outline": False,
        "documents": documents,
        "freshness": freshness,
        "migrations": _context_migrations_json(views, ordered),
        "boundaries": _context_boundaries_json(views, ordered),
        "related_omitted": _context_related_omitted_json(
            views, document_id, include_related=include_related
        ),
        "stats": stats,
    }
    if assume_known_used:
        payload["assume_known_mismatches"] = mismatches or []
    _print_json(payload)
    return 0


def _emit_context_outline_json(
    config,
    views: _Views,
    included: dict[str, set[str]],
    ordered: list[str],
    document_id: str,
    *,
    depth: int,
    include_related: bool,
) -> int:
    """Print the map-first outline packet: section sizes, no content.

    Shares the same root shape as the full `context --json` packet
    (target/depth/include_related/diagnostics), but `documents[]` entries
    carry only `id`, `path`, `revision`, `relations` and `sections`, and
    `stats` counts listed sections and their total byte size instead of
    explicit/omitted H2 counts, so a client can budget a follow-up `--include`
    fetch while retaining the revision needed by `--assume-known`.
    """

    documents: list[dict[str, object]] = []
    listed_sections = 0
    total_section_bytes = 0
    for selected_id in ordered:
        view = views[selected_id]
        sections = _section_size_maps(view)
        listed_sections += len(sections)
        total_section_bytes += sum(int(item["bytes"]) for item in sections)
        documents.append(
            {
                "id": selected_id,
                "path": view.path.as_posix(),
                "revision": view.revision,
                "relations": sorted(included[selected_id]),
                "sections": sections,
            }
        )
    freshness = _freshness_rows(config, views, ordered)
    _print_json(
        {
            "target": document_id,
            "depth": depth,
            "include_related": include_related,
            "outline": True,
            "documents": documents,
            "freshness": freshness,
            "migrations": _context_migrations_json(views, ordered),
            "boundaries": _context_boundaries_json(views, ordered),
            "related_omitted": _context_related_omitted_json(
                views, document_id, include_related=include_related
            ),
            "stats": {
                "included_documents": len(ordered),
                "listed_sections": listed_sections,
                "total_section_bytes": total_section_bytes,
            },
        }
    )
    return 0


def _context_diagnostic_notes(
    config,
    views: _Views,
    ordered: list[str],
    document_id: str,
    *,
    include_related: bool,
    extra_notes: list[str] | None = None,
) -> list[str]:
    """Return the sorted "Diagnostics and boundaries" note lines.

    Shared between the full-content packet and `--outline`, which prints
    exactly the same notes ahead of its own closing action line. `extra_notes`
    carries declared-cache and delta-packet notes; it is empty for `--outline`
    (which cannot combine with `--assume-known`/`--since`), keeping that
    packet byte-identical.
    """

    notes: list[str] = list(extra_notes or [])
    freshness_found = False
    for row in _freshness_rows(config, views, ordered):
        mode = (
            "historical snapshot"
            if row["classification"] == "historical snapshot"
            else "STALE"
        )
        notes.append(
            f"{row['source_id']}: {row['target_id']}@"
            f"{row['pinned_revision']}, current "
            f"{row['current_revision']} — {mode}"
        )
        freshness_found = True
    if not freshness_found:
        notes.append("No stale revision pins among included documents.")
    for selected_id in ordered:
        for relation, value, target_id in views[selected_id].migrations:
            notes.append(f"{selected_id}: {relation} {value} -> {target_id}")
    boundary_found = False
    for selected_id in ordered:
        for relation, value, reason in views[selected_id].boundaries:
            notes.append(
                f"{selected_id}: unresolved/resource {relation} "
                f"{value} ({reason})"
            )
            boundary_found = True
    if not boundary_found:
        notes.append(
            "No unresolved/resource boundaries among included documents."
        )
    if not include_related:
        related = list(views[document_id].related_values)
        if related:
            notes.append("Related omitted: " + ", ".join(related))
    return sorted(set(notes))


def _emit_context_outline_text(
    config,
    views: _Views,
    included: dict[str, set[str]],
    ordered: list[str],
    document_id: str,
    *,
    depth: int,
    include_related: bool,
) -> int:
    """Print the map-first outline: section size tables, no content."""

    out: list[str] = []
    out.append(f"# Context outline: {document_id}")
    out.append("")
    out.append(f"- Dependency depth: {depth}")
    out.append(f"- Related traversal: {'included' if include_related else 'omitted'}")
    listed_sections = 0
    total_bytes = 0
    for selected_id in ordered:
        view = views[selected_id]
        out.append("")
        out.append(f"## {selected_id} — {view.path.as_posix()}")
        out.append("")
        out.append(f"Relations: {', '.join(sorted(included[selected_id]))}.")
        out.append("")
        out.append("| Anchor | Level | Lines | Bytes | Title |")
        out.append("|---|---|---|---|---|")
        for section_map in _section_size_maps(view):
            out.append(
                f"| `{section_map['anchor']}` | H{section_map['level']} | "
                f"{section_map['lines']} | {section_map['bytes']} | "
                f"{section_map['title']} |"
            )
            listed_sections += 1
            total_bytes += int(section_map["bytes"])
    out.append("")
    out.append("## Diagnostics and boundaries")
    out.append("")
    for note in _context_diagnostic_notes(
        config, views, ordered, document_id, include_related=include_related
    ):
        out.append(f"- {note}")
    out.append(
        "- Fetch content with --include ID#anchor, or drop --outline for full navigation."
    )
    out.append("")
    out.append("## Packet stats")
    out.append("")
    out.append(f"- Included documents: {len(ordered)}")
    out.append(f"- Listed sections: {listed_sections}")
    out.append(f"- Total section bytes: {total_bytes}")
    sys.stdout.write("\n".join(out) + "\n")
    return 0


def context(
    project_root: Path,
    document_id: str,
    *,
    anchor: str | None = None,
    depth: int = 1,
    include_related: bool = False,
    includes: list[str] | None = None,
    json_output: bool = False,
    outline: bool = False,
    assume_known: list[str] | None = None,
    since: str | None = None,
) -> int:
    if outline and (anchor is not None or includes):
        print(
            "ERROR: cannot combine --outline with --anchor or --include",
            file=sys.stderr,
        )
        return 1
    if outline and (assume_known or since is not None):
        print(
            "ERROR: cannot combine --outline with --assume-known or --since",
            file=sys.stderr,
        )
        return 1
    if since is not None and assume_known:
        print(
            "ERROR: cannot combine --since with --assume-known",
            file=sys.stderr,
        )
        return 1
    assumed: dict[str, int] = {}
    for raw in assume_known or []:
        document, separator, revision = raw.partition("@")
        if (
            not separator
            or not document
            or not (revision.isascii() and revision.isdigit())
            or int(revision) <= 0
        ):
            print(f"ERROR: invalid --assume-known value: {raw!r}", file=sys.stderr)
            return 1
        declared = int(revision)
        if document in assumed and assumed[document] != declared:
            print(
                f"ERROR: conflicting --assume-known declarations for {document}",
                file=sys.stderr,
            )
            return 1
        assumed[document] = declared
    since_manifest: dict[str, object] | None = None
    generation_short: str | None = None
    try:
        config = load_config(project_root)
        if since is not None:
            resolved = resolve_generation_manifest(config, since)
            if resolved is None:
                print(
                    f"ERROR: unknown projection generation: {since}",
                    file=sys.stderr,
                )
                return 1
            generation, since_manifest = resolved
            generation_short = generation[:12]
        views, _, catalog_value = _load_views(config)
        if catalog_value is not None:
            find_document(catalog_value, document_id)
        elif document_id not in views:
            raise ValueError(f"document ID not found: {document_id}")
        included = _context_selection(
            views,
            document_id,
            depth=depth,
            include_related=include_related,
        )
        forced: dict[str, list[str]] = {}
        for raw in includes or []:
            selected_id, selected_anchor = _selection(raw)
            if catalog_value is not None:
                find_document(catalog_value, selected_id)
            elif selected_id not in views:
                raise ValueError(f"document ID not found: {selected_id}")
            included.setdefault(selected_id, set()).add("explicit")
            if selected_anchor:
                forced.setdefault(selected_id, []).append(selected_anchor)
        for assumed_id in assumed:
            # A declared document is validated even when it does not enter the
            # packet, so a stale declaration fails closed instead of silently
            # doing nothing. Declaring an ID never forces its inclusion.
            if catalog_value is not None:
                find_document(catalog_value, assumed_id)
            elif assumed_id not in views:
                raise ValueError(f"document ID not found: {assumed_id}")
        selected_anchors = {
            selected_id: [
                *([anchor] if selected_id == document_id and anchor else []),
                *forced.get(selected_id, []),
            ]
            for selected_id in included
        }
        if catalog_value is not None:
            # A verified projection is only ever written for a tree with no
            # blocking errors, so this validation runs on the direct path only.
            by_id = {
                document.metadata.document_id: document
                for document in catalog_value.documents
                if document.metadata is not None
            }
            relevant_paths = {by_id[item].path for item in included}
            blockers = [
                issue
                for issue in (
                    *validate_metadata(catalog_value),
                    *validate_adoption(catalog_value, config),
                )
                if issue.affects_graph
                and issue.severity != "warning"
                and issue.path in relevant_paths
            ]
            for selected_id in included:
                document = by_id[selected_id]
                blockers.extend(
                    ValidationIssue(document.path, message)
                    for message in document_section_issues(document, config)
                )
            if blockers:
                for issue in blockers:
                    print(
                        f"ERROR: {issue.path.as_posix()}: {issue.message}",
                        file=sys.stderr,
                    )
                return 1
        for selected_id in included:
            known_anchors = {section.anchor for section in views[selected_id].sections}
            for selected_anchor in selected_anchors[selected_id]:
                if selected_anchor not in known_anchors:
                    raise ValueError(
                        f"anchor not found in {selected_id}: {selected_anchor}"
                    )
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    ordered = _ordered_selection(included, document_id)
    if outline:
        if json_output:
            return _emit_context_outline_json(
                config,
                views,
                included,
                ordered,
                document_id,
                depth=depth,
                include_related=include_related,
            )
        return _emit_context_outline_text(
            config,
            views,
            included,
            ordered,
            document_id,
            depth=depth,
            include_related=include_related,
        )
    plans, mismatches, extra_notes, assumed_known_omitted = _build_packet_plans(
        views,
        ordered,
        assumed=assumed,
        since_manifest=since_manifest,
        generation_short=generation_short,
    )
    if json_output:
        return _emit_context_json(
            config,
            views,
            included,
            selected_anchors,
            ordered,
            document_id,
            depth=depth,
            include_related=include_related,
            plans=plans,
            mismatches=mismatches,
            assumed_known_omitted=assumed_known_omitted,
            assume_known_used=bool(assume_known),
        )
    out: list[str] = []
    explicit_count = 0
    omitted_count = 0
    out.append(f"# Context packet: {document_id}")
    out.append("")
    out.append(f"- Dependency depth: {depth}")
    out.append(f"- Related traversal: {'included' if include_related else 'omitted'}")
    for selected_id in ordered:
        view = views[selected_id]
        plan = plans.get(selected_id)
        out.append("")
        out.append(f"## {selected_id} — {view.path.as_posix()}")
        out.append("")
        out.append(f"Relations: {', '.join(sorted(included[selected_id]))}.")
        explicit_anchors, changed_set, omitted = _packet_sections(
            config, view, selected_anchors[selected_id], plan
        )
        if plan is None or not plan.omit_navigation:
            out.append("")
            out.append(
                extract_navigation(
                    view.content,
                    view.sections,
                    config.navigation_extend_through,
                ).rstrip()
            )
        for selected_anchor in explicit_anchors:
            section = next(
                (
                    item
                    for item in view.sections
                    if item.anchor == selected_anchor
                ),
                None,
            )
            explicit_count += 1
            title = (
                "Changed section"
                if selected_anchor in changed_set
                else "Explicit section"
            )
            out.append("")
            out.append(f"### {title} `{selected_anchor}`")
            out.append("")
            out.append(extract_section(view.content, section).rstrip())
        omitted_count += len(omitted)
        out.append("")
        out.append(_coverage_line(plan, explicit_anchors, omitted))
    out.append("")
    out.append("## Diagnostics and boundaries")
    out.append("")
    for note in _context_diagnostic_notes(
        config,
        views,
        ordered,
        document_id,
        include_related=include_related,
        extra_notes=extra_notes,
    ):
        out.append(f"- {note}")
    out.append("- Expand with --depth, --include-related, or --include ID#anchor.")
    body = "\n".join(out) + "\n"
    line_count = body.count("\n")
    byte_count = len(body.encode("utf-8"))
    sys.stdout.write(body)
    print()
    print("## Packet stats")
    print()
    print(f"- Included documents: {len(ordered)}")
    print(f"- Explicit sections: {explicit_count}")
    print(f"- Omitted H2 sections: {omitted_count}")
    if assume_known:
        print(f"- Content omitted (assumed known): {assumed_known_omitted}")
    print(f"- Body size: {line_count} lines, {byte_count} UTF-8 bytes")
    return 0


def impact(project_root: Path, document_id: str) -> int:
    try:
        config = load_config(project_root)
        views, incoming, catalog_value = _load_views(config)
        if catalog_value is not None:
            find_document(catalog_value, document_id)
            blockers = [
                issue
                for issue in (
                    *validate_membership(catalog_value),
                    *validate_metadata(catalog_value),
                    *validate_adoption(catalog_value, config),
                )
                if issue.affects_graph and issue.severity != "warning"
            ]
            if blockers:
                for issue in blockers:
                    print(
                        f"ERROR: {issue.path.as_posix()}: {issue.message}",
                        file=sys.stderr,
                    )
                return 1
        target = views.get(document_id)
        if target is None:
            raise ValueError(f"document ID not found: {document_id}")
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"# Impact analysis: {document_id}")
    print()
    print(f"- Path: `{target.path.as_posix()}`")
    print(f"- Type/status: {target.document_type} / {target.status}")
    print(f"- Current revision: {target.revision}")
    print()
    print("| Downstream | Relation | Pin | Classification |")
    print("|---|---|---|---|")
    edges = incoming.get(document_id, ())
    for edge in edges:
        source_type = views[edge.peer_id].document_type
        if edge.relation == "related":
            classification = "related navigation"
        elif edge.relation == "validated_against":
            if source_type in config.snapshot_document_types:
                classification = "historical snapshot"
            elif edge.expected_revision == target.revision:
                classification = "freshness pin (current)"
            else:
                classification = "freshness pin (already stale)"
        elif edge.relation == "supersedes":
            classification = "lineage"
        else:
            classification = "semantic"
        pin = str(edge.expected_revision) if edge.expected_revision else "—"
        print(
            f"| `{edge.peer_id}` | {edge.relation} | {pin} | "
            f"{classification} |"
        )
    if not edges:
        print("| — | — | — | no reverse metadata dependencies |")
    return 0


def migration_report(project_root: Path, *, json_output: bool = False) -> int:
    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if json_output:
        _print_json(
            {
                "resolved": [
                    _migration_json(item)
                    for item in catalog_value.relation_migrations
                ],
                "boundaries": [
                    _boundary_json(item)
                    for item in catalog_value.relation_boundaries
                ],
            }
        )
        return 0
    for item in catalog_value.relation_migrations:
        print(
            f"resolved\t{item.source_id}\t{item.relation}\t"
            f"{item.value}\t{item.target_id}"
        )
    for item in catalog_value.relation_boundaries:
        print(
            f"boundary\t{item.source_id}\t{item.relation}\t"
            f"{item.value}\t{item.reason}"
        )
    return 0


def migrate(project_root: Path, *, apply: bool = False) -> int:
    """Preview, by default, or (with `apply`) write resolved legacy relations.

    Preview is entirely read-only. `apply` computes the same plan, validates
    it against a scratch copy of the documentation tree, and only then
    rewrites the affected Markdown files atomically.
    """

    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if not config.documentation_root.is_dir():
        print(
            f"ERROR: documentation root does not exist: {config.documentation_root}",
            file=sys.stderr,
        )
        return 1
    try:
        catalog_value = build_catalog(config)
        plan = build_migration_plan(config, catalog_value)
        problems = validate_plan(config, plan)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"ERROR: failed to build migration plan: {error}", file=sys.stderr)
        return 1
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    if not plan.changes:
        print("No resolvable legacy relation migrations found.")
        return 0

    for change in plan.changes:
        print(
            f"would-migrate\t{change.source_id}\t{change.relation}\t"
            f"{change.old_value}\t{change.new_value}\t{change.path.as_posix()}"
        )
    if apply:
        try:
            apply_migration_plan(config, plan)
        except (OSError, ValueError) as error:
            print(f"ERROR: failed to apply migration: {error}", file=sys.stderr)
            return 1
        print(
            f"Applied {len(plan.changes)} legacy relation migration(s) across "
            f"{len(plan.updated_contents)} file(s)."
        )
    else:
        print(
            f"Preview only; {len(plan.changes)} legacy relation migration(s) across "
            f"{len(plan.updated_contents)} file(s). Re-run with --apply to write."
        )
    return 0


def _issue_json(issue: ValidationIssue) -> dict[str, object]:
    return {
        "path": issue.path.as_posix(),
        "message": issue.message,
        "severity": issue.severity,
        "target_id": issue.target_id,
    }


def readiness(
    project_root: Path,
    *,
    json_output: bool = False,
    selection: _Selection | None = None,
) -> int:
    """Report, read-only, whether an existing project is adoption-ready.

    Stable summary data (counts, projection state, the next safe command)
    goes to stdout; ERROR/WARNING diagnostics go to stderr, matching
    `validate`, `doctor` and `migrate`. `--json` prints one deterministic
    object carrying the same data in full instead of counts, so a consumer
    never has to parse the stderr diagnostics.

    In selected-source mode the project is addressed by its reusable
    `--source NAME` selector everywhere the project root would otherwise be
    printed, and the payload gains a `source` key. Without a source the
    output is unchanged.
    """

    selection = selection or _Selection(project_root)
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    catalog_value = build_catalog(config)
    report = evaluate_readiness(config, catalog_value)
    next_command = report.next_command(selection.selector)

    if json_output:
        # One payload shape for every project state: a missing documentation
        # root reports empty categories rather than a shorter object, so a
        # consumer never has to branch on which keys exist.
        payload: dict[str, object] = {
            "documentation_root_exists": report.documentation_root_exists,
            "ready": report.ready,
            "blocking": [_issue_json(issue) for issue in report.blocking],
            "resolvable_migrations": [
                _migration_json(item) for item in report.resolvable_migrations
            ],
            "boundaries": [_boundary_json(item) for item in report.boundaries],
            "stale_pins": [_issue_json(issue) for issue in report.stale_pins],
            "projection": {
                "state": report.projection_state,
                "reason": report.projection_reason,
            },
            "next_command": next_command,
        }
        if selection.source is not None:
            payload["source"] = selection.source
        _print_json(payload)
        return 0 if report.ready else 1

    if not report.documentation_root_exists:
        # The documentation root is named relatively under a selected source,
        # so no diagnostic stream carries the private absolute path either.
        documentation_root = (
            config.documentation_root.relative_to(config.project_root).as_posix()
            if selection.source is not None
            else config.documentation_root
        )
        print(f"# Adoption readiness: {selection.selector}")
        print()
        print(
            f"ERROR: documentation root does not exist: {documentation_root}",
            file=sys.stderr,
        )
        print(f"- Next safe command: {next_command}")
        return 1

    print(f"# Adoption readiness: {selection.selector}")
    print()
    print(f"- Blocking structural/configuration errors: {len(report.blocking)}")
    for issue in report.blocking:
        level = "WARNING" if issue.severity == "warning" else "ERROR"
        print(f"  {level}: {issue.path.as_posix()}: {issue.message}", file=sys.stderr)
    print(f"- Resolvable legacy relation migrations: {len(report.resolvable_migrations)}")
    print(f"- Explicit unresolved/resource boundaries: {len(report.boundaries)}")
    print(f"- Stale freshness pins: {len(report.stale_pins)}")
    for issue in report.stale_pins:
        print(f"  WARNING: {issue.path.as_posix()}: {issue.message}", file=sys.stderr)
    print(f"- Projection: {report.projection_state} ({report.projection_reason})")
    print()
    print(
        "Run `docsystem migration-report` or `docsystem validate --verbose-adoption` "
        "for row-level migration and boundary detail."
    )
    print(f"- Next safe command: {next_command}")
    return 0 if report.ready else 1


def index_projection(project_root: Path, *, write: bool = False) -> int:
    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
        errors = [
            issue
            for issue in validate_catalog(catalog_value, config)
            if issue.severity != "warning"
        ]
        if errors:
            for issue in errors:
                print(
                    f"ERROR: {issue.path.as_posix()}: {issue.message}",
                    file=sys.stderr,
                )
            return 1
        current = build_projection(catalog_value, config)
        valid, reason = projection_status(config, current)
        if write:
            generation = write_projection(config, current)
            print(f"Projection generation written: {generation}")
            return 0
        if not valid:
            print(f"ERROR: {reason}", file=sys.stderr)
            return 1
        print("Projection is current.")
        return 0
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


def changes(project_root: Path, *, json_output: bool = False) -> int:
    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
        errors = [
            issue
            for issue in validate_catalog(catalog_value, config)
            if issue.severity != "warning"
        ]
        if errors:
            for issue in errors:
                print(
                    f"ERROR: {issue.path.as_posix()}: {issue.message}",
                    file=sys.stderr,
                )
            return 1
        current = build_projection(catalog_value, config)
        if json_output:
            report = evaluate_changes(config, current)
            _print_json(
                {
                    "status": report.status,
                    "changes": [
                        {
                            "document_id": change.document_id,
                            "kind": change.kind,
                            "sections": list(change.sections),
                        }
                        for change in report.changes
                    ],
                }
            )
            return 0
        for line in projection_changes(config, current):
            print(line)
        return 0
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


def _validation_summary(issues: tuple[ValidationIssue, ...]) -> dict[str, int]:
    summary = {
        "errors": 0,
        "warnings": 0,
        "adoption_resolved": 0,
        "adoption_boundaries": 0,
        "stale_pins": 0,
    }
    for issue in issues:
        if issue.severity == "warning":
            summary["warnings"] += 1
        else:
            summary["errors"] += 1
        if issue.category == "adoption-resolved":
            summary["adoption_resolved"] += 1
        elif issue.category == "adoption-boundary":
            summary["adoption_boundaries"] += 1
        elif issue.target_id is not None:
            summary["stale_pins"] += 1
    return summary


def _sanitize_local_error(error: Exception, selection: _Selection) -> str:
    """Strip the local project root out of an error message.

    Without a source the message keeps naming the project root exactly as the
    caller typed it. With a source the root is private workspace wiring, so
    every spelling of it is replaced by the reusable selector.
    """

    project_root = selection.project_root
    message = str(error)
    try:
        resolved = project_root.resolve().as_posix()
    except OSError:
        return message
    if selection.source is None:
        return message.replace(resolved, project_root.as_posix())
    for spelling in (resolved, project_root.as_posix(), str(project_root)):
        message = message.replace(spelling, selection.selector)
    return message


def _readiness_diagnostics(
    config,
    catalog_value: MarkdownCatalog,
    selection: _Selection,
) -> list[str]:
    report = evaluate_readiness(config, catalog_value)
    diagnostics = [
        f"documentation_root_exists={report.documentation_root_exists}",
        f"ready={report.ready}",
        f"blocking_errors={len(report.blocking)}",
        f"resolvable_migrations={len(report.resolvable_migrations)}",
        f"boundaries={len(report.boundaries)}",
        f"stale_pins={len(report.stale_pins)}",
        f"projection={report.projection_state} ({report.projection_reason})",
        f"next_command={report.next_command(selection.selector)}",
    ]
    return diagnostics


def _report_body(
    *,
    selection: _Selection,
    project_name: str,
    report_type: str,
    source: str,
    component: str | None,
) -> str:
    project_root = selection.project_root
    labels = [
        report_type,
        "triage",
        f"project:{_label_slug(project_name)}",
        f"source:{source}",
    ]
    if component:
        labels.append(f"component:{component}")

    diagnostics: list[str] = []
    config_excerpt = "not available"
    affected: list[str] = []
    runtime_changes = "none; report draft is read-only"
    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
        issues = validate_catalog(catalog_value, config)
        summary = _validation_summary(issues)
        diagnostics.extend(_readiness_diagnostics(config, catalog_value, selection))
        diagnostics.append(
            "validation="
            f"{summary['errors']} error(s), {summary['warnings']} warning(s)"
        )
        diagnostics.append(
            "adoption="
            f"{summary['adoption_resolved']} resolved mapping(s), "
            f"{summary['adoption_boundaries']} boundary row(s)"
        )
        diagnostics.append(f"stale_pins={summary['stale_pins']}")
        views, _ = _views_from_catalog(catalog_value)
        ordered_ids = sorted(views)
        freshness = _freshness_rows(config, views, ordered_ids)
        historical_count = sum(
            1
            for item in freshness
            if item["classification"] == "historical snapshot"
        )
        current_stale_count = sum(
            1 for item in freshness if item["classification"] == "stale"
        )
        diagnostics.append(
            "freshness_classification="
            f"{current_stale_count} stale, {historical_count} historical snapshot"
        )
        current = build_projection(catalog_value, config)
        changes_report = evaluate_changes(config, current)
        diagnostics.append(
            f"changes={changes_report.status}, {len(changes_report.changes)} change(s)"
        )
        documentation_root = config.documentation_root.relative_to(
            config.project_root
        ).as_posix()
        config_excerpt = "\n".join(
            (
                f'documentation.root = "{documentation_root}"',
                f'documentation.language = "{config.language}"',
                "areas = "
                + json.dumps(
                    {role: path.as_posix() for role, path in config.areas.items()},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "identifiers = "
                + json.dumps(config.identifiers, ensure_ascii=False, sort_keys=True),
                "catalog.exclude = "
                + json.dumps(
                    list(config.catalog_exclusions),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "navigation.extend_through = "
                + json.dumps(
                    list(config.navigation_extend_through),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                f'relations.legacy_paths = "{config.legacy_relation_mode}"',
                "relations.snapshot_types = "
                + json.dumps(
                    list(config.snapshot_document_types),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                f'projection.format = "{config.projection_format}"',
            )
        )
        affected.extend(
            sorted(
                {
                    issue.path.as_posix()
                    for issue in issues
                    if issue.severity != "warning" or issue.target_id is not None
                }
            )
        )
    except (OSError, ValueError) as error:
        diagnostics.append(
            f"configuration_error={_sanitize_local_error(error, selection)}"
        )

    title = REPORT_TYPES[report_type]
    component_text = component or "not selected"
    affected_text = "\n".join(f"- `{item}`" for item in affected) or "- none captured"
    diagnostics_text = "\n".join(f"- {item}" for item in diagnostics) or "- none captured"
    labels_text = ", ".join(f"`{label}`" for label in labels)
    command = (
        f"docsystem report draft {selection.report_selector} --project-name "
        f"{project_name!r} --type {report_type} --source {source}"
        + (f" --component {component}" if component else "")
    )
    return f"""# {title}: {project_name}

## Labels

{labels_text}

## Project

- Project/adopter name: {project_name}
- Source host or agent: {source}
- DocumentationEngine version: {__version__}
- DocumentationEngine commit: <!-- fill if different from installed version -->
- Component: {component_text}

## Commands run and exit codes

```bash
{command}
exit: 0
```

## Compact diagnostics

{diagnostics_text}

## Sanitized config/profile excerpt

```text
{config_excerpt}
```

## Affected IDs, anchors, or paths

{affected_text}

## Expected behavior

<!-- Fill in the expected behavior. -->

## Actual behavior

<!-- Fill in the observed behavior or compatibility gap. -->

## Runtime or local-state changes made

{runtime_changes}

## Requested DocumentationEngine action

<!-- Fill in the requested fix, clarification, migration support, or pattern. -->

## Privacy and sanitization checklist

- [ ] Private document bodies are omitted or sanitized.
- [ ] Private scratch, review, roadmap, or planning content is omitted.
- [ ] Config/profile excerpts are sanitized and contain no secrets.
- [ ] Local artifact paths, if any, are pointers only.
- [ ] A minimal public/synthetic fixture is included when this is a core bug.
"""


def report_draft(
    project_root: Path,
    *,
    project_name: str,
    report_type: str,
    source: str,
    component: str | None = None,
    output: Path | None = None,
    selection: _Selection | None = None,
) -> int:
    if component is not None and component not in REPORT_COMPONENTS:
        print(f"ERROR: unsupported report component: {component}", file=sys.stderr)
        return 1
    text = _report_body(
        selection=selection or _Selection(project_root),
        project_name=project_name,
        report_type=report_type,
        source=source,
        component=component,
    )
    return _write_text_or_stdout(text, output)


def finish(
    project_root: Path,
    document_id: str,
    *,
    depth: int = 1,
    include_related: bool = False,
    json_output: bool = False,
) -> int:
    try:
        config = load_config(project_root)
        views, _, catalog_value = _load_views(config)
        if catalog_value is not None:
            find_document(catalog_value, document_id)
        elif document_id not in views:
            raise ValueError(f"document ID not found: {document_id}")
        included = _context_selection(
            views,
            document_id,
            depth=depth,
            include_related=include_related,
        )
        ordered = _ordered_selection(included, document_id)
        target = views[document_id]
        omitted_by_document = {
            selected_id: [
                section.anchor
                for section in views[selected_id].sections
                if section.level == 2
                and section.anchor not in config.navigation_extend_through
            ]
            for selected_id in ordered
        }
        freshness = _freshness_rows(config, views, ordered)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    migrations = [
        {
            "source_id": selected_id,
            "relation": relation,
            "value": value,
            "target_id": target_id,
        }
        for selected_id in ordered
        for relation, value, target_id in views[selected_id].migrations
    ]
    boundaries = [
        {
            "source_id": selected_id,
            "relation": relation,
            "value": value,
            "reason": reason,
        }
        for selected_id in ordered
        for relation, value, reason in views[selected_id].boundaries
    ]
    if json_output:
        _print_json(
            {
                "target": document_id,
                "path": target.path.as_posix(),
                "depth": depth,
                "include_related": include_related,
                "included_documents": [
                    {
                        "id": selected_id,
                        "path": views[selected_id].path.as_posix(),
                        "relations": sorted(included[selected_id]),
                        "omitted_h2": omitted_by_document[selected_id],
                    }
                    for selected_id in ordered
                ],
                "freshness": freshness,
                "migrations": migrations,
                "boundaries": boundaries,
            }
        )
        return 0

    print(f"# Finish handoff: {document_id}")
    print()
    print(f"- Path: `{target.path.as_posix()}`")
    print(f"- Type/status: {target.document_type} / {target.status}")
    print(f"- Revision: {target.revision}")
    print(f"- Dependency depth summarized: {depth}")
    print(f"- Related traversal: {'included' if include_related else 'omitted'}")
    print()
    print("## Included context")
    print()
    for selected_id in ordered:
        relation_text = ", ".join(sorted(included[selected_id]))
        omitted = omitted_by_document[selected_id]
        print(
            f"- `{selected_id}` — `{views[selected_id].path.as_posix()}` "
            f"({relation_text}); omitted H2: "
            f"{', '.join(omitted) if omitted else 'none'}"
        )
    print()
    print("## Freshness and snapshot pins")
    print()
    if freshness:
        for row in freshness:
            print(
                f"- `{row['source_id']}` pins `{row['target_id']}@"
                f"{row['pinned_revision']}`; current revision is "
                f"{row['current_revision']} — {row['classification']}"
            )
    else:
        print("- No stale or historical snapshot pins among included documents.")
    print()
    print("## Migration and boundary notes")
    print()
    if migrations:
        for item in migrations:
            print(
                f"- `{item['source_id']}` {item['relation']} "
                f"`{item['value']}` -> `{item['target_id']}`"
            )
    else:
        print("- No resolved legacy relation mappings among included documents.")
    if boundaries:
        for item in boundaries:
            print(
                f"- `{item['source_id']}` unresolved/resource "
                f"{item['relation']} `{item['value']}` ({item['reason']})"
            )
    else:
        print("- No unresolved/resource boundaries among included documents.")
    print()
    print("## Return protocol")
    print()
    print("- Re-run `docsystem validate PROJECT` before handing work back.")
    print("- Re-run `docsystem changes PROJECT` after writing a projection.")
    print("- File adopter reports with `docsystem report draft` when reusable gaps remain.")
    return 0


def dependencies(project_root: Path, document_id: str, *, reverse: bool = False) -> int:
    try:
        config = load_config(project_root)
        markdown_catalog = build_catalog(config)
        document = find_document(markdown_catalog, document_id)
        metadata_issues = validate_metadata(markdown_catalog)
        relevant_issues = (
            (*validate_membership(markdown_catalog), *metadata_issues)
            if reverse
            else tuple(
                issue for issue in metadata_issues if issue.path == document.path
            )
        )
        graph_errors = [
            issue
            for issue in relevant_issues
            if issue.affects_graph and issue.severity != "warning"
        ]
        for issue in relevant_issues:
            related_warning = (
                issue.severity == "warning"
                and (not reverse or issue.target_id == document_id)
            )
            if related_warning or issue in graph_errors:
                level = "WARNING" if issue.severity == "warning" else "ERROR"
                print(
                    f"{level}: {issue.path.as_posix()}: {issue.message}",
                    file=sys.stderr,
                )
        if graph_errors:
            return 1
        graph = build_dependency_graph(markdown_catalog)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    edges = graph.incoming(document_id) if reverse else graph.outgoing(document_id)
    for edge in sorted(
        edges,
        key=lambda item: (
            item.relation,
            item.source_id if reverse else item.target_id,
        ),
    ):
        peer = edge.source_id if reverse else edge.target_id
        expected_revision = (
            str(edge.expected_revision)
            if edge.expected_revision is not None
            else "-"
        )
        print(f"{edge.relation}\t{peer}\t{expected_revision}")
    return 0


# Text column order for `references`: kind, relation, authority, origin,
# distance, class (direct/transitive), address, path, reason. `kind` is
# "edge" for a traversal result or "boundary" for a visible unresolved
# target; unused columns for a boundary row are "-".
_REFERENCES_COLUMNS = (
    "kind",
    "relation",
    "authority",
    "origin",
    "distance",
    "class",
    "address",
    "path",
    "reason",
)


def _references_result_row(result: TraversalResult) -> str:
    path = " -> ".join(address.text for address in result.path.addresses)
    return "\t".join(
        (
            "edge",
            result.relation,
            result.authority,
            result.origin,
            str(result.distance),
            "direct" if result.direct else "transitive",
            result.address.text,
            path,
            result.reason or "-",
        )
    )


def _references_boundary_row(boundary: Boundary) -> str:
    return "\t".join(
        (
            "boundary",
            "-",
            "-",
            "-",
            "-",
            "-",
            boundary.source.text,
            boundary.raw_target,
            f"{boundary.category}: {boundary.reason}",
        )
    )


def _references_result_json(result: TraversalResult) -> dict[str, object]:
    return {
        "address": result.address.text,
        "relation": result.relation,
        "authority": result.authority,
        "origin": result.origin,
        "distance": result.distance,
        "direct": result.direct,
        "path": [address.text for address in result.path.addresses],
        "reason": result.reason,
    }


def _references_boundary_json(boundary: Boundary) -> dict[str, object]:
    return {
        "source": boundary.source.text,
        "raw_target": boundary.raw_target,
        "category": boundary.category,
        "reason": boundary.reason,
    }


def _references_direct(
    config: ProjectConfig, address: Address, *, reverse: bool, transitive: bool
) -> _ReferencesOutput | None:
    """Resolve entirely from Markdown: the fallback and no-projection path."""

    markdown_catalog = build_catalog(config)
    try:
        document = find_document(markdown_catalog, address.document_id)
    except ValueError:
        return None
    if address.anchor is not None and address.anchor not in {
        section.anchor for section in document.sections
    }:
        return None
    all_graph_issues = (
        *validate_membership(markdown_catalog),
        *validate_metadata(markdown_catalog),
        *validate_adoption(markdown_catalog, config),
    )
    relevant_issues = (
        all_graph_issues
        if reverse or transitive
        else tuple(
            issue
            for issue in all_graph_issues
            if issue.path == document.path
        )
    )
    blockers = tuple(
        issue
        for issue in relevant_issues
        if issue.affects_graph and issue.severity != "warning"
    )
    if blockers:
        raise _ReferenceGraphInvalid(blockers)
    reference_graph = build_reference_graph(markdown_catalog, config)
    results = reference_graph.traverse(address, reverse=reverse, transitive=transitive)
    boundary_sources = (
        ()
        if reverse
        else (address, *(result.address for result in results if transitive))
    )
    boundaries = tuple(
        sorted(
            (
                boundary
                for source in boundary_sources
                for boundary in reference_graph.boundaries_from(source)
            ),
            key=lambda item: (item.source.text, item.category, item.raw_target),
        )
    )
    observed = "unknown" if reverse else ("bounded" if boundaries else "complete")
    return _ReferencesOutput(results, boundaries, observed)


def _references_projected(
    config: ProjectConfig, address: Address, *, reverse: bool, transitive: bool
) -> _ReferencesOutput | None:
    """Resolve using only the shards the query touches, or raise/return None.

    Returns `None` for a genuinely unknown ID/anchor (proven by manifest
    membership, which `open_targeted_projection` already bound to current
    source freshness). Raises `ProjectionUnavailable` when a *touched* shard
    fails verification, so the caller can fall back to `_references_direct`
    for the whole query instead of reporting a partial result.
    """

    accessor, reason = open_targeted_projection(config)
    if accessor is None:
        raise ProjectionUnavailable(reason)
    if address.document_id not in accessor.manifest.get("documents", {}):
        return None
    if address.anchor is not None:
        document_shard = accessor.document(address.document_id)
        if document_shard is None:
            raise ProjectionUnavailable(f"document shard unavailable for {address.document_id}")
        if address.anchor not in document_shard.get("sections", {}):
            return None
    observed_boundaries: dict[tuple[str, str, str], Boundary] = {}

    def _forward(current: Address) -> tuple:
        edges = targeted_forward_edges(accessor, current)
        if edges is None:
            raise ProjectionUnavailable(f"references shard unavailable for {current.text}")
        for item in edges[1]:
            boundary = Boundary(
                current, item["raw_target"], item["category"], item["reason"]
            )
            observed_boundaries[
                (boundary.source.text, boundary.category, boundary.raw_target)
            ] = boundary
        return edges[0]

    def _reverse_edges(current: Address) -> tuple:
        edges = targeted_reverse_edges(accessor, current)
        if edges is None:
            raise ProjectionUnavailable(f"reverse shard unavailable for {current.text}")
        return edges

    results = graph_traverse(
        address, forward=_forward, reverse_edges=_reverse_edges, reverse=reverse,
        transitive=transitive,
    )
    boundaries = tuple(
        sorted(
            observed_boundaries.values(),
            key=lambda item: (item.source.text, item.category, item.raw_target),
        )
    )
    observed = "unknown" if reverse else ("bounded" if boundaries else "complete")
    return _ReferencesOutput(results, boundaries, observed)


def references(
    project_root: Path,
    raw_address: str,
    *,
    reverse: bool = False,
    transitive: bool = False,
    json_output: bool = False,
) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    try:
        address = parse_address(raw_address)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    diagnostic: str | None = None
    output = None
    try:
        output = _references_projected(
            config, address, reverse=reverse, transitive=transitive
        )
    except ProjectionUnavailable as error:
        diagnostic = f"{error}; using direct Markdown"
        try:
            output = _references_direct(
                config, address, reverse=reverse, transitive=transitive
            )
        except _ReferenceGraphInvalid as invalid:
            for issue in invalid.issues:
                print(
                    f"ERROR: {issue.path.as_posix()}: {issue.message}",
                    file=sys.stderr,
                )
            return 1

    if output is None:
        print(f"ERROR: unknown graph address: {address.text}", file=sys.stderr)
        return 1

    if diagnostic is not None:
        print(f"NOTE: {diagnostic}", file=sys.stderr)

    results, boundaries = output.results, output.boundaries
    if json_output:
        _print_json(
            {
                "address": address.text,
                "reverse": reverse,
                "transitive": transitive,
                "results": [_references_result_json(result) for result in results],
                "boundaries": [_references_boundary_json(boundary) for boundary in boundaries],
                "completeness": {
                    "authored": "complete",
                    "observed": output.observed_completeness,
                },
            }
        )
        return 0
    for result in results:
        print(_references_result_row(result))
    for boundary in boundaries:
        print(_references_boundary_row(boundary))
    return 0


# Text column order for `change-plan`: kind, address, disposition, scope,
# relation, authority, origin, distance, class, path, reason. `kind` is
# "item" for one plan-item reason, "boundary" for a visible unresolved
# target, or "completeness" for one graph-layer state; unused columns for a
# "boundary" or "completeness" row are "-". A "completeness" row reuses
# `address` for the layer name (`authored`/`observed`/`generated`) and
# `reason` for its `complete`/`bounded`/`unknown`/`not-enumerated` state.
_CHANGE_PLAN_COLUMNS = (
    "kind",
    "address",
    "disposition",
    "scope",
    "relation",
    "authority",
    "origin",
    "distance",
    "class",
    "path",
    "reason",
)


def _change_plan_reason_class(reason: InclusionReason) -> str:
    if reason.scope == "target":
        return "target"
    return "direct" if reason.direct else "transitive"


def _change_plan_item_rows(item: PlanItem) -> tuple[str, ...]:
    rows = []
    for reason in item.reasons:
        path = " -> ".join(step.text for step in reason.path)
        rows.append(
            "\t".join(
                (
                    "item",
                    item.address.text,
                    item.disposition,
                    reason.scope,
                    reason.relation,
                    reason.authority,
                    reason.origin,
                    str(reason.distance),
                    _change_plan_reason_class(reason),
                    path,
                    reason.detail or "-",
                )
            )
        )
    return tuple(rows)


def _change_plan_boundary_row(boundary: Boundary) -> str:
    return "\t".join(
        (
            "boundary",
            boundary.source.text,
            "-",
            "-",
            "-",
            "-",
            "-",
            "-",
            "-",
            boundary.raw_target,
            f"{boundary.category}: {boundary.reason}",
        )
    )


def _change_plan_completeness_rows(completeness: Completeness) -> tuple[str, ...]:
    return tuple(
        "\t".join(("completeness", layer, "-", "-", "-", "-", "-", "-", "-", "-", state))
        for layer, state in (
            ("authored", completeness.authored),
            ("observed", completeness.observed),
            ("generated", completeness.generated),
        )
    )


def _change_plan_reason_json(reason: InclusionReason) -> dict[str, object]:
    return {
        "scope": reason.scope,
        "relation": reason.relation,
        "authority": reason.authority,
        "origin": reason.origin,
        "distance": reason.distance,
        "direct": reason.direct,
        "path": [step.text for step in reason.path],
        "detail": reason.detail,
    }


def _change_plan_item_json(item: PlanItem) -> dict[str, object]:
    return {
        "address": item.address.text,
        "disposition": item.disposition,
        "reasons": [_change_plan_reason_json(reason) for reason in item.reasons],
    }


def _change_plan_direct(
    config: ProjectConfig, address: Address, *, reverse: bool, transitive: bool
) -> ChangePlan | None:
    """Resolve entirely from Markdown: the fallback and no-projection path."""

    markdown_catalog = build_catalog(config)
    try:
        document = find_document(markdown_catalog, address.document_id)
    except ValueError:
        return None
    if address.anchor is not None and address.anchor not in {
        section.anchor for section in document.sections
    }:
        return None
    all_graph_issues = (
        *validate_membership(markdown_catalog),
        *validate_metadata(markdown_catalog),
        *validate_adoption(markdown_catalog, config),
    )
    blockers = tuple(
        issue
        for issue in all_graph_issues
        if issue.affects_graph and issue.severity != "warning"
    )
    if blockers:
        raise _ReferenceGraphInvalid(blockers)
    reference_graph = build_reference_graph(markdown_catalog, config)

    def _forward(current: Address) -> tuple[GraphEdge, ...]:
        return reference_graph.forward(current)

    def _reverse(current: Address) -> tuple[GraphEdge, ...]:
        return reference_graph.reverse_edges(current)

    forward_reasons = graph_traverse_reasons(
        address, forward=_forward, reverse_edges=_reverse, reverse=False, transitive=transitive
    )
    reverse_reasons = (
        graph_traverse_reasons(
            address, forward=_forward, reverse_edges=_reverse, reverse=True, transitive=transitive
        )
        if reverse
        else ()
    )
    touched = (
        (address,)
        if not transitive
        else (address, *dict.fromkeys(result.address for result in forward_reasons))
    )
    boundaries = tuple(
        boundary for source in touched for boundary in reference_graph.boundaries_from(source)
    )
    return build_change_plan(
        address,
        reverse=reverse,
        transitive=transitive,
        forward_reasons=forward_reasons,
        reverse_reasons=reverse_reasons,
        boundaries=boundaries,
    )


def _change_plan_projected(
    config: ProjectConfig, address: Address, *, reverse: bool, transitive: bool
) -> ChangePlan | None:
    """Resolve using only the shards the query touches, or raise/return None."""

    accessor, reason = open_targeted_projection(config)
    if accessor is None:
        raise ProjectionUnavailable(reason)
    if address.document_id not in accessor.manifest.get("documents", {}):
        return None
    if address.anchor is not None:
        document_shard = accessor.document(address.document_id)
        if document_shard is None:
            raise ProjectionUnavailable(f"document shard unavailable for {address.document_id}")
        if address.anchor not in document_shard.get("sections", {}):
            return None
    observed_boundaries: dict[tuple[str, str, str], Boundary] = {}

    def _forward(current: Address) -> tuple[GraphEdge, ...]:
        edges = targeted_forward_edges(accessor, current)
        if edges is None:
            raise ProjectionUnavailable(f"references shard unavailable for {current.text}")
        for item in edges[1]:
            boundary = Boundary(current, item["raw_target"], item["category"], item["reason"])
            observed_boundaries[
                (boundary.source.text, boundary.category, boundary.raw_target)
            ] = boundary
        return edges[0]

    def _reverse(current: Address) -> tuple[GraphEdge, ...]:
        edges = targeted_reverse_edges(accessor, current)
        if edges is None:
            raise ProjectionUnavailable(f"reverse shard unavailable for {current.text}")
        return edges

    forward_reasons = graph_traverse_reasons(
        address, forward=_forward, reverse_edges=_reverse, reverse=False, transitive=transitive
    )
    reverse_reasons = (
        graph_traverse_reasons(
            address, forward=_forward, reverse_edges=_reverse, reverse=True, transitive=transitive
        )
        if reverse
        else ()
    )
    boundaries = tuple(observed_boundaries.values())
    return build_change_plan(
        address,
        reverse=reverse,
        transitive=transitive,
        forward_reasons=forward_reasons,
        reverse_reasons=reverse_reasons,
        boundaries=boundaries,
    )


def change_plan(
    project_root: Path,
    raw_address: str,
    *,
    reverse: bool = False,
    transitive: bool = False,
    json_output: bool = False,
) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    try:
        address = parse_address(raw_address)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    diagnostic: str | None = None
    plan: ChangePlan | None = None
    try:
        plan = _change_plan_projected(config, address, reverse=reverse, transitive=transitive)
    except ProjectionUnavailable as error:
        diagnostic = f"{error}; using direct Markdown"
        try:
            plan = _change_plan_direct(config, address, reverse=reverse, transitive=transitive)
        except _ReferenceGraphInvalid as invalid:
            for issue in invalid.issues:
                print(
                    f"ERROR: {issue.path.as_posix()}: {issue.message}",
                    file=sys.stderr,
                )
            return 1

    if plan is None:
        print(f"ERROR: unknown graph address: {address.text}", file=sys.stderr)
        return 1

    if diagnostic is not None:
        print(f"NOTE: {diagnostic}", file=sys.stderr)

    if json_output:
        _print_json(
            {
                "address": address.text,
                "reverse": reverse,
                "transitive": transitive,
                "items": [_change_plan_item_json(item) for item in plan.items],
                "boundaries": [
                    _references_boundary_json(boundary) for boundary in plan.boundaries
                ],
                "completeness": {
                    "authored": plan.completeness.authored,
                    "observed": plan.completeness.observed,
                    "generated": plan.completeness.generated,
                },
            }
        )
        return 0
    for item in plan.items:
        for row in _change_plan_item_rows(item):
            print(row)
    for boundary in plan.boundaries:
        print(_change_plan_boundary_row(boundary))
    for row in _change_plan_completeness_rows(plan.completeness):
        print(row)
    return 0


@dataclass(frozen=True)
class _MaintenanceOccurrenceResult:
    """One resolved occurrence: excluded evidence, or an eligible current block."""

    document_id: str
    anchor: str
    role: str
    eligible: bool
    disposition: str  # "clean" | "drifted" | "excluded"
    reason: str | None
    section_range: tuple[int, int]
    marker_range: tuple[int, int] | None
    content_range: tuple[int, int] | None
    document_hash: str
    section_hash: str
    block_hash: str | None
    diff: str | None


def _maintenance_find_target(config: ProjectConfig, name: str):
    for target in config.maintenance_targets:
        if target.name == name:
            return target
    return None


def _maintenance_occurrence_row(result: _MaintenanceOccurrenceResult) -> str:
    return "\t".join(
        (
            "occurrence",
            f"{result.document_id}#{result.anchor}",
            result.role,
            result.disposition,
            result.reason or "-",
            f"section={result.section_range[0]}-{result.section_range[1]}",
            (
                f"content={result.content_range[0]}-{result.content_range[1]}"
                if result.content_range is not None
                else "content=-"
            ),
            f"document_hash={result.document_hash}",
            f"section_hash={result.section_hash}",
            f"block_hash={result.block_hash or '-'}",
        )
    )


def _maintenance_occurrence_json(result: _MaintenanceOccurrenceResult) -> dict[str, object]:
    return {
        "address": f"{result.document_id}#{result.anchor}",
        "role": result.role,
        "eligible": result.eligible,
        "disposition": result.disposition,
        "reason": result.reason,
        "section_range": {
            "start_line": result.section_range[0],
            "end_line": result.section_range[1],
        },
        "marker_range": (
            {
                "start_line": result.marker_range[0],
                "end_line": result.marker_range[1],
            }
            if result.marker_range is not None
            else None
        ),
        "content_range": (
            {
                "start_line": result.content_range[0],
                "end_line": result.content_range[1],
            }
            if result.content_range is not None
            else None
        ),
        "document_hash": result.document_hash,
        "section_hash": result.section_hash,
        "block_hash": result.block_hash,
        "diff": result.diff,
    }


def maintenance(
    project_root: Path,
    target_name: str,
    *,
    check: bool,
    preview: bool,
    json_output: bool = False,
    expected_source_hash: str | None = None,
) -> int:
    """Read-only managed-block drift check/preview for one declared target.

    `--check` reports the same deterministic result as `--preview` but exits
    `2` on drift so it composes as a CI gate; `--preview` always exits `0` for
    a valid target. Neither ever writes Markdown: this milestone has no
    `--write`/`--apply` variant. Invalid config, an unknown target, unknown or
    ambiguous document/section/marker addresses, and graph-blocking errors
    fail closed with exit `1`, diagnostics on stderr only and no stdout.
    """

    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    target = _maintenance_find_target(config, target_name)
    if target is None:
        print(f"ERROR: unknown maintenance target: {target_name}", file=sys.stderr)
        return 1

    involved_ids = {
        target.source_document_id,
        *(occurrence.document_id for occurrence in target.occurrences),
    }
    try:
        views, _, catalog_value = _load_views(config)
        if catalog_value is not None:
            by_id = {
                document.metadata.document_id: document
                for document in catalog_value.documents
                if document.metadata is not None
            }
            for document_id in involved_ids:
                if document_id not in by_id:
                    raise ValueError(f"document ID not found: {document_id}")
            blockers = [
                issue
                for issue in (
                    *validate_membership(catalog_value),
                    *validate_metadata(catalog_value),
                    *validate_adoption(catalog_value, config),
                )
                if issue.affects_graph and issue.severity != "warning"
            ]
            for document_id in involved_ids:
                document = by_id[document_id]
                blockers.extend(
                    ValidationIssue(document.path, message)
                    for message in document_section_issues(document, config)
                )
            if blockers:
                for issue in blockers:
                    print(
                        f"ERROR: {issue.path.as_posix()}: {issue.message}",
                        file=sys.stderr,
                    )
                return 1
        for document_id in involved_ids:
            if document_id not in views:
                raise ValueError(f"document ID not found: {document_id}")

        source_view = views[target.source_document_id]
        source_section = next(
            (
                section
                for section in source_view.sections
                if section.anchor == target.source_anchor
            ),
            None,
        )
        if source_section is None:
            raise ValueError(
                f"anchor not found in {target.source_document_id}: "
                f"{target.source_anchor}"
            )
        occurrence_sections: dict[int, MarkdownSection] = {}
        for index, occurrence in enumerate(target.occurrences):
            occurrence_view = views[occurrence.document_id]
            section = next(
                (
                    item
                    for item in occurrence_view.sections
                    if item.anchor == occurrence.anchor
                ),
                None,
            )
            if section is None:
                raise ValueError(
                    f"anchor not found in {occurrence.document_id}: "
                    f"{occurrence.anchor}"
                )
            occurrence_sections[index] = section
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    source_scan = scan_markers(source_view.content, target.name)
    source_span, source_issues = resolve_marker(source_scan, SOURCE)
    if source_span is None:
        for issue in source_issues:
            print(f"ERROR: {target.source_document_id}: {issue}", file=sys.stderr)
        return 1
    if not span_within_section(source_span, source_section):
        print(
            f"ERROR: {target.source_document_id}: source marker for target "
            f"{target.name!r} is outside declared section #{target.source_anchor}",
            file=sys.stderr,
        )
        return 1

    source_block = block_text(source_view.content, source_span)
    source_document_hash = sha256_text(source_view.content)
    source_section_hash = sha256_text(extract_section(source_view.content, source_section))
    source_block_hash = sha256_text(source_block)
    if expected_source_hash is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_source_hash):
            print(
                "ERROR: --expect-source-hash must be a lowercase SHA-256 value",
                file=sys.stderr,
            )
            return 1
        if expected_source_hash != source_block_hash:
            print(
                "ERROR: source block hash changed: expected "
                f"{expected_source_hash}, current {source_block_hash}",
                file=sys.stderr,
            )
            return 1
    source_marker_range = (source_span.start_line, source_span.end_line)
    source_content_range = (source_span.start_line + 1, source_span.end_line - 1)

    occurrence_results: list[_MaintenanceOccurrenceResult] = []
    any_drift = False
    for index, occurrence in enumerate(target.occurrences):
        occurrence_view = views[occurrence.document_id]
        occurrence_section = occurrence_sections[index]
        occurrence_document_hash = sha256_text(occurrence_view.content)
        occurrence_section_hash = sha256_text(
            extract_section(occurrence_view.content, occurrence_section)
        )
        if occurrence.role != CURRENT:
            occurrence_results.append(
                _MaintenanceOccurrenceResult(
                    document_id=occurrence.document_id,
                    anchor=occurrence.anchor,
                    role=occurrence.role,
                    eligible=False,
                    disposition=EXCLUDED,
                    reason=f"role is {occurrence.role!r}, not eligible for preview",
                    section_range=(
                        occurrence_section.start_line,
                        occurrence_section.end_line,
                    ),
                    marker_range=None,
                    content_range=None,
                    document_hash=occurrence_document_hash,
                    section_hash=occurrence_section_hash,
                    block_hash=None,
                    diff=None,
                )
            )
            continue
        occurrence_scan = scan_markers(occurrence_view.content, target.name)
        occurrence_span, occurrence_issues = resolve_marker(occurrence_scan, MANAGED)
        if occurrence_span is None:
            for issue in occurrence_issues:
                print(f"ERROR: {occurrence.document_id}: {issue}", file=sys.stderr)
            return 1
        if not span_within_section(occurrence_span, occurrence_section):
            print(
                f"ERROR: {occurrence.document_id}: managed marker for target "
                f"{target.name!r} is outside declared section #{occurrence.anchor}",
                file=sys.stderr,
            )
            return 1
        occurrence_block = block_text(occurrence_view.content, occurrence_span)
        occurrence_block_hash = sha256_text(occurrence_block)
        drifted = occurrence_block != source_block
        diff_text = None
        if drifted:
            any_drift = True
            diff_text = unified_block_diff(
                before=block_lines(occurrence_view.content, occurrence_span),
                after=block_lines(source_view.content, source_span),
                from_label=f"{occurrence.document_id}#{occurrence.anchor}",
                to_label=f"{target.source_document_id}#{target.source_anchor}",
            )
        occurrence_results.append(
            _MaintenanceOccurrenceResult(
                document_id=occurrence.document_id,
                anchor=occurrence.anchor,
                role=occurrence.role,
                eligible=True,
                disposition=DRIFTED if drifted else CLEAN,
                reason=None,
                section_range=(
                    occurrence_section.start_line,
                    occurrence_section.end_line,
                ),
                marker_range=(occurrence_span.start_line, occurrence_span.end_line),
                content_range=(
                    occurrence_span.start_line + 1,
                    occurrence_span.end_line - 1,
                ),
                document_hash=occurrence_document_hash,
                section_hash=occurrence_section_hash,
                block_hash=occurrence_block_hash,
                diff=diff_text,
            )
        )

    status = DRIFTED if any_drift else CLEAN

    source_address = Address(target.source_document_id, target.source_anchor)
    plan_diagnostic: str | None = None
    plan: ChangePlan | None = None
    try:
        if catalog_value is not None:
            plan = _change_plan_direct(
                config, source_address, reverse=True, transitive=False
            )
        else:
            plan = _change_plan_projected(
                config, source_address, reverse=True, transitive=False
            )
    except ProjectionUnavailable as error:
        plan_diagnostic = f"{error}; using direct Markdown"
        try:
            plan = _change_plan_direct(config, source_address, reverse=True, transitive=False)
        except _ReferenceGraphInvalid as invalid:
            for issue in invalid.issues:
                print(
                    f"ERROR: {issue.path.as_posix()}: {issue.message}",
                    file=sys.stderr,
                )
            return 1
    if plan is None:
        print(f"ERROR: unknown graph address: {source_address.text}", file=sys.stderr)
        return 1
    if plan_diagnostic is not None:
        print(f"NOTE: {plan_diagnostic}", file=sys.stderr)

    if json_output:
        _print_json(
            {
                "target": target.name,
                "mode": "check" if check else "preview",
                "status": status,
                "source": {
                    "address": source_address.text,
                    "document_hash": source_document_hash,
                    "section_hash": source_section_hash,
                    "block_hash": source_block_hash,
                    "section_range": {
                        "start_line": source_section.start_line,
                        "end_line": source_section.end_line,
                    },
                    "marker_range": {
                        "start_line": source_marker_range[0],
                        "end_line": source_marker_range[1],
                    },
                    "content_range": {
                        "start_line": source_content_range[0],
                        "end_line": source_content_range[1],
                    },
                },
                "occurrences": [
                    _maintenance_occurrence_json(result) for result in occurrence_results
                ],
                "change_plan": {
                    "address": plan.address.text,
                    "items": [_change_plan_item_json(item) for item in plan.items],
                    "boundaries": [
                        _references_boundary_json(boundary)
                        for boundary in plan.boundaries
                    ],
                    "completeness": {
                        "authored": plan.completeness.authored,
                        "observed": plan.completeness.observed,
                        "generated": plan.completeness.generated,
                    },
                },
            }
        )
    else:
        print(f"target\t{target.name}\tstatus\t{status}")
        print(
            "source\t"
            f"{source_address.text}\t"
            f"document_hash={source_document_hash},"
            f"section_hash={source_section_hash},"
            f"block_hash={source_block_hash},"
            f"section={source_section.start_line}-{source_section.end_line},"
            f"content={source_content_range[0]}-{source_content_range[1]}"
        )
        for result in occurrence_results:
            print(_maintenance_occurrence_row(result))
        for item in plan.items:
            for row in _change_plan_item_rows(item):
                print(f"changeplan\t{row}")
        for boundary in plan.boundaries:
            print(f"changeplan\t{_change_plan_boundary_row(boundary)}")
        for row in _change_plan_completeness_rows(plan.completeness):
            print(f"changeplan\t{row}")
        for result in occurrence_results:
            if result.diff:
                print()
                print(f"## diff {result.document_id}#{result.anchor}")
                print()
                sys.stdout.write(result.diff)

    if check:
        return 2 if status == DRIFTED else 0
    return 0


def show_config(project_root: Path) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"documentation_root={config.documentation_root}")
    print(f"language={config.language}")
    for role, path in sorted(config.areas.items()):
        print(f"area.{role}={path}")
    for role, prefix in sorted(config.identifiers.items()):
        print(f"identifier.{role}={prefix}")
    for pattern in config.catalog_exclusions:
        print(f"catalog.exclude={pattern}")
    for anchor in config.navigation_extend_through:
        print(f"navigation.extend_through={anchor}")
    print(f"relations.legacy_paths={config.legacy_relation_mode}")
    for document_type in config.snapshot_document_types:
        print(f"relations.snapshot_type={document_type}")
    print(f"projection.format={config.projection_format}")
    print(f"projection.keep_generations={config.keep_generations}")
    return 0


def _agent_instructions_text(selection: _Selection, config: ProjectConfig) -> str:
    doc_root = config.documentation_root.relative_to(config.project_root).as_posix()
    out: list[str] = []
    out.append("## Documentation with Documentation Engine")
    out.append("")
    out.append(
        "This project uses `docsystem` for structured Markdown documentation "
        f"rooted at `{doc_root}` (language: {config.language})."
    )
    out.append("")
    out.append("Configured areas and identifier namespaces:")
    out.append("")
    for role, path in sorted(config.areas.items()):
        out.append(f"- {role} -> {path.as_posix()}")
    for role, prefix in sorted(config.identifiers.items()):
        out.append(f"- {prefix} ({role})")
    out.append("")
    out.append("Agent rules:")
    out.append("")
    out.append(
        "- Always pass the project root explicitly; do not rely on the "
        "current working directory matching the intended project."
    )
    out.append(
        "- Start read-only with `docsystem readiness "
        f"{selection.selector} --json` and follow its `next_command` field."
    )
    out.append(
        "- Prefer `--json` on commands that support it instead of parsing "
        "human-readable text output."
    )
    out.append(
        "- Expand context with `--depth`, `--include` or `--include-related` "
        "instead of assuming an omitted document or section is irrelevant."
    )
    out.append(
        "- Never run `docsystem init`, `docsystem migrate --apply` or "
        "`docsystem index --write` without explicit approval."
    )
    out.append(
        "- Before mutating ignored/local-only documentation state, follow "
        "this project's local backup policy if one exists."
    )
    out.append("")
    out.append(
        "See `docs/agent-contract.md` in the Documentation Engine repository "
        "for the full agent contract."
    )
    return "\n".join(out) + "\n"


def agent_instructions(
    project_root: Path,
    *,
    json_output: bool = False,
    selection: _Selection | None = None,
) -> int:
    """Print a deterministic agent-rules snippet for AGENTS.md/CLAUDE.md.

    Read-only: the snippet is derived from the project's `.docsystem.toml`
    plus the engine's stable agent contract, never from parsing
    `docs/setup-guide.md`, so a pasted snippet cannot silently drift from the
    project's actually configured areas and identifiers. Works even when the
    documentation root itself is missing, since only configuration is read.

    Under a selected source the snippet addresses the project as
    `--source NAME`, so a snippet pasted into a committed AGENTS.md never
    carries the private workspace path it was generated from.
    """
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    text = _agent_instructions_text(selection or _Selection(project_root), config)
    if json_output:
        _print_json({"text": text})
        return 0
    sys.stdout.write(text)
    return 0


def _add_source_options(
    command_parser: argparse.ArgumentParser, *, short_flag: bool = True
) -> None:
    """Add the workspace selection flags shared by every project command.

    `--workspace-source` is the spelling every project command accepts.
    `--source` is the short alias for the same option, added everywhere it is
    free; `report draft` already owns `--source` for the reporting host, whose
    existing meaning is preserved rather than overloaded.
    """

    names = ["--source", "--workspace-source"] if short_flag else ["--workspace-source"]
    command_parser.add_argument(
        *names,
        dest="workspace_source",
        metavar="NAME",
        help=(
            "Run against the named source from the local workspace registry "
            "instead of the positional project root."
        ),
    )
    command_parser.add_argument(
        "--workspace",
        metavar="PATH",
        type=Path,
        help=(
            "Workspace root holding workspace.toml. Overrides "
            "DOCSYSTEM_WORKSPACE and .docsystem.local.toml."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("init", "Create configuration and the documentation root."),
        ("doctor", "Validate project configuration and filesystem state."),
        ("show-config", "Print the normalized project configuration."),
        ("catalog", "List Markdown files and their logical area roles."),
        ("validate", "Validate hierarchical Markdown navigation."),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
        if command != "init":
            _add_source_options(command_parser)
        if command == "catalog":
            command_parser.add_argument(
                "--explain",
                action="store_true",
                help="Classify every Markdown source under the documentation root.",
            )
            command_parser.add_argument(
                "--json",
                action="store_true",
                dest="json_output",
                help="Print a deterministic JSON object instead of tab-separated text.",
            )
        if command in {"doctor", "validate"}:
            command_parser.add_argument(
                "--verbose-adoption",
                action="store_true",
                help="Print every legacy adoption warning instead of summaries.",
            )

    read_parser = subparsers.add_parser(
        "read", help="Read a Markdown document or section by stable ID."
    )
    read_parser.add_argument("document_id")
    read_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    selection = read_parser.add_mutually_exclusive_group()
    selection.add_argument("--anchor")
    selection.add_argument("--navigation", action="store_true")
    selection.add_argument("--list", action="store_true", dest="list_sections")

    dependencies_parser = subparsers.add_parser(
        "dependencies", help="List forward or reverse semantic dependencies."
    )
    dependencies_parser.add_argument("document_id")
    dependencies_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    dependencies_parser.add_argument("--reverse", action="store_true")

    references_parser = subparsers.add_parser(
        "references",
        help="Read-only forward/reverse section-and-reference graph inspection.",
    )
    references_parser.add_argument("address", metavar="ID[#anchor]")
    references_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    references_parser.add_argument("--reverse", action="store_true")
    references_parser.add_argument("--transitive", action="store_true")
    references_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )

    change_plan_parser = subparsers.add_parser(
        "change-plan",
        help=(
            "Read-only explainable change plan: read/review items, "
            "aggregated reasons and boundaries for a document or section."
        ),
    )
    change_plan_parser.add_argument("address", metavar="ID[#anchor]")
    change_plan_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    change_plan_parser.add_argument("--reverse", action="store_true")
    change_plan_parser.add_argument("--transitive", action="store_true")
    change_plan_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )

    maintenance_parser = subparsers.add_parser(
        "maintenance",
        help="Read-only managed maintenance drift check and preview diff.",
    )
    maintenance_parser.add_argument("target", metavar="TARGET")
    maintenance_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    maintenance_mode = maintenance_parser.add_mutually_exclusive_group(required=True)
    maintenance_mode.add_argument("--check", action="store_true")
    maintenance_mode.add_argument("--preview", action="store_true")
    maintenance_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )
    maintenance_parser.add_argument(
        "--expect-source-hash",
        metavar="SHA256",
        help="Fail closed unless the canonical source block still has this hash.",
    )

    context_parser = subparsers.add_parser("context", help="Build an inspectable context packet.")
    context_parser.add_argument("document_id")
    context_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    context_parser.add_argument("--anchor")
    context_parser.add_argument("--depth", type=int, choices=range(0, 6), default=1)
    context_parser.add_argument("--include-related", action="store_true")
    context_parser.add_argument("--include", action="append", default=[])
    context_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of the Markdown packet.",
    )
    context_parser.add_argument(
        "--outline",
        action="store_true",
        help=(
            "Print section size maps instead of content; combine with --json "
            "for the structured form. Cannot combine with --anchor or --include."
        ),
    )
    context_parser.add_argument(
        "--assume-known",
        action="append",
        default=[],
        dest="assume_known",
        metavar="ID@REV",
        help=(
            "Declare a document already held at revision REV (repeatable). When "
            "it enters the packet at that exact revision its content is omitted; "
            "a revision mismatch includes content with a diagnostics note."
        ),
    )
    context_parser.add_argument(
        "--since",
        metavar="GENERATION",
        help=(
            "Emit a delta packet against a retained projection generation "
            "(full hash or unique >=12-char prefix): unchanged documents are "
            "omitted, changed documents add their changed sections."
        ),
    )

    impact_parser = subparsers.add_parser("impact", help="Show reverse metadata impact.")
    impact_parser.add_argument("document_id")
    impact_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())

    migration_report_parser = subparsers.add_parser(
        "migration-report", help="Report legacy relation adoption mappings."
    )
    migration_report_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    migration_report_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Preview (default) or apply resolvable legacy relation migrations.",
    )
    migrate_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    migrate_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write resolved values into Markdown source. Default is a "
        "non-mutating preview.",
    )

    readiness_parser = subparsers.add_parser(
        "readiness", help="Report adoption readiness without writing source."
    )
    readiness_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    readiness_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of a text summary.",
    )

    index_parser = subparsers.add_parser("index", help="Check or write the projection.")
    index_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    index_parser.add_argument("--write", action="store_true")

    changes_parser = subparsers.add_parser("changes", help="Show changes since projection.")
    changes_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    changes_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )

    finish_parser = subparsers.add_parser(
        "finish",
        help="Build a compact handoff packet for returning work to a parent context.",
    )
    finish_parser.add_argument("document_id")
    finish_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    finish_parser.add_argument("--depth", type=int, choices=range(0, 6), default=1)
    finish_parser.add_argument("--include-related", action="store_true")
    finish_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of the Markdown packet.",
    )

    report_parser = subparsers.add_parser(
        "report",
        help="Create privacy-safe adopter report drafts.",
    )
    report_subparsers = report_parser.add_subparsers(
        dest="report_command", required=True
    )
    draft_parser = report_subparsers.add_parser(
        "draft",
        help="Draft a GitHub issue body from compact local diagnostics.",
    )
    draft_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    draft_parser.add_argument("--project-name", required=True)
    draft_parser.add_argument(
        "--type",
        required=True,
        choices=tuple(REPORT_TYPES),
        dest="report_type",
    )
    draft_parser.add_argument(
        "--source",
        required=True,
        choices=REPORT_SOURCES,
    )
    draft_parser.add_argument("--component")
    draft_parser.add_argument("--output", type=Path)

    agent_instructions_parser = subparsers.add_parser(
        "agent-instructions",
        help="Print a deterministic agent-rules snippet for AGENTS.md/CLAUDE.md.",
    )
    agent_instructions_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    agent_instructions_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of the Markdown snippet.",
    )

    workspace_parser = subparsers.add_parser(
        "workspace",
        help="Inspect the local workspace registry of selectable sources.",
    )
    workspace_subparsers = workspace_parser.add_subparsers(
        dest="workspace_command", required=True
    )
    for workspace_command, workspace_help in (
        ("list", "List registered sources, their visibility and availability."),
        ("doctor", "Report whether every registered source is selectable."),
    ):
        command_parser = workspace_subparsers.add_parser(
            workspace_command, help=workspace_help
        )
        command_parser.add_argument(
            "project", nargs="?", type=Path, default=Path.cwd()
        )
        command_parser.add_argument(
            "--workspace",
            metavar="PATH",
            type=Path,
            help=(
                "Workspace root holding workspace.toml. Overrides "
                "DOCSYSTEM_WORKSPACE and .docsystem.local.toml."
            ),
        )
        command_parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Print a deterministic JSON object instead of tab-separated text.",
        )

    for command_parser in (
        read_parser,
        dependencies_parser,
        references_parser,
        change_plan_parser,
        maintenance_parser,
        context_parser,
        impact_parser,
        migration_report_parser,
        migrate_parser,
        readiness_parser,
        index_parser,
        changes_parser,
        finish_parser,
        agent_instructions_parser,
    ):
        _add_source_options(command_parser)
    _add_source_options(draft_parser, short_flag=False)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "workspace":
        if args.workspace_command == "list":
            return workspace_list(
                args.project,
                workspace_option=args.workspace,
                json_output=args.json_output,
            )
        if args.workspace_command == "doctor":
            return workspace_doctor(
                args.project,
                workspace_option=args.workspace,
                json_output=args.json_output,
            )
        raise AssertionError(f"unknown workspace command: {args.workspace_command}")

    selection = _resolve_selection(args)
    if selection is None:
        return 1
    project = selection.project_root

    if args.command == "read":
        return read_document(
            project,
            args.document_id,
            anchor=args.anchor,
            navigation=args.navigation,
            list_sections=args.list_sections,
        )
    if args.command == "dependencies":
        return dependencies(project, args.document_id, reverse=args.reverse)
    if args.command == "references":
        return references(
            project,
            args.address,
            reverse=args.reverse,
            transitive=args.transitive,
            json_output=args.json_output,
        )
    if args.command == "change-plan":
        return change_plan(
            project,
            args.address,
            reverse=args.reverse,
            transitive=args.transitive,
            json_output=args.json_output,
        )
    if args.command == "maintenance":
        return maintenance(
            project,
            args.target,
            check=args.check,
            preview=args.preview,
            json_output=args.json_output,
            expected_source_hash=args.expect_source_hash,
        )
    if args.command == "catalog":
        return catalog(project, explain=args.explain, json_output=args.json_output)
    if args.command == "context":
        return context(
            project,
            args.document_id,
            anchor=args.anchor,
            depth=args.depth,
            include_related=args.include_related,
            includes=args.include,
            json_output=args.json_output,
            outline=args.outline,
            assume_known=args.assume_known,
            since=args.since,
        )
    if args.command == "impact":
        return impact(project, args.document_id)
    if args.command == "migration-report":
        return migration_report(project, json_output=args.json_output)
    if args.command == "migrate":
        return migrate(project, apply=args.apply)
    if args.command == "readiness":
        return readiness(
            project, json_output=args.json_output, selection=selection
        )
    if args.command == "index":
        return index_projection(project, write=args.write)
    if args.command == "changes":
        return changes(project, json_output=args.json_output)
    if args.command == "finish":
        return finish(
            project,
            args.document_id,
            depth=args.depth,
            include_related=args.include_related,
            json_output=args.json_output,
        )
    if args.command == "agent-instructions":
        return agent_instructions(
            project, json_output=args.json_output, selection=selection
        )
    if args.command == "report":
        if args.report_command == "draft":
            return report_draft(
                project,
                project_name=args.project_name,
                report_type=args.report_type,
                source=args.source,
                component=args.component,
                output=args.output,
                selection=selection,
            )
        raise AssertionError(f"unknown report command: {args.report_command}")
    if args.command == "doctor":
        return doctor(
            project, verbose_adoption=args.verbose_adoption
        )
    if args.command == "validate":
        return validate(
            project, verbose_adoption=args.verbose_adoption
        )
    handlers = {
        "init": initialize,
        "show-config": show_config,
    }
    return handlers[args.command](project)
