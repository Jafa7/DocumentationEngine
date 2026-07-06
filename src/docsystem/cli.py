"""Command-line interface for Documentation Engine."""

from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path

from docsystem.catalog import (
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
    build_projection,
    projection_status,
    write_projection,
)
from docsystem.projection import (
    changes as projection_changes,
)
from docsystem.readiness import evaluate_readiness
from docsystem.sections import extract_navigation, extract_section


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


def catalog(project_root: Path, *, explain: bool = False) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    markdown_catalog = build_catalog(config)
    if explain:
        for membership in markdown_catalog.memberships:
            detail = membership.role or membership.reason or "-"
            print(f"{membership.state}\t{detail}\t{membership.path.as_posix()}")
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
        markdown_catalog = build_catalog(config)
        _projection_notice(config, markdown_catalog)
        document = find_document(markdown_catalog, document_id)
        section_issues = document_section_issues(document, config)
        if section_issues:
            for message in section_issues:
                print(
                    f"ERROR: {document.path.as_posix()}: {message}",
                    file=sys.stderr,
                )
            return 1
        if list_sections:
            output = "".join(
                f"{section.anchor}\tH{section.level}\t"
                f"{section.start_line}:{section.end_line}\t{section.title}\n"
                for section in document.sections
            )
        elif anchor is not None:
            section = next(
                (item for item in document.sections if item.anchor == anchor), None
            )
            if section is None:
                raise ValueError(f"anchor not found in {document_id}: {anchor}")
            output = extract_section(document.content, section)
        elif navigation:
            output = extract_navigation(
                document.content,
                document.sections,
                config.navigation_extend_through,
            )
        else:
            output = (
                document.content
                if document.content.endswith("\n")
                else f"{document.content}\n"
            )
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0


def _projection_notice(config, catalog) -> None:
    valid, reason = projection_status(config, build_projection(catalog))
    if not valid:
        print(f"WARNING: {reason}; using direct Markdown", file=sys.stderr)


def _selection(raw: str) -> tuple[str, str | None]:
    document_id, separator, anchor = raw.partition("#")
    if not document_id or (separator and not anchor):
        raise ValueError(f"invalid include selection: {raw!r}")
    return document_id, anchor if separator else None


def context(
    project_root: Path,
    document_id: str,
    *,
    anchor: str | None = None,
    depth: int = 1,
    include_related: bool = False,
    includes: list[str] | None = None,
) -> int:
    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
        _projection_notice(config, catalog_value)
        target = find_document(catalog_value, document_id)
        graph = build_dependency_graph(catalog_value)
        by_id = {
            document.metadata.document_id: document
            for document in catalog_value.documents
            if document.metadata is not None
        }
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
            for edge in graph.outgoing(source_id):
                if edge.relation not in allowed:
                    continue
                included.setdefault(edge.target_id, set()).add(edge.relation)
                queue.append((edge.target_id, current_depth + 1))
        forced: dict[str, list[str]] = {}
        for raw in includes or []:
            selected_id, selected_anchor = _selection(raw)
            find_document(catalog_value, selected_id)
            included.setdefault(selected_id, set()).add("explicit")
            if selected_anchor:
                forced.setdefault(selected_id, []).append(selected_anchor)
        relevant_paths = {by_id[item].path for item in included}
        selected_anchors = {
            selected_id: [
                *([anchor] if selected_id == document_id and anchor else []),
                *forced.get(selected_id, []),
            ]
            for selected_id in included
        }
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
            known_anchors = {section.anchor for section in document.sections}
            for selected_anchor in selected_anchors[selected_id]:
                if selected_anchor not in known_anchors:
                    raise ValueError(
                        f"anchor not found in {selected_id}: {selected_anchor}"
                    )
        if blockers:
            for issue in blockers:
                print(
                    f"ERROR: {issue.path.as_posix()}: {issue.message}",
                    file=sys.stderr,
                )
            return 1
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"# Context packet: {document_id}")
    print()
    print(f"- Dependency depth: {depth}")
    print(f"- Related traversal: {'included' if include_related else 'omitted'}")
    ordered = [document_id, *sorted(item for item in included if item != document_id)]
    for selected_id in ordered:
        document = by_id[selected_id]
        print()
        print(f"## {selected_id} — {document.path.as_posix()}")
        print()
        print(f"Relations: {', '.join(sorted(included[selected_id]))}.")
        print()
        print(
            extract_navigation(
                document.content,
                document.sections,
                config.navigation_extend_through,
            ).rstrip()
        )
        selected = selected_anchors[selected_id]
        for selected_anchor in dict.fromkeys(selected):
            section = next(
                (
                    item
                    for item in document.sections
                    if item.anchor == selected_anchor
                ),
                None,
            )
            print()
            print(f"### Explicit section `{selected_anchor}`")
            print()
            print(extract_section(document.content, section).rstrip())
        omitted = [
            item.anchor
            for item in document.sections
            if item.level == 2
            and item.anchor not in config.navigation_extend_through
            and item.anchor not in selected
        ]
        print()
        print(
            "_Coverage: navigation"
            + (" + explicit sections" if selected else "")
            + f". Omitted H2: {', '.join(omitted) if omitted else 'none'}._"
        )
    print()
    print("## Diagnostics and boundaries")
    print()
    notes: list[str] = []
    freshness_found = False
    for selected_id in ordered:
        document = by_id[selected_id]
        if document.metadata is None:
            continue
        for reference in document.metadata.references:
            if reference.expected_revision is None:
                continue
            dependency = by_id.get(reference.target_id)
            if (
                dependency is not None
                and dependency.metadata is not None
                and dependency.metadata.revision != reference.expected_revision
            ):
                mode = (
                    "historical snapshot"
                    if document.metadata.document_type
                    in config.snapshot_document_types
                    else "STALE"
                )
                notes.append(
                    f"{selected_id}: {reference.target_id}@"
                    f"{reference.expected_revision}, current "
                    f"{dependency.metadata.revision} — {mode}"
                )
                freshness_found = True
    if not freshness_found:
        notes.append("No stale revision pins among included documents.")
    for migration in catalog_value.relation_migrations:
        if migration.source_id in included:
            notes.append(
                f"{migration.source_id}: {migration.relation} "
                f"{migration.value} -> {migration.target_id}"
            )
    boundary_found = False
    for boundary in catalog_value.relation_boundaries:
        if boundary.source_id in included:
            notes.append(
                f"{boundary.source_id}: unresolved/resource {boundary.relation} "
                f"{boundary.value} ({boundary.reason})"
            )
            boundary_found = True
    if not boundary_found:
        notes.append(
            "No unresolved/resource boundaries among included documents."
        )
    if not include_related and target.metadata is not None:
        related = [
            value
            for relation, value in target.metadata.legacy_references
            if relation == "related"
        ]
        related.extend(
            reference.target_id
            for reference in target.metadata.references
            if reference.relation == "related"
        )
        if related:
            notes.append("Related omitted: " + ", ".join(related))
    for note in sorted(set(notes)):
        print(f"- {note}")
    print("- Expand with --depth, --include-related, or --include ID#anchor.")
    return 0


def impact(project_root: Path, document_id: str) -> int:
    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
        _projection_notice(config, catalog_value)
        target = find_document(catalog_value, document_id)
        graph = build_dependency_graph(catalog_value)
        by_id = {
            document.metadata.document_id: document
            for document in catalog_value.documents
            if document.metadata is not None
        }
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
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"# Impact analysis: {document_id}")
    print()
    print(f"- Path: `{target.path.as_posix()}`")
    print(
        "- Type/status: "
        f"{target.metadata.document_type if target.metadata else None} / "
        f"{target.metadata.status if target.metadata else None}"
    )
    print(f"- Current revision: {target.metadata.revision if target.metadata else '—'}")
    print()
    print("| Downstream | Relation | Pin | Classification |")
    print("|---|---|---|---|")
    for edge in graph.incoming(document_id):
        source = by_id[edge.source_id]
        source_type = source.metadata.document_type if source.metadata else None
        if edge.relation == "related":
            classification = "related navigation"
        elif edge.relation == "validated_against":
            if source_type in config.snapshot_document_types:
                classification = "historical snapshot"
            elif (
                target.metadata is not None
                and edge.expected_revision == target.metadata.revision
            ):
                classification = "freshness pin (current)"
            else:
                classification = "freshness pin (already stale)"
        elif edge.relation == "supersedes":
            classification = "lineage"
        else:
            classification = "semantic"
        pin = str(edge.expected_revision) if edge.expected_revision else "—"
        print(
            f"| `{edge.source_id}` | {edge.relation} | {pin} | "
            f"{classification} |"
        )
    if not graph.incoming(document_id):
        print("| — | — | — | no reverse metadata dependencies |")
    return 0


def migration_report(project_root: Path) -> int:
    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
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


def readiness(project_root: Path) -> int:
    """Report, read-only, whether an existing project is adoption-ready.

    Stable summary data (counts, projection state, the next safe command)
    goes to stdout; ERROR/WARNING diagnostics go to stderr, matching
    `validate`, `doctor` and `migrate`.
    """

    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    catalog_value = build_catalog(config)
    report = evaluate_readiness(config, catalog_value)

    print(f"# Adoption readiness: {project_root}")
    print()
    if not report.documentation_root_exists:
        print(
            f"ERROR: documentation root does not exist: {config.documentation_root}",
            file=sys.stderr,
        )
        print(f"- Next safe command: {report.next_command(str(project_root))}")
        return 1

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
    print(f"- Next safe command: {report.next_command(str(project_root))}")
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
        current = build_projection(catalog_value)
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


def changes(project_root: Path) -> int:
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
        current = build_projection(catalog_value)
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

    impact_parser = subparsers.add_parser("impact", help="Show reverse metadata impact.")
    impact_parser.add_argument("document_id")
    impact_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())

    report_parser = subparsers.add_parser(
        "migration-report", help="Report legacy relation adoption mappings."
    )
    report_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())

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

    index_parser = subparsers.add_parser("index", help="Check or write the projection.")
    index_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    index_parser.add_argument("--write", action="store_true")

    changes_parser = subparsers.add_parser("changes", help="Show changes since projection.")
    changes_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
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
        return catalog(args.project, explain=args.explain)
    if args.command == "context":
        return context(
            args.project,
            args.document_id,
            anchor=args.anchor,
            depth=args.depth,
            include_related=args.include_related,
            includes=args.include,
        )
    if args.command == "impact":
        return impact(args.project, args.document_id)
    if args.command == "migration-report":
        return migration_report(args.project)
    if args.command == "migrate":
        return migrate(args.project, apply=args.apply)
    if args.command == "readiness":
        return readiness(args.project)
    if args.command == "index":
        return index_projection(args.project, write=args.write)
    if args.command == "changes":
        return changes(args.project)
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
