"""Bounded, journaled transactions for documentation maintenance.

This provider-neutral module implements the non-destructive change journal
described by `DOC-017`. Every entry point takes explicit `source_root` and
`journal_root` authority from its caller. The public maintenance CLI integrates
it only for declared managed blocks; tests and safety drills use copied or
synthetic roots and never grant implicit authority over a private `plan/`.

A *generation* is one immutable, journaled attempt at a bounded write. Every
generation is created under an exclusive directory name derived from a
workstream ID and a UTC timestamp, and contains:

    manifest.json           deterministic, sorted file inventory
    before/                 byte-exact copies of source content pre-write
    after/                  byte-exact copies of the content this generation
                             attempted to write (kept even when rolled back,
                             as evidence of the attempt)
    semantic.patch.diff     unified diff, before -> semantic content
    mechanical.patch.diff   unified diff, semantic -> mechanical content
    verification.json       status, checks performed and the manifest hash
    recovery.json           written only when the creating transaction rolls
                             itself back automatically

Explicit recovery creates a separate immutable record below
`recoveries/<source-generation>/<timestamp>/`; it never appends to the source
generation.

No operation in this module can delete authored source content: the only
file-content types are `"create"` (a path that must not yet exist) and
`"bounded-edit"` (an existing path edited within a declared line range). A
rename/move is simulated by leaving the original path out of the transaction
entirely and adding a `"create"` edit for the new path, so the original file
is never touched. The one exception is that a `"create"` edit written by a
transaction that is later rolled back or explicitly recovered is removed:
that content was never authored (it was this tool's own uncommitted write),
so undoing it is not a delete of pre-existing authored material.

A completed generation's `manifest.json` and `verification.json` are only
ever rewritten by the same `run_bounded_transaction` call that created them,
to record their own final status. No later call reuses or mutates an
    existing generation: generation directories are created with exclusive
    `mkdir`. `recover_generation` reads it and creates a separate record;
    `copy_generation_to_cloud` only reads it.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1

_OPERATIONS = frozenset({"bounded-edit", "create"})
_WORKSTREAM_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+)+$")
_CREATED_AT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GENERATION_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")


class JournalError(ValueError):
    """A journal request violates a bounded-write safety invariant."""


@dataclass(frozen=True)
class LineRange:
    """A 1-indexed, inclusive line-number bound on where a change may land."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 1:
            raise JournalError("range start must be at least 1")
        if self.end < self.start:
            raise JournalError("range end must be greater than or equal to start")


@dataclass(frozen=True)
class FileEdit:
    """One file's declared bounded transaction.

    `before_sha256` is the caller's expected current hash of the source file
    (required for `"bounded-edit"`, forbidden for `"create"`, which requires
    the path to not yet exist). `semantic_content` is the file's full text
    after the semantic edit stage; `mechanical_content` is its full text
    after the separate mechanical finalization stage. `allowed_ranges` bounds
    both stages: the semantic stage is measured against `before` (or, for
    `"create"`, must cover the whole new file); the mechanical stage is
    measured against the semantic content, per line number at that stage.
    """

    path: str
    operation: str
    before_sha256: str | None
    semantic_content: str
    mechanical_content: str
    allowed_ranges: tuple[LineRange, ...]


@dataclass(frozen=True)
class FileGuard:
    """A read-only file hash that must remain stable for the transaction."""

    path: str
    sha256: str


@dataclass(frozen=True)
class ApplyResult:
    """The outcome of one `run_bounded_transaction` call."""

    generation_id: str
    workstream_id: str
    status: str
    changed_paths: tuple[str, ...]
    manifest_sha256: str
    validation_passed: bool
    checks: tuple[str, ...]
    reason: str | None
    generation_root: Path


@dataclass(frozen=True)
class RecoveryResult:
    """The outcome of one `recover_generation` call."""

    generation_id: str
    status: str
    reason: str | None
    restored_paths: tuple[str, ...]
    recovery_record: str | None = None


@dataclass(frozen=True)
class CloudCopyResult:
    """The outcome of one `copy_generation_to_cloud` call."""

    generation_id: str
    destination: str
    verified: bool


@dataclass(frozen=True)
class _AdmittedEdit:
    normalized_path: str
    absolute_path: Path
    operation: str
    before_sha256: str | None
    after_sha256: str
    before_bytes: bytes
    before_text: str
    semantic_content: str
    mechanical_content: str
    allowed_ranges: tuple[LineRange, ...]


@dataclass(frozen=True)
class _VerifiedGeneration:
    generation_root: Path
    manifest: Mapping[str, object]
    verification: Mapping[str, object]


def normalize_source_path(raw: str) -> PurePosixPath:
    """Normalize and validate one caller-supplied relative POSIX file path."""

    if not isinstance(raw, str) or not raw:
        raise JournalError("file path must be a non-empty string")
    if "\\" in raw:
        raise JournalError(f"file path must use POSIX '/' separators: {raw!r}")
    if raw.startswith("/"):
        raise JournalError(f"file path must be relative: {raw!r}")
    # `PurePosixPath` silently drops '.' segments and only keeps parts, so the
    # raw '/'-split components are checked directly before any normalization.
    raw_parts = raw.split("/")
    if not raw_parts or any(part in ("", ".", "..") for part in raw_parts):
        raise JournalError(f"file path must not contain '.' or '..': {raw!r}")
    path = PurePosixPath(raw)
    return path


def validate_workstream_id(value: str) -> None:
    if not isinstance(value, str) or not _WORKSTREAM_PATTERN.fullmatch(value):
        raise JournalError(f"workstream id must match {_WORKSTREAM_PATTERN.pattern}: {value!r}")


def _validate_timestamp(value: str) -> None:
    if not isinstance(value, str) or not _CREATED_AT_PATTERN.fullmatch(value):
        raise JournalError(f"timestamp must be UTC ISO-8601 'Z' format: {value!r}")


def _generation_id(workstream_id: str, created_at: str) -> str:
    compact = created_at.replace("-", "").replace(":", "")
    return f"{compact}-{workstream_id}"


def _validate_generation_id(value: str) -> None:
    if not isinstance(value, str) or not _GENERATION_PATTERN.fullmatch(value):
        raise JournalError(f"invalid generation id: {value!r}")


def _has_symlink_component(path: Path) -> bool:
    absolute = path.absolute()
    return any(
        candidate.exists() and candidate.is_symlink() for candidate in (absolute, *absolute.parents)
    )


def _resolved_separate_roots(source_root: Path, journal_root: Path) -> tuple[Path, Path]:
    resolved_source = source_root.resolve(strict=True)
    if not resolved_source.is_dir():
        raise JournalError("source root must be a directory")
    if _has_symlink_component(journal_root):
        raise JournalError("journal root must not contain a symlink")
    resolved_journal = journal_root.resolve(strict=False)
    if (
        resolved_source == resolved_journal
        or resolved_source.is_relative_to(resolved_journal)
        or resolved_journal.is_relative_to(resolved_source)
    ):
        raise JournalError("source root and journal root must not overlap")
    journal_root.mkdir(parents=True, exist_ok=True)
    resolved_journal = journal_root.resolve(strict=True)
    return resolved_source, resolved_journal


def _split_lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _validate_ranges(ranges: Sequence[LineRange], path: str) -> None:
    if not ranges:
        raise JournalError(f"{path}: at least one allowed range is required")
    ordered = sorted(ranges, key=lambda item: item.start)
    if list(ranges) != ordered:
        raise JournalError(f"{path}: declared ranges must be ordered")
    for previous, current in pairwise(ordered):
        if current.start <= previous.end:
            raise JournalError(f"{path}: declared ranges overlap or are unordered")


def _changed_spans(before: str, after: str) -> list[tuple[int, int]]:
    """Return 1-indexed inclusive before-line spans touched by any edit."""

    matcher = difflib.SequenceMatcher(
        None, _split_lines(before), _split_lines(after), autojunk=False
    )
    spans: list[tuple[int, int]] = []
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i1 == i2:
            anchor = max(i1, 1)
            spans.append((anchor, anchor))
        else:
            spans.append((i1 + 1, i2))
    return spans


def _within_ranges(span: tuple[int, int], ranges: Sequence[LineRange]) -> bool:
    start, end = span
    return any(item.start <= start and end <= item.end for item in ranges)


def _validate_bounded_change(
    before: str, after: str, ranges: Sequence[LineRange], path: str, stage: str
) -> None:
    for span in _changed_spans(before, after):
        if not _within_ranges(span, ranges):
            raise JournalError(
                f"{path}: {stage} change at line(s) {span[0]}-{span[1]} is outside "
                "the declared allowed range"
            )


def _validate_create_range(semantic_content: str, ranges: Sequence[LineRange], path: str) -> None:
    total_lines = max(len(_split_lines(semantic_content)), 1)
    ordered = sorted(ranges, key=lambda item: item.start)
    covered_end = 0
    for item in ordered:
        if item.start > covered_end + 1:
            break
        covered_end = max(covered_end, item.end)
    if ordered[0].start != 1 or covered_end < total_lines:
        raise JournalError(
            f"{path}: create operation requires declared range(s) covering the "
            f"entire new file (1-{total_lines})"
        )


def _safe_target(source_root: Path, relative: PurePosixPath, operation: str) -> Path:
    candidate = source_root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise JournalError(f"path escapes source root via symlink: {relative.as_posix()}")
    resolved_root = source_root.resolve(strict=False)
    resolved_candidate = candidate.resolve(strict=False)
    if not resolved_candidate.is_relative_to(resolved_root):
        raise JournalError(f"path escapes source root: {relative.as_posix()}")
    if operation == "create":
        if candidate.exists():
            raise JournalError(f"create target already exists: {relative.as_posix()}")
    else:
        if not candidate.exists():
            raise JournalError(f"bounded-edit target does not exist: {relative.as_posix()}")
        if candidate.is_dir():
            raise JournalError(f"bounded-edit target is a directory: {relative.as_posix()}")
        if not candidate.is_file():
            raise JournalError(f"bounded-edit target is not a regular file: {relative.as_posix()}")
    return candidate


def _admit(source_root: Path, edits: Sequence[FileEdit]) -> tuple[_AdmittedEdit, ...]:
    """Validate every declared edit against the current source, read-only.

    Every rule here runs before any source file is written: a stale hash, an
    unsafe path or an out-of-range change all raise `JournalError` without
    creating a generation directory or touching `source_root`.
    """

    if not edits:
        raise JournalError("a transaction must declare at least one file edit")

    admitted: list[_AdmittedEdit] = []
    seen: set[str] = set()
    for edit in edits:
        if edit.operation not in _OPERATIONS:
            raise JournalError(f"unsupported operation: {edit.operation!r}")
        normalized = normalize_source_path(edit.path)
        posix = normalized.as_posix()
        if posix in seen:
            raise JournalError(f"duplicate normalized target: {posix}")
        seen.add(posix)
        _validate_ranges(edit.allowed_ranges, posix)
        target = _safe_target(source_root, normalized, edit.operation)

        if edit.operation == "create":
            if edit.before_sha256 is not None:
                raise JournalError(f"{posix}: create operation must not declare before_sha256")
            before_bytes = b""
            before_text = ""
            _validate_create_range(edit.semantic_content, edit.allowed_ranges, posix)
        else:
            if edit.before_sha256 is None or not _SHA256_PATTERN.fullmatch(edit.before_sha256):
                raise JournalError(f"{posix}: before_sha256 must be a 64-character hex sha256")
            actual_bytes = target.read_bytes()
            if hashlib.sha256(actual_bytes).hexdigest() != edit.before_sha256:
                raise JournalError(f"{posix}: stale before_sha256; source has changed")
            try:
                before_text = actual_bytes.decode("utf-8")
            except UnicodeDecodeError as error:
                raise JournalError(f"{posix}: source is not valid UTF-8") from error
            before_bytes = actual_bytes
            _validate_bounded_change(
                before_text, edit.semantic_content, edit.allowed_ranges, posix, "semantic"
            )

        _validate_bounded_change(
            edit.semantic_content,
            edit.mechanical_content,
            edit.allowed_ranges,
            posix,
            "mechanical",
        )
        if edit.operation == "create":
            _validate_create_range(edit.mechanical_content, edit.allowed_ranges, posix)

        after_sha256 = hashlib.sha256(edit.mechanical_content.encode("utf-8")).hexdigest()
        admitted.append(
            _AdmittedEdit(
                normalized_path=posix,
                absolute_path=target,
                operation=edit.operation,
                before_sha256=edit.before_sha256,
                after_sha256=after_sha256,
                before_bytes=before_bytes,
                before_text=before_text,
                semantic_content=edit.semantic_content,
                mechanical_content=edit.mechanical_content,
                allowed_ranges=edit.allowed_ranges,
            )
        )
    return tuple(sorted(admitted, key=lambda item: item.normalized_path))


def _admit_guards(
    source_root: Path,
    guards: Sequence[FileGuard],
    admitted: Sequence[_AdmittedEdit],
) -> tuple[tuple[str, Path, str], ...]:
    edit_paths = {item.normalized_path for item in admitted}
    resolved: list[tuple[str, Path, str]] = []
    seen: set[str] = set()
    for guard in guards:
        path = normalize_source_path(guard.path)
        normalized = path.as_posix()
        if normalized in seen:
            raise JournalError(f"duplicate read guard: {normalized}")
        if normalized in edit_paths:
            raise JournalError(f"read guard overlaps edited path: {normalized}")
        if not _SHA256_PATTERN.fullmatch(guard.sha256):
            raise JournalError(f"{normalized}: guard sha256 must be 64 lowercase hex characters")
        target = _safe_target(source_root, path, "bounded-edit")
        if _current_hash(target) != guard.sha256:
            raise JournalError(f"{normalized}: stale read guard; source has changed")
        seen.add(normalized)
        resolved.append((normalized, target, guard.sha256))
    return tuple(sorted(resolved))


def _revalidate_guards(guards: Sequence[tuple[str, Path, str]]) -> None:
    for normalized, target, expected_hash in guards:
        if _current_hash(target) != expected_hash:
            raise JournalError(f"{normalized}: guarded source changed during transaction")


def _unified_patch(before: str, after: str, path: str) -> str:
    diff = difflib.unified_diff(
        _split_lines(before),
        _split_lines(after),
        fromfile=f"before/{path}",
        tofile=f"after/{path}",
    )
    return "".join(diff)


def _json_bytes(data: Mapping[str, object]) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json(path: Path, data: Mapping[str, object]) -> bytes:
    raw = _json_bytes(data)
    path.write_bytes(raw)
    return raw


def _manifest_dict(
    workstream_id: str,
    generation_id: str,
    created_at: str,
    status: str,
    admitted: Sequence[_AdmittedEdit],
    guards: Sequence[tuple[str, Path, str]],
    semantic_patch_sha256: str,
    mechanical_patch_sha256: str,
) -> dict[str, object]:
    files = [
        {
            "path": item.normalized_path,
            "operation": item.operation,
            "before_sha256": item.before_sha256,
            "after_sha256": item.after_sha256,
            "allowed_ranges": [{"start": r.start, "end": r.end} for r in item.allowed_ranges],
        }
        for item in sorted(admitted, key=lambda item: item.normalized_path)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "workstream_id": workstream_id,
        "generation_id": generation_id,
        "created_at": created_at,
        "status": status,
        "patches": {
            "semantic": {
                "path": "semantic.patch.diff",
                "sha256": semantic_patch_sha256,
            },
            "mechanical": {
                "path": "mechanical.patch.diff",
                "sha256": mechanical_patch_sha256,
            },
        },
        "files": files,
        "guards": [
            {"path": path, "sha256": sha256}
            for path, _target, sha256 in guards
        ],
    }


_CHECKS = (
    "path-admission",
    "hash-admission",
    "range-admission",
    "atomic-apply",
    "validation-hook",
    "read-guard-stability",
)


def _current_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _revalidate_admitted(source_root: Path, admitted: Sequence[_AdmittedEdit]) -> None:
    """Recheck paths and source hashes immediately before a mutating phase."""

    for item in admitted:
        relative = normalize_source_path(item.normalized_path)
        target = _safe_target(source_root, relative, item.operation)
        if target != item.absolute_path:
            raise JournalError(f"source target changed after admission: {item.normalized_path}")
        expected = item.before_sha256
        if _current_hash(target) != expected:
            raise JournalError(f"{item.normalized_path}: source changed after admission")


def _atomic_replace_bytes(path: Path, content: bytes) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".journal-restore", dir=str(path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with open(descriptor, "wb") as handle:
            handle.write(content)
        temp_path.replace(path)
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise


def _restore_source(
    admitted: Sequence[_AdmittedEdit], restore_targets: Sequence[Path]
) -> tuple[str, ...]:
    by_path = {item.absolute_path: item for item in admitted}
    restored: list[str] = []
    for absolute_path in restore_targets:
        item = by_path[absolute_path]
        if item.operation == "bounded-edit":
            _atomic_replace_bytes(absolute_path, item.before_bytes)
        else:
            absolute_path.unlink(missing_ok=True)
        restored.append(item.normalized_path)
    for absolute_path in restore_targets:
        item = by_path[absolute_path]
        if _current_hash(absolute_path) != item.before_sha256:
            raise JournalError(f"rollback verification failed: {item.normalized_path}")
    return tuple(sorted(restored))


def run_bounded_transaction(
    *,
    source_root: Path,
    journal_root: Path,
    workstream_id: str,
    created_at: str,
    edits: Sequence[FileEdit],
    validate: Callable[[Path], bool],
    guards: Sequence[FileGuard] = (),
) -> ApplyResult:
    """Admit, journal and atomically apply one bounded transaction.

    Order: admit every edit against the current source (no writes); create an
    exclusive generation directory holding `before/`, `after/`, both patches
    and a manifest; atomically replace source files on the same filesystem;
    run the caller's `validate` hook against the final source. A failure at
    any step after generation creation restores every changed file
    byte-for-byte from `before/` and records recovery evidence; the
    generation directory itself is never deleted.
    """

    validate_workstream_id(workstream_id)
    _validate_timestamp(created_at)
    resolved_source, resolved_journal = _resolved_separate_roots(source_root, journal_root)
    admitted = _admit(resolved_source, edits)
    admitted_guards = _admit_guards(resolved_source, guards, admitted)

    generation_id = _generation_id(workstream_id, created_at)
    generation_root = resolved_journal / generation_id
    try:
        generation_root.mkdir()
    except FileExistsError as error:
        raise JournalError(f"generation already exists: {generation_id}") from error

    before_dir = generation_root / "before"
    after_dir = generation_root / "after"
    before_dir.mkdir()
    after_dir.mkdir()

    for item in admitted:
        if item.operation == "bounded-edit":
            before_file = before_dir / item.normalized_path
            before_file.parent.mkdir(parents=True, exist_ok=True)
            before_file.write_bytes(item.before_bytes)
        after_file = after_dir / item.normalized_path
        after_file.parent.mkdir(parents=True, exist_ok=True)
        after_file.write_bytes(item.mechanical_content.encode("utf-8"))

    semantic_patch = "".join(
        _unified_patch(item.before_text, item.semantic_content, item.normalized_path)
        for item in admitted
    )
    mechanical_patch = "".join(
        _unified_patch(item.semantic_content, item.mechanical_content, item.normalized_path)
        for item in admitted
    )
    semantic_patch_bytes = semantic_patch.encode("utf-8")
    mechanical_patch_bytes = mechanical_patch.encode("utf-8")
    (generation_root / "semantic.patch.diff").write_bytes(semantic_patch_bytes)
    (generation_root / "mechanical.patch.diff").write_bytes(mechanical_patch_bytes)

    manifest = _manifest_dict(
        workstream_id,
        generation_id,
        created_at,
        "pending",
        admitted,
        admitted_guards,
        hashlib.sha256(semantic_patch_bytes).hexdigest(),
        hashlib.sha256(mechanical_patch_bytes).hexdigest(),
    )
    _write_json(generation_root / "manifest.json", manifest)

    changed_paths = tuple(item.normalized_path for item in admitted)

    temp_files: list[tuple[Path, Path]] = []
    try:
        _revalidate_admitted(resolved_source, admitted)
        _revalidate_guards(admitted_guards)
        for item in admitted:
            item.absolute_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{item.absolute_path.name}.",
                suffix=".journal-tmp",
                dir=str(item.absolute_path.parent),
            )
            with open(descriptor, "wb") as handle:
                handle.write(item.mechanical_content.encode("utf-8"))
            temp_files.append((Path(temp_name), item.absolute_path))
    except (OSError, JournalError) as error:
        for temp_path, _ in temp_files:
            temp_path.unlink(missing_ok=True)
        raise JournalError(f"failed to stage bounded write: {error}") from error

    committed: list[Path] = []
    apply_error: OSError | JournalError | None = None
    admitted_by_path = {item.absolute_path: item for item in admitted}
    for temp_path, final_path in temp_files:
        try:
            item = admitted_by_path[final_path]
            relative = normalize_source_path(item.normalized_path)
            if _safe_target(resolved_source, relative, item.operation) != final_path:
                raise JournalError(f"source target changed before apply: {item.normalized_path}")
            if _current_hash(final_path) != item.before_sha256:
                raise JournalError(
                    f"source changed immediately before apply: {item.normalized_path}"
                )
            temp_path.replace(final_path)
        except (OSError, JournalError) as error:
            apply_error = error
            break
        committed.append(final_path)

    if apply_error is not None:
        for temp_path, final_path in temp_files:
            if final_path not in committed:
                temp_path.unlink(missing_ok=True)
        manifest_sha256 = _rollback(
            admitted,
            committed,
            generation_root,
            generation_id,
            manifest,
            created_at,
            reason="apply-failure",
        )
        return ApplyResult(
            generation_id=generation_id,
            workstream_id=workstream_id,
            status="rolled-back",
            changed_paths=changed_paths,
            manifest_sha256=manifest_sha256,
            validation_passed=False,
            checks=_CHECKS[:4],
            reason=f"apply-failure: {apply_error}",
            generation_root=generation_root,
        )

    validation_error: Exception | None = None
    try:
        validation_passed = bool(validate(resolved_source))
        if validation_passed:
            _revalidate_guards(admitted_guards)
    except Exception as error:  # validation is an injected trust boundary
        validation_error = error
        validation_passed = False
    if not validation_passed:
        reason = "validation-failure"
        if validation_error is not None:
            reason = f"validation-error: {type(validation_error).__name__}"
        manifest_sha256 = _rollback(
            admitted,
            [item.absolute_path for item in admitted],
            generation_root,
            generation_id,
            manifest,
            created_at,
            reason=reason,
        )
        return ApplyResult(
            generation_id=generation_id,
            workstream_id=workstream_id,
            status="rolled-back",
            changed_paths=changed_paths,
            manifest_sha256=manifest_sha256,
            validation_passed=False,
            checks=_CHECKS,
            reason=reason,
            generation_root=generation_root,
        )

    unexpected = [
        item.normalized_path
        for item in admitted
        if _current_hash(item.absolute_path) != item.after_sha256
    ]
    if unexpected:
        reason = "post-validation source changed: " + ", ".join(unexpected)
        manifest_sha256 = _rollback(
            admitted,
            [item.absolute_path for item in admitted],
            generation_root,
            generation_id,
            manifest,
            created_at,
            reason=reason,
        )
        return ApplyResult(
            generation_id=generation_id,
            workstream_id=workstream_id,
            status="rolled-back",
            changed_paths=changed_paths,
            manifest_sha256=manifest_sha256,
            validation_passed=False,
            checks=_CHECKS,
            reason=reason,
            generation_root=generation_root,
        )

    try:
        verification_payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "generation_id": generation_id,
            "status": "applied",
            "checks": list(_CHECKS),
            "changed_paths": sorted(changed_paths),
        }
        finished_manifest = dict(manifest)
        finished_manifest["status"] = "applied"
        finished_manifest["verification_sha256"] = hashlib.sha256(
            _json_bytes(verification_payload)
        ).hexdigest()
        manifest_bytes = _write_json(generation_root / "manifest.json", finished_manifest)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        _write_json(
            generation_root / "verification.json",
            {**verification_payload, "manifest_sha256": manifest_sha256},
        )
    except OSError as error:
        reason = f"evidence-finalization-failure: {error}"
        manifest_sha256 = _rollback(
            admitted,
            [item.absolute_path for item in admitted],
            generation_root,
            generation_id,
            manifest,
            created_at,
            reason=reason,
        )
        return ApplyResult(
            generation_id=generation_id,
            workstream_id=workstream_id,
            status="rolled-back",
            changed_paths=changed_paths,
            manifest_sha256=manifest_sha256,
            validation_passed=False,
            checks=_CHECKS,
            reason=reason,
            generation_root=generation_root,
        )
    return ApplyResult(
        generation_id=generation_id,
        workstream_id=workstream_id,
        status="applied",
        changed_paths=changed_paths,
        manifest_sha256=manifest_sha256,
        validation_passed=True,
        checks=_CHECKS,
        reason=None,
        generation_root=generation_root,
    )


def _rollback(
    admitted: Sequence[_AdmittedEdit],
    restore_targets: Sequence[Path],
    generation_root: Path,
    generation_id: str,
    manifest: Mapping[str, object],
    recovered_at: str,
    *,
    reason: str,
) -> str:
    try:
        restored = _restore_source(admitted, restore_targets)
    except OSError as error:
        raise JournalError(f"automatic rollback failed: {error}") from error

    recovery_bytes = _write_json(
        generation_root / "recovery.json",
        {
            "schema_version": SCHEMA_VERSION,
            "generation_id": generation_id,
            "recovered_at": recovered_at,
            "trigger": reason,
            "status": "restored",
            "restored_paths": list(restored),
        },
    )
    verification_payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "generation_id": generation_id,
        "recovery_sha256": hashlib.sha256(recovery_bytes).hexdigest(),
        "status": "rolled-back",
        "reason": reason,
        "changed_paths": sorted(item.normalized_path for item in admitted),
    }
    finished_manifest = dict(manifest)
    finished_manifest["status"] = "rolled-back"
    finished_manifest["verification_sha256"] = hashlib.sha256(
        _json_bytes(verification_payload)
    ).hexdigest()
    manifest_bytes = _write_json(generation_root / "manifest.json", finished_manifest)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    _write_json(
        generation_root / "verification.json",
        {**verification_payload, "manifest_sha256": manifest_sha256},
    )
    return manifest_sha256


def _load_and_verify_generation(journal_root: Path, generation_id: str) -> _VerifiedGeneration:
    """Load one generation and verify its evidence is internally consistent.

    Raises `JournalError` for anything that means the generation cannot be
    trusted: missing evidence files, a manifest hash that no longer matches
    `verification.json`, or a `before`/`after` copy whose bytes no longer
    match its recorded hash. This is the single integrity gate shared by
    `recover_generation` and `copy_generation_to_cloud`.
    """

    _validate_generation_id(generation_id)
    resolved_journal = journal_root.resolve(strict=True)
    if not resolved_journal.is_dir():
        raise JournalError("journal root must be a directory")
    generation_root = resolved_journal / generation_id
    if generation_root.is_symlink():
        raise JournalError(f"generation must not be a symlink: {generation_id}")
    try:
        resolved_generation = generation_root.resolve(strict=True)
    except FileNotFoundError as error:
        raise JournalError(f"generation is incomplete or missing: {generation_id}") from error
    if not resolved_generation.is_relative_to(resolved_journal):
        raise JournalError(f"generation escapes journal root: {generation_id}")
    if any(path.is_symlink() for path in resolved_generation.rglob("*")):
        raise JournalError(f"generation contains a symlink: {generation_id}")
    generation_root = resolved_generation
    manifest_path = generation_root / "manifest.json"
    verification_path = generation_root / "verification.json"
    if not manifest_path.is_file() or not verification_path.is_file():
        raise JournalError(f"generation is incomplete or missing: {generation_id}")

    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest_value = json.loads(manifest_bytes)
        verification_value = json.loads(verification_path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise JournalError(f"generation evidence is corrupted: {generation_id}") from error

    if not isinstance(manifest_value, dict) or not isinstance(verification_value, dict):
        raise JournalError(f"generation evidence has invalid shape: {generation_id}")
    manifest: dict[str, object] = manifest_value
    verification: dict[str, object] = verification_value

    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or verification.get("schema_version") != SCHEMA_VERSION
        or manifest.get("generation_id") != generation_id
        or verification.get("generation_id") != generation_id
    ):
        raise JournalError(f"generation identity mismatch: {generation_id}")

    status = manifest.get("status")
    if status not in {"applied", "rolled-back"} or verification.get("status") != status:
        raise JournalError(f"generation is not consistently completed: {generation_id}")
    if status == "rolled-back":
        recovery_file = generation_root / "recovery.json"
        recovery_sha = verification.get("recovery_sha256")
        if (
            not isinstance(recovery_sha, str)
            or not recovery_file.is_file()
            or hashlib.sha256(recovery_file.read_bytes()).hexdigest() != recovery_sha
        ):
            raise JournalError(f"recovery evidence is corrupted: {generation_id}")

    recorded_hash = verification.get("manifest_sha256")
    if recorded_hash != hashlib.sha256(manifest_bytes).hexdigest():
        raise JournalError(f"manifest integrity check failed: {generation_id}")
    verification_payload = dict(verification)
    verification_payload.pop("manifest_sha256", None)
    if (
        manifest.get("verification_sha256")
        != hashlib.sha256(_json_bytes(verification_payload)).hexdigest()
    ):
        raise JournalError(f"verification integrity check failed: {generation_id}")

    patches = manifest.get("patches")
    if not isinstance(patches, dict):
        raise JournalError(f"generation patch evidence is missing: {generation_id}")
    for patch_name in ("semantic", "mechanical"):
        patch_entry = patches.get(patch_name)
        if not isinstance(patch_entry, dict):
            raise JournalError(f"generation patch evidence is invalid: {generation_id}")
        patch_path = patch_entry.get("path")
        patch_sha = patch_entry.get("sha256")
        if patch_path != f"{patch_name}.patch.diff" or not isinstance(patch_sha, str):
            raise JournalError(f"generation patch evidence is invalid: {generation_id}")
        patch_file = generation_root / patch_path
        if (
            not patch_file.is_file()
            or hashlib.sha256(patch_file.read_bytes()).hexdigest() != patch_sha
        ):
            raise JournalError(f"{patch_name} patch evidence is corrupted: {generation_id}")

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise JournalError(f"generation file evidence is invalid: {generation_id}")
    seen_paths: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise JournalError(f"generation file evidence is invalid: {generation_id}")
        path_value = entry.get("path")
        if not isinstance(path_value, str):
            raise JournalError(f"generation file path is invalid: {generation_id}")
        path = normalize_source_path(path_value).as_posix()
        if path in seen_paths:
            raise JournalError(f"generation contains duplicate path: {path}")
        seen_paths.add(path)
        if entry.get("operation") not in _OPERATIONS:
            raise JournalError(f"generation operation is invalid: {path}")
        before_sha = entry.get("before_sha256")
        after_sha = entry.get("after_sha256")
        if before_sha is not None and (
            not isinstance(before_sha, str) or not _SHA256_PATTERN.fullmatch(before_sha)
        ):
            raise JournalError(f"generation before hash is invalid: {path}")
        if not isinstance(after_sha, str) or not _SHA256_PATTERN.fullmatch(after_sha):
            raise JournalError(f"generation after hash is invalid: {path}")
        if before_sha is not None:
            before_file = generation_root / "before" / path
            if (
                not before_file.is_file()
                or hashlib.sha256(before_file.read_bytes()).hexdigest() != before_sha
            ):
                raise JournalError(f"before evidence is corrupted: {generation_id}/{path}")
        if after_sha is not None:
            after_file = generation_root / "after" / path
            if (
                not after_file.is_file()
                or hashlib.sha256(after_file.read_bytes()).hexdigest() != after_sha
            ):
                raise JournalError(f"after evidence is corrupted: {generation_id}/{path}")

    guards = manifest.get("guards", [])
    if not isinstance(guards, list):
        raise JournalError(f"generation guard evidence is invalid: {generation_id}")
    seen_guards: set[str] = set()
    for entry in guards:
        if not isinstance(entry, dict):
            raise JournalError(f"generation guard evidence is invalid: {generation_id}")
        path_value = entry.get("path")
        sha256 = entry.get("sha256")
        if not isinstance(path_value, str):
            raise JournalError(f"generation guard path is invalid: {generation_id}")
        path = normalize_source_path(path_value).as_posix()
        if path in seen_guards or path in seen_paths:
            raise JournalError(f"generation contains duplicate guarded path: {path}")
        if not isinstance(sha256, str) or not _SHA256_PATTERN.fullmatch(sha256):
            raise JournalError(f"generation guard hash is invalid: {path}")
        seen_guards.add(path)

    return _VerifiedGeneration(generation_root, manifest, verification)


def _recovery_target(source_root: Path, path: str) -> Path:
    relative = normalize_source_path(path)
    candidate = source_root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise JournalError(f"recovery path contains a symlink: {path}")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(source_root):
        raise JournalError(f"recovery path escapes source root: {path}")
    if candidate.exists() and not candidate.is_file():
        raise JournalError(f"recovery target is not a regular file: {path}")
    return candidate


def recover_generation(
    *,
    source_root: Path,
    journal_root: Path,
    generation_id: str,
    recovered_at: str,
) -> RecoveryResult:
    """Restore `before` bytes and append a separate immutable recovery record."""

    _validate_timestamp(recovered_at)
    resolved_source, resolved_journal = _resolved_separate_roots(source_root, journal_root)
    verified = _load_and_verify_generation(resolved_journal, generation_id)
    files_value = verified.manifest.get("files")
    assert isinstance(files_value, list)  # established by the integrity gate
    files: list[dict[str, object]] = files_value

    targets: list[tuple[dict[str, object], Path]] = []
    current_hashes: list[str | None] = []
    before_hashes: list[str | None] = []
    after_hashes: list[str] = []
    for entry in files:
        path = entry["path"]
        assert isinstance(path, str)
        target = _recovery_target(resolved_source, path)
        before_hash = entry["before_sha256"]
        after_hash = entry["after_sha256"]
        assert before_hash is None or isinstance(before_hash, str)
        assert isinstance(after_hash, str)
        targets.append((entry, target))
        current_hashes.append(_current_hash(target))
        before_hashes.append(before_hash)
        after_hashes.append(after_hash)

    if current_hashes == before_hashes:
        return RecoveryResult(
            generation_id=generation_id,
            status="already-recovered",
            reason=None,
            restored_paths=(),
        )
    if current_hashes != after_hashes:
        mismatched = sorted(
            str(entry["path"])
            for (entry, _target), current, after in zip(
                targets, current_hashes, after_hashes, strict=True
            )
            if current != after
        )
        return RecoveryResult(
            generation_id=generation_id,
            status="refused",
            reason=(
                "current source differs from the expected recovery state: " + ", ".join(mismatched)
            ),
            restored_paths=(),
        )

    compact = recovered_at.replace("-", "").replace(":", "")
    recovery_record = f"recoveries/{generation_id}/{compact}"
    record_root = resolved_journal / recovery_record
    if _has_symlink_component(record_root.parent):
        raise JournalError("recovery record path must not contain a symlink")
    record_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        record_root.mkdir()
    except FileExistsError as error:
        raise JournalError(f"recovery record already exists: {recovery_record}") from error

    record_manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "source_generation_id": generation_id,
        "source_manifest_sha256": hashlib.sha256(
            (verified.generation_root / "manifest.json").read_bytes()
        ).hexdigest(),
        "recovered_at": recovered_at,
        "status": "pending",
        "files": [
            {
                "path": entry["path"],
                "before_sha256": entry["before_sha256"],
                "after_sha256": entry["after_sha256"],
            }
            for entry, _target in targets
        ],
    }
    _write_json(record_root / "manifest.json", record_manifest)

    staged: list[tuple[dict[str, object], Path, Path | None]] = []
    try:
        # Close the preflight/write gap before staging any recovery mutation.
        for (entry, target), expected in zip(targets, after_hashes, strict=True):
            path = entry["path"]
            assert isinstance(path, str)
            if _recovery_target(resolved_source, path) != target:
                raise JournalError(f"recovery target changed after preflight: {path}")
            if _current_hash(target) != expected:
                raise JournalError(f"source changed during recovery preflight: {path}")

        for entry, target in targets:
            before_hash = entry["before_sha256"]
            if before_hash is None:
                held = target.with_name(f".{target.name}.recovery-held-{uuid.uuid4().hex}")
                target.replace(held)
                staged.append((entry, target, held))
            else:
                path = entry["path"]
                assert isinstance(path, str)
                before_bytes = (verified.generation_root / "before" / path).read_bytes()
                _atomic_replace_bytes(target, before_bytes)
                staged.append((entry, target, None))

        for entry, target in targets:
            if _current_hash(target) != entry["before_sha256"]:
                raise JournalError(f"recovery verification failed: {entry['path']}")

        for _entry, _target, held in staged:
            if held is not None:
                held.unlink()

        finished_manifest = dict(record_manifest)
        finished_manifest["status"] = "recovered"
        manifest_bytes = _write_json(record_root / "manifest.json", finished_manifest)
        _write_json(
            record_root / "verification.json",
            {
                "schema_version": SCHEMA_VERSION,
                "source_generation_id": generation_id,
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "status": "recovered",
                "restored_paths": sorted(str(entry["path"]) for entry, _ in targets),
            },
        )
    except (OSError, JournalError) as error:
        # Undo the recovery attempt itself so a failed recovery cannot leave a
        # mixture of before and after states.
        for entry, target, held in reversed(staged):
            if held is not None and held.exists():
                held.replace(target)
            else:
                path = entry["path"]
                assert isinstance(path, str)
                after_bytes = (verified.generation_root / "after" / path).read_bytes()
                _atomic_replace_bytes(target, after_bytes)
        raise JournalError(f"explicit recovery failed and was rolled back: {error}") from error

    restored = tuple(sorted(str(entry["path"]) for entry, _ in targets))
    return RecoveryResult(
        generation_id=generation_id,
        status="recovered",
        reason=None,
        restored_paths=restored,
        recovery_record=recovery_record,
    )


def _verify_readback(source_generation_root: Path, copied_root: Path) -> None:
    if any(path.is_symlink() for path in copied_root.rglob("*")):
        raise JournalError("cloud copy contains a symlink")
    source_files = sorted(
        (p for p in source_generation_root.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(source_generation_root).as_posix(),
    )
    copied_files = sorted(
        p.relative_to(copied_root).as_posix() for p in copied_root.rglob("*") if p.is_file()
    )
    expected_files = [path.relative_to(source_generation_root).as_posix() for path in source_files]
    if copied_files != expected_files:
        raise JournalError("cloud copy file inventory mismatch")
    for path in source_files:
        relative = path.relative_to(source_generation_root)
        copied = copied_root / relative
        if not copied.is_file():
            raise JournalError(f"cloud copy is missing file: {relative.as_posix()}")
        if (
            hashlib.sha256(path.read_bytes()).hexdigest()
            != hashlib.sha256(copied.read_bytes()).hexdigest()
        ):
            raise JournalError(f"cloud copy hash mismatch: {relative.as_posix()}")


def copy_generation_to_cloud(
    *,
    journal_root: Path,
    generation_id: str,
    backup_root: Path,
    copied_at: str,
) -> CloudCopyResult:
    """Copy one verified, completed generation to a caller-supplied backup root.

    The copy lands under an incomplete temporary name first, is fully
    read back and hash-verified against the source generation, and only then
    is atomically published (renamed) to its final name. Nothing at
    `backup_root` other than that temporary and final entry is ever created,
    modified or deleted, and an existing final destination is always
    rejected outright.
    """

    _validate_timestamp(copied_at)
    verified = _load_and_verify_generation(journal_root, generation_id)
    generation_root = verified.generation_root.resolve(strict=True)

    if _has_symlink_component(backup_root):
        raise JournalError("cloud backup root must not contain a symlink")
    backup_root.mkdir(parents=True, exist_ok=True)
    resolved_backup_root = backup_root.resolve(strict=True)
    destination_final = resolved_backup_root / generation_id
    if destination_final.exists() or destination_final.is_symlink():
        raise JournalError(f"cloud destination already exists: {generation_id}")

    if (
        generation_root == resolved_backup_root
        or generation_root.is_relative_to(resolved_backup_root)
        or resolved_backup_root.is_relative_to(generation_root)
    ):
        raise JournalError("unsafe overlap between generation and cloud destination")

    resolved_journal_root = journal_root.resolve(strict=True)
    if resolved_backup_root.is_relative_to(
        resolved_journal_root
    ) or resolved_journal_root.is_relative_to(resolved_backup_root):
        raise JournalError("unsafe overlap between journal root and cloud destination")

    temp_destination = resolved_backup_root / f".incoming-{generation_id}-{uuid.uuid4().hex}"
    try:
        shutil.copytree(generation_root, temp_destination)
        # Recheck immutable source evidence after the copy to close the gap
        # between the initial integrity gate and readback verification.
        _load_and_verify_generation(resolved_journal_root, generation_id)
        _verify_readback(generation_root, temp_destination)
    except (OSError, JournalError) as error:
        shutil.rmtree(temp_destination, ignore_errors=True)
        raise JournalError(
            f"cloud copy verification failed for {generation_id}: {error}"
        ) from error

    try:
        temp_destination.replace(destination_final)
    except OSError as error:
        shutil.rmtree(temp_destination, ignore_errors=True)
        raise JournalError(f"cloud publish failed for {generation_id}: {error}") from error

    return CloudCopyResult(
        generation_id=generation_id,
        destination=str(destination_final),
        verified=True,
    )


def evidence_packet(
    result: ApplyResult, recovery: RecoveryResult | None = None
) -> dict[str, object]:
    """Build the bounded RM-003 `O-04` evidence packet for one apply result.

    Contains only relative paths, hashes and fixed status strings: no
    document body and no absolute filesystem path from either the source
    project or the test fixture that produced it.
    """

    packet: dict[str, object] = {
        "generation_id": result.generation_id,
        "workstream_id": result.workstream_id,
        "status": result.status,
        "changed_paths": list(result.changed_paths),
        "checks": list(result.checks),
        "manifest_sha256": result.manifest_sha256,
        "validation_passed": result.validation_passed,
        "unresolved_failure": result.reason if result.status != "applied" else None,
        "recovery": None,
    }
    if recovery is not None:
        packet["recovery"] = {
            "generation_id": recovery.generation_id,
            "status": recovery.status,
            "reason": recovery.reason,
            "restored_paths": list(recovery.restored_paths),
            "recovery_record": recovery.recovery_record,
        }
    return packet
