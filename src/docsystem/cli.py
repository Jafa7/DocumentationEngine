"""Command-line interface for Documentation Engine."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from docsystem.catalog import (
    build_catalog,
    build_dependency_graph,
    document_section_issues,
    find_document,
    validate_catalog,
    validate_membership,
    validate_metadata,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config
from docsystem.sections import extract_navigation, extract_section


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


def doctor(project_root: Path) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    errors: list[str] = []
    warnings: list[str] = []
    if not config.documentation_root.is_dir():
        errors.append(f"documentation root does not exist: {config.documentation_root}")
    else:
        for issue in validate_catalog(build_catalog(config), config):
            rendered = f"{issue.path.as_posix()}: {issue.message}"
            if issue.severity == "warning":
                warnings.append(rendered)
            else:
                errors.append(rendered)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
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


def validate(project_root: Path) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    issues = validate_catalog(build_catalog(config), config)
    errors = [issue for issue in issues if issue.severity != "warning"]
    for issue in issues:
        level = "WARNING" if issue.severity == "warning" else "ERROR"
        print(f"{level}: {issue.path.as_posix()}: {issue.message}", file=sys.stderr)
    if errors:
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
        document = find_document(build_catalog(config), document_id)
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
    handlers = {
        "init": initialize,
        "doctor": doctor,
        "show-config": show_config,
        "validate": validate,
    }
    return handlers[args.command](args.project)
