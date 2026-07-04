"""Project configuration loading and validation."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

CONFIG_FILENAME = ".docsystem.toml"
PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
DEFAULT_CONFIG = """\
version = 1

[documentation]
root = "plan"
language = "en"

[areas]
foundation = "foundation"
architecture = "architecture"
decisions = "decisions"
roadmap = "roadmap"
scratch = "scratch"
reviews = "reviews"
experiments = "experiments"
modules = "modules"

[identifiers]
document = "DOC"
decision = "DEC"
roadmap = "RM"

[catalog]
exclude = []

[projection]
format = "sharded-json"
keep_generations = 2
"""


@dataclass(frozen=True)
class ProjectConfig:
    project_root: Path
    documentation_root: Path
    language: str
    areas: dict[str, PurePosixPath]
    identifiers: dict[str, str]
    projection_format: str
    keep_generations: int
    catalog_exclusions: tuple[str, ...] = ()


def _relative_path(value: object, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must be a project-relative path")
    return path


def _catalog_exclusions(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("catalog must be a table")
    values = raw.get("exclude", [])
    if not isinstance(values, list):
        raise ValueError("catalog.exclude must be a list")

    patterns: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        field = f"catalog.exclude[{index}]"
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty string")
        if "\\" in value:
            raise ValueError(f"{field} must use POSIX '/' separators")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(
                f"{field} must be relative to the documentation root"
            )
        normalized = path.as_posix()
        if normalized in seen:
            raise ValueError(
                f"catalog.exclude contains duplicate normalized pattern "
                f"{normalized!r}"
            )
        seen.add(normalized)
        patterns.append(normalized)
    return tuple(patterns)


def load_config(project_root: Path) -> ProjectConfig:
    root = project_root.resolve()
    config_path = root / CONFIG_FILENAME
    if not config_path.is_file():
        raise ValueError(f"configuration not found: {config_path}")
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    if raw.get("version") != 1:
        raise ValueError("unsupported configuration version")
    documentation = raw.get("documentation")
    areas = raw.get("areas")
    identifiers = raw.get("identifiers")
    projection = raw.get("projection")
    if not all(isinstance(item, dict) for item in (documentation, areas, identifiers, projection)):
        raise ValueError("documentation, areas, identifiers and projection tables are required")

    documentation_path = _relative_path(documentation.get("root"), "documentation.root")
    language = documentation.get("language")
    if not isinstance(language, str) or not language:
        raise ValueError("documentation.language must be a non-empty string")

    normalized_areas = {
        str(role): _relative_path(path, f"areas.{role}") for role, path in areas.items()
    }
    if len(set(normalized_areas.values())) != len(normalized_areas):
        raise ValueError("area paths must be unique")

    normalized_identifiers: dict[str, str] = {}
    for role, prefix in identifiers.items():
        if not isinstance(prefix, str) or not PREFIX_PATTERN.fullmatch(prefix):
            raise ValueError(f"identifiers.{role} has an invalid prefix")
        normalized_identifiers[str(role)] = prefix
    if len(set(normalized_identifiers.values())) != len(normalized_identifiers):
        raise ValueError("identifier prefixes must be unique")

    catalog_exclusions = _catalog_exclusions(raw.get("catalog"))

    projection_format = projection.get("format")
    if projection_format != "sharded-json":
        raise ValueError("only sharded-json projection is supported")
    keep_generations = projection.get("keep_generations")
    if not isinstance(keep_generations, int) or not 1 <= keep_generations <= 20:
        raise ValueError("projection.keep_generations must be between 1 and 20")

    return ProjectConfig(
        project_root=root,
        documentation_root=root / documentation_path,
        language=language,
        areas=normalized_areas,
        identifiers=normalized_identifiers,
        projection_format=projection_format,
        keep_generations=keep_generations,
        catalog_exclusions=catalog_exclusions,
    )
