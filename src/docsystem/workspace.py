"""Local workspace registry for single-source project selection.

A workspace is a local directory holding a `workspace.toml` manifest that
names existing project profiles by a stable source name. Selecting a source
resolves exactly one ordinary project root; every command then runs its
existing semantics unchanged against that root. This is deliberately not
federation: there are no qualified IDs, no cross-source relations and no
aggregate catalog. A source is one plain project, addressed by name instead
of by an absolute path a caller would otherwise have to paste around.

Nothing here is provider-specific, and nothing reads document bodies: the
registry only resolves and classifies project roots.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from docsystem.config import CONFIG_FILENAME, load_config

WORKSPACE_FILENAME = "workspace.toml"
LOCAL_POINTER_FILENAME = ".docsystem.local.toml"
WORKSPACE_ENV_VAR = "DOCSYSTEM_WORKSPACE"

SOURCE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
VISIBILITIES = ("private", "public")

_MANIFEST_KEYS = frozenset({"version", "sources"})
_SOURCE_KEYS = frozenset({"name", "root", "visibility"})
_POINTER_KEYS = frozenset({"workspace"})

# Availability reasons are fixed slugs, never rendered from a local path or a
# document body, so a listing can be shared without leaking private wiring.
REASON_MISSING_ROOT = "missing-root"
REASON_MISSING_CONFIGURATION = "missing-configuration"
REASON_INVALID_CONFIGURATION = "invalid-configuration"
REASON_UNSAFE_LOCAL_PATH = "unsafe-local-path"


class WorkspaceError(ValueError):
    """A workspace manifest, pointer or source selection was not honored."""


@dataclass(frozen=True)
class WorkspaceSource:
    """One registered source: a name bound to a contained project root."""

    name: str
    root: PurePosixPath
    visibility: str
    project_root: Path


@dataclass(frozen=True)
class Workspace:
    """An immutable, validated workspace manifest."""

    root: Path
    sources: tuple[WorkspaceSource, ...]

    def find(self, name: str) -> WorkspaceSource | None:
        return next((item for item in self.sources if item.name == name), None)


@dataclass(frozen=True)
class SourceStatus:
    """Whether a registered source can currently be selected, and why not."""

    name: str
    visibility: str
    available: bool
    reason: str | None


def _require_version(raw: Mapping[str, object]) -> None:
    version = raw.get("version")
    # `True == 1` in Python, so booleans are rejected before the value check.
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise WorkspaceError("workspace manifest version must be exactly 1")


def _source_root(value: object, name: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise WorkspaceError(f"source {name!r}: root must be a non-empty string")
    if "\\" in value:
        raise WorkspaceError(f"source {name!r}: root must use POSIX '/' separators")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise WorkspaceError(
            f"source {name!r}: root must be a relative path without '..'"
        )
    return path


def _source_name(value: object, index: int) -> str:
    if not isinstance(value, str) or not SOURCE_NAME_PATTERN.fullmatch(value):
        raise WorkspaceError(
            f"sources[{index}]: name must match {SOURCE_NAME_PATTERN.pattern}"
        )
    return value


def _source_visibility(value: object, name: str) -> str:
    if value not in VISIBILITIES:
        raise WorkspaceError(
            f"source {name!r}: visibility must be 'private' or 'public'"
        )
    return str(value)


def _parse_source(raw: object, index: int, workspace_root: Path) -> WorkspaceSource:
    if not isinstance(raw, dict):
        raise WorkspaceError(f"sources[{index}] must be a table")
    unknown = sorted(set(raw) - _SOURCE_KEYS)
    if unknown:
        raise WorkspaceError(
            f"sources[{index}] has unknown key(s): {', '.join(unknown)}"
        )
    name = _source_name(raw.get("name"), index)
    root = _source_root(raw.get("root"), name)
    visibility = _source_visibility(raw.get("visibility"), name)

    # Resolve before containment so a symlinked source root cannot escape the
    # workspace. `resolve()` is non-strict, so a source whose root does not
    # exist yet is still checked and later reported as unavailable.
    try:
        project_root = (workspace_root / root).resolve()
    except (OSError, RuntimeError) as error:
        raise WorkspaceError(
            f"source {name!r}: root cannot be resolved safely"
        ) from error
    if not project_root.is_relative_to(workspace_root):
        raise WorkspaceError(
            f"source {name!r}: resolved root escapes the workspace root"
        )
    return WorkspaceSource(
        name=name,
        root=root,
        visibility=visibility,
        project_root=project_root,
    )


def load_workspace(workspace_root: Path) -> Workspace:
    """Load and fully validate `<workspace_root>/workspace.toml`.

    Every rule is a hard failure: a rejected manifest yields no workspace at
    all rather than a partially trusted one. Duplicate names and duplicate
    resolved roots are errors, never last-wins.
    """

    try:
        root = workspace_root.expanduser().resolve()
    except (OSError, RuntimeError) as error:
        raise WorkspaceError("workspace root cannot be resolved safely") from error
    manifest_path = root / WORKSPACE_FILENAME
    if not manifest_path.is_file():
        raise WorkspaceError("workspace manifest not found")
    try:
        with manifest_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise WorkspaceError(f"invalid workspace manifest: {error}") from error
    except OSError as error:
        raise WorkspaceError("workspace manifest is unreadable") from error

    unknown = sorted(set(raw) - _MANIFEST_KEYS)
    if unknown:
        raise WorkspaceError(
            f"workspace manifest has unknown key(s): {', '.join(unknown)}"
        )
    _require_version(raw)

    entries = raw.get("sources", [])
    if not isinstance(entries, list):
        raise WorkspaceError("workspace manifest sources must be an array of tables")

    sources: list[WorkspaceSource] = []
    names: set[str] = set()
    roots: set[Path] = set()
    for index, entry in enumerate(entries):
        source = _parse_source(entry, index, root)
        if source.name in names:
            raise WorkspaceError(f"duplicate workspace source name: {source.name}")
        if source.project_root in roots:
            raise WorkspaceError(
                f"source {source.name!r}: duplicate resolved source root"
            )
        if any(
            source.project_root.is_relative_to(existing)
            or existing.is_relative_to(source.project_root)
            for existing in roots
        ):
            raise WorkspaceError(
                f"source {source.name!r}: overlapping resolved source root"
            )
        names.add(source.name)
        roots.add(source.project_root)
        sources.append(source)

    return Workspace(
        root=root,
        sources=tuple(sorted(sources, key=lambda item: item.name)),
    )


def evaluate_source(source: WorkspaceSource) -> SourceStatus:
    """Classify one source as selectable, or name the fixed reason it is not."""

    reason: str | None = None
    if not source.project_root.is_dir():
        reason = REASON_MISSING_ROOT
    elif not (source.project_root / CONFIG_FILENAME).is_file():
        reason = REASON_MISSING_CONFIGURATION
    else:
        try:
            config = load_config(source.project_root)
        except (OSError, ValueError):
            reason = REASON_INVALID_CONFIGURATION
        else:
            # A selected source may later receive `migrate --apply` or
            # `index --write`. Reject symlink escapes before selection so
            # those existing commands cannot write outside the registered
            # source root. Nonexistent paths still resolve lexically inside
            # the source and remain valid for normal bootstrap/readiness.
            writable_roots = (
                config.documentation_root,
                source.project_root / ".docsystem" / "cache",
            )
            try:
                contained = all(
                    path.resolve().is_relative_to(source.project_root)
                    for path in writable_roots
                )
            except (OSError, RuntimeError):
                contained = False
            if not contained:
                reason = REASON_UNSAFE_LOCAL_PATH
    return SourceStatus(
        name=source.name,
        visibility=source.visibility,
        available=reason is None,
        reason=reason,
    )


def source_statuses(workspace: Workspace) -> tuple[SourceStatus, ...]:
    """Classify every registered source, sorted by source name."""

    return tuple(evaluate_source(source) for source in workspace.sources)


def read_local_pointer(project_root: Path) -> Path | None:
    """Read `.docsystem.local.toml` from a discovery root, if present.

    The pointer is local machine wiring, never public project policy, so it
    carries exactly one key and is rejected outright when it carries anything
    else.
    """

    pointer_path = project_root / LOCAL_POINTER_FILENAME
    if not pointer_path.is_file():
        return None
    try:
        with pointer_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise WorkspaceError(
            f"invalid {LOCAL_POINTER_FILENAME}: {error}"
        ) from error
    except OSError as error:
        raise WorkspaceError(
            f"{LOCAL_POINTER_FILENAME} is unreadable"
        ) from error

    unknown = sorted(set(raw) - _POINTER_KEYS)
    if unknown:
        raise WorkspaceError(
            f"{LOCAL_POINTER_FILENAME} has unknown key(s): {', '.join(unknown)}"
        )
    value = raw.get("workspace")
    if not isinstance(value, str) or not value:
        raise WorkspaceError(
            f"{LOCAL_POINTER_FILENAME} must set workspace to a non-empty string"
        )
    pointer = Path(value).expanduser()
    if not pointer.is_absolute():
        raise WorkspaceError(
            f"{LOCAL_POINTER_FILENAME} workspace must be an absolute path"
        )
    return pointer


def discover_workspace_root(
    *,
    workspace_option: Path | None,
    project_root: Path,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Resolve the workspace root by fixed precedence, or return `None`.

    Precedence is explicit `--workspace`, then `DOCSYSTEM_WORKSPACE`, then a
    `.docsystem.local.toml` pointer in the positional discovery root. An
    empty environment value is malformed rather than absent, so a broken
    export fails closed instead of silently falling through to the pointer.
    """

    if workspace_option is not None:
        return workspace_option
    values = os.environ if environ is None else environ
    raw = values.get(WORKSPACE_ENV_VAR)
    if raw is not None:
        if not raw.strip():
            raise WorkspaceError(f"{WORKSPACE_ENV_VAR} must not be empty")
        return Path(raw).expanduser()
    return read_local_pointer(project_root)


def resolve_workspace(
    *,
    workspace_option: Path | None,
    project_root: Path,
    environ: Mapping[str, str] | None = None,
) -> Workspace:
    """Discover and load the workspace, failing closed when there is none."""

    root = discover_workspace_root(
        workspace_option=workspace_option,
        project_root=project_root,
        environ=environ,
    )
    if root is None:
        raise WorkspaceError(
            "no workspace configured; pass --workspace PATH, set "
            f"{WORKSPACE_ENV_VAR}, or add {LOCAL_POINTER_FILENAME}"
        )
    return load_workspace(root)


def resolve_source_root(
    name: str,
    *,
    workspace_option: Path | None,
    project_root: Path,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve one selected source to an existing project root.

    An unknown or unavailable source raises instead of falling back to the
    positional project root: an explicit selection that cannot be honored is
    an error, never a silent switch to a different project.
    """

    workspace = resolve_workspace(
        workspace_option=workspace_option,
        project_root=project_root,
        environ=environ,
    )
    source = workspace.find(name)
    if source is None:
        raise WorkspaceError(f"unknown workspace source: {name}")
    status = evaluate_source(source)
    if not status.available:
        raise WorkspaceError(
            f"workspace source is unavailable: {name} ({status.reason})"
        )
    return source.project_root
