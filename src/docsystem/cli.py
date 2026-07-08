"""Command-line interface for Documentation Engine."""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

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
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config
from docsystem.migration import apply_migration_plan, build_migration_plan, validate_plan
from docsystem.projection import (
    LoadedProjection,
    build_projection,
    evaluate_changes,
    load_verified_projection,
    projection_status,
    write_projection,
)
from docsystem.projection import (
    changes as projection_changes,
)
from docsystem.readiness import evaluate_readiness
from docsystem.sections import MarkdownSection, extract_navigation, extract_section

# Version of every `--json` root object. Bump only on a breaking change to
# an existing field; adding new fields is compatible and does not bump it.
JSON_SCHEMA_VERSION = 1


def _print_json(payload: dict[str, object]) -> None:
    print(
        json.dumps(
            {"schema_version": JSON_SCHEMA_VERSION, **payload},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


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
    issues = validate_catalog(build_catalog(config), config)
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
    issues = validate_catalog(build_catalog(config), config)
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
) -> int:
    """Print the context packet as one structured JSON object.

    The JSON form carries the same selection, coverage and diagnostics data
    as the Markdown packet, but structured (typed lists instead of prose
    notes) so a machine client never parses packet text.
    """

    documents: list[dict[str, object]] = []
    explicit_count = 0
    omitted_count = 0
    for selected_id in ordered:
        view = views[selected_id]
        selected = selected_anchors[selected_id]
        explicit_sections = []
        for selected_anchor in dict.fromkeys(selected):
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
        omitted = [
            item.anchor
            for item in view.sections
            if item.level == 2
            and item.anchor not in config.navigation_extend_through
            and item.anchor not in selected
        ]
        omitted_count += len(omitted)
        documents.append(
            {
                "id": selected_id,
                "path": view.path.as_posix(),
                "relations": sorted(included[selected_id]),
                "navigation": extract_navigation(
                    view.content,
                    view.sections,
                    config.navigation_extend_through,
                ).rstrip(),
                "explicit_sections": explicit_sections,
                "omitted_h2": omitted,
            }
        )
    freshness: list[dict[str, object]] = []
    for selected_id in ordered:
        view = views[selected_id]
        for edge in view.outgoing:
            if edge.expected_revision is None:
                continue
            dependency = views.get(edge.peer_id)
            if dependency is not None and dependency.revision != edge.expected_revision:
                freshness.append(
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
    _print_json(
        {
            "target": document_id,
            "depth": depth,
            "include_related": include_related,
            "documents": documents,
            "freshness": freshness,
            "migrations": [
                {
                    "source_id": selected_id,
                    "relation": relation,
                    "value": value,
                    "target_id": target_id,
                }
                for selected_id in ordered
                for relation, value, target_id in views[selected_id].migrations
            ],
            "boundaries": [
                {
                    "source_id": selected_id,
                    "relation": relation,
                    "value": value,
                    "reason": reason,
                }
                for selected_id in ordered
                for relation, value, reason in views[selected_id].boundaries
            ],
            "related_omitted": (
                [] if include_related else list(views[document_id].related_values)
            ),
            "stats": {
                "included_documents": len(ordered),
                "explicit_sections": explicit_count,
                "omitted_h2_sections": omitted_count,
            },
        }
    )
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
) -> int:
    try:
        config = load_config(project_root)
        views, _, catalog_value = _load_views(config)
        if catalog_value is not None:
            find_document(catalog_value, document_id)
        elif document_id not in views:
            raise ValueError(f"document ID not found: {document_id}")
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

    ordered = [document_id, *sorted(item for item in included if item != document_id)]
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
        out.append("")
        out.append(f"## {selected_id} — {view.path.as_posix()}")
        out.append("")
        out.append(f"Relations: {', '.join(sorted(included[selected_id]))}.")
        out.append("")
        out.append(
            extract_navigation(
                view.content,
                view.sections,
                config.navigation_extend_through,
            ).rstrip()
        )
        selected = selected_anchors[selected_id]
        for selected_anchor in dict.fromkeys(selected):
            section = next(
                (
                    item
                    for item in view.sections
                    if item.anchor == selected_anchor
                ),
                None,
            )
            explicit_count += 1
            out.append("")
            out.append(f"### Explicit section `{selected_anchor}`")
            out.append("")
            out.append(extract_section(view.content, section).rstrip())
        omitted = [
            item.anchor
            for item in view.sections
            if item.level == 2
            and item.anchor not in config.navigation_extend_through
            and item.anchor not in selected
        ]
        omitted_count += len(omitted)
        out.append("")
        out.append(
            "_Coverage: navigation"
            + (" + explicit sections" if selected else "")
            + f". Omitted H2: {', '.join(omitted) if omitted else 'none'}._"
        )
    out.append("")
    out.append("## Diagnostics and boundaries")
    out.append("")
    notes: list[str] = []
    freshness_found = False
    for selected_id in ordered:
        view = views[selected_id]
        for edge in view.outgoing:
            if edge.expected_revision is None:
                continue
            dependency = views.get(edge.peer_id)
            if dependency is not None and dependency.revision != edge.expected_revision:
                mode = (
                    "historical snapshot"
                    if view.document_type in config.snapshot_document_types
                    else "STALE"
                )
                notes.append(
                    f"{selected_id}: {edge.peer_id}@"
                    f"{edge.expected_revision}, current "
                    f"{dependency.revision} — {mode}"
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
    for note in sorted(set(notes)):
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


def readiness(project_root: Path, *, json_output: bool = False) -> int:
    """Report, read-only, whether an existing project is adoption-ready.

    Stable summary data (counts, projection state, the next safe command)
    goes to stdout; ERROR/WARNING diagnostics go to stderr, matching
    `validate`, `doctor` and `migrate`. `--json` prints one deterministic
    object carrying the same data in full instead of counts, so a consumer
    never has to parse the stderr diagnostics.
    """

    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    catalog_value = build_catalog(config)
    report = evaluate_readiness(config, catalog_value)
    next_command = report.next_command(str(project_root))

    if json_output:
        # One payload shape for every project state: a missing documentation
        # root reports empty categories rather than a shorter object, so a
        # consumer never has to branch on which keys exist.
        _print_json(
            {
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
        )
        return 0 if report.ready else 1

    if not report.documentation_root_exists:
        print(f"# Adoption readiness: {project_root}")
        print()
        print(
            f"ERROR: documentation root does not exist: {config.documentation_root}",
            file=sys.stderr,
        )
        print(f"- Next safe command: {next_command}")
        return 1

    print(f"# Adoption readiness: {project_root}")
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

    impact_parser = subparsers.add_parser("impact", help="Show reverse metadata impact.")
    impact_parser.add_argument("document_id")
    impact_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())

    report_parser = subparsers.add_parser(
        "migration-report", help="Report legacy relation adoption mappings."
    )
    report_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    report_parser.add_argument(
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "read":
        return read_document(
            args.project,
            args.document_id,
            anchor=args.anchor,
            navigation=args.navigation,
            list_sections=args.list_sections,
        )
    if args.command == "dependencies":
        return dependencies(args.project, args.document_id, reverse=args.reverse)
    if args.command == "catalog":
        return catalog(args.project, explain=args.explain, json_output=args.json_output)
    if args.command == "context":
        return context(
            args.project,
            args.document_id,
            anchor=args.anchor,
            depth=args.depth,
            include_related=args.include_related,
            includes=args.include,
            json_output=args.json_output,
        )
    if args.command == "impact":
        return impact(args.project, args.document_id)
    if args.command == "migration-report":
        return migration_report(args.project, json_output=args.json_output)
    if args.command == "migrate":
        return migrate(args.project, apply=args.apply)
    if args.command == "readiness":
        return readiness(args.project, json_output=args.json_output)
    if args.command == "index":
        return index_projection(args.project, write=args.write)
    if args.command == "changes":
        return changes(args.project, json_output=args.json_output)
    if args.command == "doctor":
        return doctor(
            args.project, verbose_adoption=args.verbose_adoption
        )
    if args.command == "validate":
        return validate(
            args.project, verbose_adoption=args.verbose_adoption
        )
    handlers = {
        "init": initialize,
        "show-config": show_config,
    }
    return handlers[args.command](args.project)
