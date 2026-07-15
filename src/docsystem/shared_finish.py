"""Strict declarations for read-only shared workstream finish packets."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from docsystem.journal import JournalError, validate_workstream_id
from docsystem.workspace import SOURCE_NAME_PATTERN

SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 64 * 1024
MAX_PARTICIPANTS = 100
STATUSES = frozenset({"applied", "blocked", "skipped"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REASON = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_ROOT_KEYS = frozenset({"schema_version", "workstream_id", "participants"})
_PARTICIPANT_KEYS = frozenset(
    {"source", "status", "generation", "manifest_sha256", "reason"}
)


class SharedFinishError(ValueError):
    """A shared finish declaration is malformed or ambiguous."""


@dataclass(frozen=True)
class ParticipantDeclaration:
    source: str
    status: str
    generation: str | None = None
    manifest_sha256: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class SharedFinishRecord:
    workstream_id: str
    participants: tuple[ParticipantDeclaration, ...]


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise SharedFinishError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _exact_keys(value: dict[str, object], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise SharedFinishError(f"{label} has unknown key(s): {', '.join(unknown)}")


def load_shared_finish_record(path: Path) -> SharedFinishRecord:
    """Load one bounded JSON declaration without accepting last-key-wins data."""

    try:
        raw_bytes = path.read_bytes()
    except OSError as error:
        raise SharedFinishError("shared finish record is unreadable") from error
    if len(raw_bytes) > MAX_RECORD_BYTES:
        raise SharedFinishError("shared finish record exceeds 65536 bytes")
    try:
        raw = json.loads(raw_bytes, object_pairs_hook=_reject_duplicate_keys)
    except UnicodeDecodeError as error:
        raise SharedFinishError("shared finish record must be UTF-8") from error
    except json.JSONDecodeError as error:
        raise SharedFinishError(f"invalid shared finish JSON: {error.msg}") from error
    if not isinstance(raw, dict):
        raise SharedFinishError("shared finish record must be a JSON object")
    _exact_keys(raw, _ROOT_KEYS, "shared finish record")
    if raw.get("schema_version") != SCHEMA_VERSION or isinstance(
        raw.get("schema_version"), bool
    ):
        raise SharedFinishError("shared finish schema_version must be exactly 1")
    workstream_id = raw.get("workstream_id")
    try:
        validate_workstream_id(workstream_id)  # type: ignore[arg-type]
    except JournalError as error:
        raise SharedFinishError(str(error)) from error
    participants_raw = raw.get("participants")
    if not isinstance(participants_raw, list) or not participants_raw:
        raise SharedFinishError("participants must be a non-empty array")
    if len(participants_raw) > MAX_PARTICIPANTS:
        raise SharedFinishError("participants must contain at most 100 entries")

    participants: list[ParticipantDeclaration] = []
    seen: set[str] = set()
    for index, item in enumerate(participants_raw):
        label = f"participants[{index}]"
        if not isinstance(item, dict):
            raise SharedFinishError(f"{label} must be an object")
        _exact_keys(item, _PARTICIPANT_KEYS, label)
        source = item.get("source")
        if not isinstance(source, str) or not SOURCE_NAME_PATTERN.fullmatch(source):
            raise SharedFinishError(
                f"{label}.source must match {SOURCE_NAME_PATTERN.pattern}"
            )
        if source in seen:
            raise SharedFinishError(f"duplicate participant source: {source}")
        seen.add(source)
        status = item.get("status")
        if status not in STATUSES:
            raise SharedFinishError(
                f"{label}.status must be 'applied', 'blocked' or 'skipped'"
            )
        generation = item.get("generation")
        manifest_sha256 = item.get("manifest_sha256")
        reason = item.get("reason")
        if status == "applied":
            if not isinstance(generation, str) or not generation:
                raise SharedFinishError(f"{label}.generation is required when applied")
            if not isinstance(manifest_sha256, str) or not _SHA256.fullmatch(
                manifest_sha256
            ):
                raise SharedFinishError(
                    f"{label}.manifest_sha256 must be 64 lowercase hex characters"
                )
            if reason is not None:
                raise SharedFinishError(f"{label}.reason is not allowed when applied")
        else:
            if generation is not None or manifest_sha256 is not None:
                raise SharedFinishError(
                    f"{label} cannot set generation or manifest_sha256 when {status}"
                )
            if not isinstance(reason, str) or not _REASON.fullmatch(reason):
                raise SharedFinishError(
                    f"{label}.reason must be a lowercase reason slug"
                )
        participants.append(
            ParticipantDeclaration(source, str(status), generation, manifest_sha256, reason)
        )
    return SharedFinishRecord(str(workstream_id), tuple(participants))
