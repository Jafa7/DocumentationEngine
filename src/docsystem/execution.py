"""Immutable provider-neutral execution handoff packet helpers."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_PACKET_BYTES = 2 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ExecutionPacketError(ValueError):
    """A deterministic execution packet validation failure."""


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
