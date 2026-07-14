"""Immutable provider-neutral execution handoff packet helpers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docsystem.admission import AdmissionError, normalize_source_path

SCHEMA_VERSION = 1
MAX_PACKET_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 128 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ExecutionPacketError(ValueError):
    """A deterministic execution packet validation failure."""


@dataclass(frozen=True)
class ExecutionChangedFile:
    path: str
    sha256: str | None


@dataclass(frozen=True)
class ExecutionResult:
    workstream_id: str
    packet_sha256: str
    changed_files: tuple[ExecutionChangedFile, ...]


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def seal_packet(payload: dict[str, object]) -> dict[str, object]:
    """Return a packet root sealed over schema version and all payload fields."""

    root = {"schema_version": SCHEMA_VERSION, **payload}
    digest = hashlib.sha256(_canonical_bytes(root)).hexdigest()
    sealed = {**root, "packet_sha256": digest}
    if len(_canonical_bytes(sealed)) > MAX_PACKET_BYTES:
        raise ExecutionPacketError(
            f"execution packet exceeds the bounded size of {MAX_PACKET_BYTES} bytes"
        )
    return sealed


def load_packet(path: Path) -> dict[str, object]:
    """Load one bounded packet and verify its self-contained integrity hash."""

    try:
        data = path.read_bytes()
        if len(data) > MAX_PACKET_BYTES:
            raise ExecutionPacketError(
                f"execution packet exceeds the bounded size of {MAX_PACKET_BYTES} bytes"
            )
        raw = json.loads(data.decode("utf-8"))
    except ExecutionPacketError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExecutionPacketError(f"cannot read execution packet: {error}") from error
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise ExecutionPacketError("execution packet must be an object with string keys")
    item: dict[str, Any] = raw
    if item.get("schema_version") != SCHEMA_VERSION:
        raise ExecutionPacketError("unsupported execution packet schema_version")
    digest = item.get("packet_sha256")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise ExecutionPacketError("packet_sha256 must be a lowercase SHA-256")
    unsigned = {key: value for key, value in item.items() if key != "packet_sha256"}
    actual = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    if actual != digest:
        raise ExecutionPacketError("execution packet integrity hash does not match")
    return dict(item)


def load_execution_result(path: Path) -> ExecutionResult:
    """Load bounded caller-declared changed-file evidence."""

    try:
        data = path.read_bytes()
        if len(data) > MAX_RESULT_BYTES:
            raise ExecutionPacketError(
                f"execution result exceeds the bounded size of {MAX_RESULT_BYTES} bytes"
            )
        raw = json.loads(data.decode("utf-8"))
    except ExecutionPacketError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExecutionPacketError(f"cannot read execution result: {error}") from error
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise ExecutionPacketError("execution result must be an object with string keys")
    required = {"schema_version", "workstream_id", "packet_sha256", "changed_files"}
    missing = required - set(raw)
    unknown = set(raw) - required
    if missing:
        raise ExecutionPacketError(
            "execution result is missing required key(s): "
            + ", ".join(sorted(missing))
        )
    if unknown:
        raise ExecutionPacketError(
            "execution result has unknown key(s): " + ", ".join(sorted(unknown))
        )
    if raw["schema_version"] != SCHEMA_VERSION:
        raise ExecutionPacketError("unsupported execution result schema_version")
    workstream_id = raw["workstream_id"]
    if not isinstance(workstream_id, str) or not workstream_id.strip():
        raise ExecutionPacketError("execution result workstream_id must be non-empty")
    packet_sha256 = raw["packet_sha256"]
    if not isinstance(packet_sha256, str) or not SHA256_PATTERN.fullmatch(
        packet_sha256
    ):
        raise ExecutionPacketError(
            "execution result packet_sha256 must be a lowercase SHA-256"
        )
    changed_raw = raw["changed_files"]
    if not isinstance(changed_raw, list) or len(changed_raw) > 100:
        raise ExecutionPacketError("execution result changed_files must be a bounded list")
    changed: list[ExecutionChangedFile] = []
    seen: set[str] = set()
    for index, value in enumerate(changed_raw):
        field = f"changed_files[{index}]"
        if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
            raise ExecutionPacketError(f"{field} must contain only path and sha256")
        try:
            item_path = normalize_source_path(value["path"], f"{field}.path")
        except AdmissionError as error:
            raise ExecutionPacketError(str(error)) from error
        if item_path in seen:
            raise ExecutionPacketError(f"duplicate changed file path: {item_path}")
        seen.add(item_path)
        raw_digest = value["sha256"]
        digest: str | None
        if raw_digest is None:
            digest = None
        elif isinstance(raw_digest, str) and SHA256_PATTERN.fullmatch(raw_digest):
            digest = raw_digest
        else:
            raise ExecutionPacketError(
                f"{field}.sha256 must be null or a lowercase SHA-256"
            )
        changed.append(ExecutionChangedFile(item_path, digest))
    return ExecutionResult(
        workstream_id.strip(),
        packet_sha256,
        tuple(sorted(changed, key=lambda item: item.path)),
    )
