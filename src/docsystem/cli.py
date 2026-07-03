"""Command-line interface for Documentation Engine."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config


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
    if not config.documentation_root.is_dir():
        errors.append(f"documentation root does not exist: {config.documentation_root}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Configuration is valid.")
    print(f"Documentation root: {config.documentation_root}")
    print(f"Language: {config.language}")
    print(f"Projection: {config.projection_format}")
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
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    return parser


def main() -> int:
    args = build_parser().parse_args()
    handlers = {
        "init": initialize,
        "doctor": doctor,
        "show-config": show_config,
    }
    return handlers[args.command](args.project)
