"""Project configuration loading and validation."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from docsystem.metadata import DOCUMENT_ID_PATTERN
from docsystem.sections import is_valid_anchor

CONFIG_FILENAME = ".docsystem.toml"
PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
MAINTENANCE_ROLES = frozenset(
    {"current", "historical", "example", "snapshot", "unmanaged"}
)
MAINTENANCE_TARGET_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
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

[navigation]
extend_through = []

[relations]
legacy_paths = "strict"
snapshot_types = []

[projection]
format = "sharded-json"
keep_generations = 2
"""


@dataclass(frozen=True)
class MaintenanceOccurrence:
    """One declared managed-block occurrence of a maintenance target."""

    document_id: str
    anchor: str
    role: str


@dataclass(frozen=True)
class MaintenanceTarget:
    """A project-defined managed maintenance target.

    `source_document_id`/`source_anchor` name the one canonical section that
    owns the block's authored bytes; `occurrences` are the bounded, declared
    document/section addresses where a bounded managed-block replica may
    exist. Only an `occurrences` entry with `role == "current"` is preview
    eligible; the remaining roles (`historical`, `example`, `snapshot`,
    `unmanaged`) are visible, excluded evidence and never receive a patch.
    """

    name: str
    source_document_id: str
    source_anchor: str
    occurrences: tuple[MaintenanceOccurrence, ...]


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
    navigation_extend_through: tuple[str, ...] = ()
    legacy_relation_mode: str = "strict"
    snapshot_document_types: tuple[str, ...] = ()
    maintenance_targets: tuple[MaintenanceTarget, ...] = ()


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


def _navigation_anchors(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("navigation must be a table")
    values = raw.get("extend_through", [])
    if not isinstance(values, list):
        raise ValueError("navigation.extend_through must be a list")

    anchors: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        field = f"navigation.extend_through[{index}]"
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty string")
        if not is_valid_anchor(value):
            raise ValueError(f"{field} has unsupported anchor syntax")
        if value in seen:
            raise ValueError(
                f"navigation.extend_through contains duplicate anchor {value!r}"
            )
        seen.add(value)
        anchors.append(value)
    return tuple(anchors)


def _relations_policy(raw: object) -> tuple[str, tuple[str, ...]]:
    if raw is None:
        return "strict", ()
    if not isinstance(raw, dict):
        raise ValueError("relations must be a table")
    mode = raw.get("legacy_paths", "strict")
    if mode not in {"strict", "resolve-with-warning"}:
        raise ValueError(
            "relations.legacy_paths must be 'strict' or 'resolve-with-warning'"
        )
    types = raw.get("snapshot_types", [])
    if not isinstance(types, list) or any(
        not isinstance(item, str) or not item.strip() for item in types
    ):
        raise ValueError("relations.snapshot_types must be a list of non-empty strings")
    if len(set(types)) != len(types):
        raise ValueError("relations.snapshot_types must be unique")
    return mode, tuple(types)


def _maintenance_id(value: object, field: str, prefixes: frozenset[str]) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a stable document ID")
    match = DOCUMENT_ID_PATTERN.fullmatch(value)
    if match is None or match.group(1) not in prefixes:
        raise ValueError(f"{field} must use a configured stable ID prefix")
    return value


def _maintenance_target_anchor(value: object, field: str) -> str:
    if not isinstance(value, str) or not is_valid_anchor(value):
        raise ValueError(f"{field} must use the supported stable anchor syntax")
    return value


def _maintenance_occurrence(
    raw: object, field: str, prefixes: frozenset[str]
) -> MaintenanceOccurrence:
    if not isinstance(raw, dict):
        raise ValueError(f"{field} must be a table")
    allowed = {"document", "anchor", "role"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(
            f"{field} has unknown key(s): {', '.join(sorted(unknown))}"
        )
    document_id = _maintenance_id(raw.get("document"), f"{field}.document", prefixes)
    anchor = _maintenance_target_anchor(raw.get("anchor"), f"{field}.anchor")
    role = raw.get("role")
    if role not in MAINTENANCE_ROLES:
        raise ValueError(
            f"{field}.role must be one of: "
            f"{', '.join(sorted(MAINTENANCE_ROLES))}"
        )
    return MaintenanceOccurrence(document_id, anchor, role)


def _maintenance_targets(
    raw: object, prefixes: frozenset[str]
) -> tuple[MaintenanceTarget, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("maintenance must be a list of tables")

    targets: list[MaintenanceTarget] = []
    seen_names: set[str] = set()
    for index, entry in enumerate(raw):
        field = f"maintenance[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{field} must be a table")
        allowed = {"name", "source_document", "source_anchor", "occurrences"}
        unknown = set(entry) - allowed
        if unknown:
            raise ValueError(
                f"{field} has unknown key(s): {', '.join(sorted(unknown))}"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not MAINTENANCE_TARGET_NAME_PATTERN.fullmatch(
            name
        ):
            raise ValueError(
                f"{field}.name must be a non-empty identifier-style string"
            )
        if name in seen_names:
            raise ValueError(f"maintenance target name is duplicated: {name!r}")
        seen_names.add(name)

        source_document_id = _maintenance_id(
            entry.get("source_document"), f"{field}.source_document", prefixes
        )
        source_anchor = _maintenance_target_anchor(
            entry.get("source_anchor"), f"{field}.source_anchor"
        )

        raw_occurrences = entry.get("occurrences")
        if not isinstance(raw_occurrences, list) or not raw_occurrences:
            raise ValueError(f"{field}.occurrences must be a non-empty list")

        occurrences: list[MaintenanceOccurrence] = []
        seen_occurrences: set[tuple[str, str]] = set()
        for occurrence_index, raw_occurrence in enumerate(raw_occurrences):
            occurrence_field = f"{field}.occurrences[{occurrence_index}]"
            occurrence = _maintenance_occurrence(
                raw_occurrence, occurrence_field, prefixes
            )
            key = (occurrence.document_id, occurrence.anchor)
            if key == (source_document_id, source_anchor):
                raise ValueError(
                    f"{occurrence_field} overlaps the declared source address "
                    f"{source_document_id}#{source_anchor}"
                )
            if key in seen_occurrences:
                raise ValueError(
                    f"{occurrence_field} duplicates another occurrence at "
                    f"{occurrence.document_id}#{occurrence.anchor}"
                )
            seen_occurrences.add(key)
            occurrences.append(occurrence)

        targets.append(
            MaintenanceTarget(
                name=name,
                source_document_id=source_document_id,
                source_anchor=source_anchor,
                occurrences=tuple(occurrences),
            )
        )
    return tuple(targets)


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
    navigation_extend_through = _navigation_anchors(raw.get("navigation"))
    legacy_relation_mode, snapshot_document_types = _relations_policy(
        raw.get("relations")
    )
    maintenance_targets = _maintenance_targets(
        raw.get("maintenance"), frozenset(normalized_identifiers.values())
    )

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
        navigation_extend_through=navigation_extend_through,
        legacy_relation_mode=legacy_relation_mode,
        snapshot_document_types=snapshot_document_types,
        maintenance_targets=maintenance_targets,
    )
