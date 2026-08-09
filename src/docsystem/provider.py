"""Privacy-safe observations over explicitly pinned projection generations."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from docsystem.projection import PinnedProjection
from docsystem.sections import is_valid_anchor

RESPONSE_SCHEMA_VERSION = 1
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
MAX_RESPONSE_BYTES = 256 * 1024
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ProviderContractError(ValueError):
    """A stable public-contract failure while rendering provider evidence."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EntityObservation:
    """One body-free document or section observation."""

    kind: str
    document_id: str
    anchor: str | None
    anchor_kind: str | None
    content_hash: str
    path: str
    start_line: int
    end_line: int
    visibility: str

    @property
    def key(self) -> tuple[str, str, str]:
        return self.kind, self.document_id, self.anchor or ""

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "document_id": self.document_id,
            "anchor": self.anchor,
            "anchor_kind": self.anchor_kind,
            "content_hash": self.content_hash,
            "path": self.path,
            "lines": {"start": self.start_line, "end": self.end_line},
            "visibility": self.visibility,
        }


@dataclass(frozen=True)
class EntityChange:
    """One exact stable-identity comparison result."""

    classification: str
    before: EntityObservation | None
    after: EntityObservation | None

    @property
    def key(self) -> tuple[str, str, str]:
        value = self.before if self.before is not None else self.after
        assert value is not None
        return value.key

    def as_dict(self) -> dict[str, object]:
        value = self.before if self.before is not None else self.after
        assert value is not None
        return {
            "classification": self.classification,
            "entity": {
                "kind": value.kind,
                "document_id": value.document_id,
                "anchor": value.anchor,
            },
            "before": None if self.before is None else self.before.as_dict(),
            "after": None if self.after is None else self.after.as_dict(),
        }


def _canonical_json(value: object, *, indent: int | None = None) -> str:
    separators = (",", ":") if indent is None else None
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=indent,
        sort_keys=True,
        separators=separators,
    )


def encode_response(value: dict[str, object]) -> str:
    """Encode one deterministic response with the CLI-wide trailing newline."""

    return _canonical_json(value, indent=2) + "\n"


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _query_hash(kind: str, generations: Sequence[str]) -> str:
    return _sha(
        {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "kind": kind,
            "generations": list(generations),
        }
    )


def _encode_cursor(query_hash: str, offset: int) -> str:
    body = _canonical_json({"offset": offset, "query": query_hash}).encode("utf-8")
    checksum = hashlib.sha256(body).hexdigest()[:16].encode("ascii")
    return base64.urlsafe_b64encode(body + b"." + checksum).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None, query_hash: str) -> int:
    if cursor is None:
        return 0
    if not isinstance(cursor, str) or not cursor:
        raise ProviderContractError("page-invalid", "cursor must be non-empty")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        body, checksum = raw.rsplit(b".", 1)
        if hashlib.sha256(body).hexdigest()[:16].encode("ascii") != checksum:
            raise ValueError("checksum")
        value = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ProviderContractError("page-invalid", "cursor is invalid") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"offset", "query"}
        or value.get("query") != query_hash
        or not isinstance(value.get("offset"), int)
        or isinstance(value.get("offset"), bool)
        or value["offset"] < 0
    ):
        raise ProviderContractError(
            "page-invalid", "cursor does not belong to this pinned query"
        )
    return int(value["offset"])


def _valid_hash(value: object) -> str:
    if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
        raise ProviderContractError(
            "generation-corrupt", "entity content hash is invalid"
        )
    return value


def _valid_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ProviderContractError(
            "generation-corrupt", "entity path is not a relative POSIX path"
        )
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ProviderContractError(
            "generation-corrupt", "entity path is not a relative POSIX path"
        )
    return path.as_posix()


def _positive_line(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ProviderContractError(
            "generation-corrupt", "entity line hint is invalid"
        )
    return value


def observations(snapshot: PinnedProjection) -> tuple[EntityObservation, ...]:
    """Return every stable body-free observation in deterministic key order."""

    visibility = snapshot.provider.get("visibility")
    if visibility not in {"private", "public"}:
        raise ProviderContractError(
            "generation-corrupt", "provider visibility is invalid"
        )
    result: list[EntityObservation] = []
    for document_id, document in sorted(snapshot.documents.items()):
        path = _valid_path(document.get("path"))
        line_count = _positive_line(document.get("line_count"))
        result.append(
            EntityObservation(
                "document",
                document_id,
                None,
                None,
                _valid_hash(document.get("source_sha256")),
                path,
                1,
                line_count,
                visibility,
            )
        )
        sections = document.get("sections")
        if not isinstance(sections, dict):
            raise ProviderContractError(
                "generation-corrupt", f"section map is invalid: {document_id}"
            )
        for anchor, section in sorted(sections.items()):
            if not isinstance(anchor, str) or not is_valid_anchor(anchor):
                raise ProviderContractError(
                    "generation-corrupt", f"section anchor is invalid: {document_id}"
                )
            if not isinstance(section, dict):
                raise ProviderContractError(
                    "generation-corrupt", f"section record is invalid: {document_id}"
                )
            anchor_kind = section.get("anchor_kind")
            if anchor_kind not in {"explicit", "generated"}:
                raise ProviderContractError(
                    "generation-corrupt",
                    f"section anchor kind is invalid: {document_id}#{anchor}",
                )
            start = _positive_line(section.get("start_line"))
            end = _positive_line(section.get("end_line"))
            if end < start or end > line_count:
                raise ProviderContractError(
                    "generation-corrupt",
                    f"section line range is invalid: {document_id}#{anchor}",
                )
            result.append(
                EntityObservation(
                    "section",
                    document_id,
                    anchor,
                    anchor_kind,
                    _valid_hash(section.get("sha256")),
                    path,
                    start,
                    end,
                    visibility,
                )
            )
    return tuple(sorted(result, key=lambda item: item.key))


def compare_observations(
    before: Sequence[EntityObservation], after: Sequence[EntityObservation]
) -> tuple[tuple[EntityChange, ...], dict[str, int]]:
    """Compare two complete observation inventories by exact stable identity."""

    before_map = {item.key: item for item in before}
    after_map = {item.key: item for item in after}
    if len(before_map) != len(before) or len(after_map) != len(after):
        raise ProviderContractError(
            "generation-ambiguous", "a stable entity has multiple observations"
        )
    counts = {
        "unchanged": 0,
        "relocated": 0,
        "changed": 0,
        "missing": 0,
        "added": 0,
        "ambiguous": 0,
    }
    changes: list[EntityChange] = []
    for key in sorted(set(before_map) | set(after_map)):
        old = before_map.get(key)
        new = after_map.get(key)
        if old is None:
            classification = "added"
        elif new is None:
            classification = "missing"
        elif old.content_hash != new.content_hash:
            classification = "changed"
        elif (
            old.path,
            old.start_line,
            old.end_line,
        ) != (
            new.path,
            new.start_line,
            new.end_line,
        ):
            classification = "relocated"
        else:
            counts["unchanged"] += 1
            continue
        counts[classification] += 1
        changes.append(EntityChange(classification, old, new))
    return tuple(changes), counts


def _snapshot_header(snapshot: PinnedProjection) -> dict[str, object]:
    provider = snapshot.provider
    coverage = provider.get("coverage")
    scope = provider.get("scope")
    boundaries = provider.get("boundaries")
    capabilities = provider.get("capabilities")
    if (
        not isinstance(provider.get("id"), str)
        or not isinstance(coverage, dict)
        or not isinstance(scope, dict)
        or not isinstance(boundaries, list)
        or any(not isinstance(item, str) for item in boundaries)
        or boundaries != sorted(set(boundaries))
        or not isinstance(capabilities, list)
        or any(not isinstance(item, str) for item in capabilities)
        or capabilities != sorted(set(capabilities))
    ):
        raise ProviderContractError(
            "generation-corrupt", "provider descriptor is invalid"
        )
    return {
        "provider_id": provider["id"],
        "visibility": provider["visibility"],
        "generation": snapshot.generation,
        "content_hash": snapshot.generation,
        "capabilities": capabilities,
        "coverage": coverage,
        "scope": scope,
        "boundaries": boundaries,
    }


def _validate_page_size(page_size: int) -> int:
    if (
        not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or not 1 <= page_size <= MAX_PAGE_SIZE
    ):
        raise ProviderContractError(
            "page-invalid", f"page size must be between 1 and {MAX_PAGE_SIZE}"
        )
    return page_size


def _bounded_page(
    items: Sequence[Any],
    *,
    cursor: str | None,
    page_size: int,
    query_hash: str,
    build: Callable[[list[Any], dict[str, object]], dict[str, object]],
) -> dict[str, object]:
    page_size = _validate_page_size(page_size)
    offset = _decode_cursor(cursor, query_hash)
    if offset > len(items) or (offset == len(items) and len(items) != 0):
        raise ProviderContractError("page-invalid", "cursor is past the result set")
    selected = list(items[offset : offset + page_size])
    while True:
        next_offset = offset + len(selected)
        page = {
            "cursor": None if offset == 0 else _encode_cursor(query_hash, offset),
            "requested_page_size": page_size,
            "effective_page_size": len(selected),
            "returned": len(selected),
            "total": len(items),
            "next_cursor": (
                _encode_cursor(query_hash, next_offset)
                if next_offset < len(items)
                else None
            ),
            "max_response_bytes": MAX_RESPONSE_BYTES,
        }
        response = build(selected, page)
        if len(encode_response(response).encode("utf-8")) <= MAX_RESPONSE_BYTES:
            return response
        if len(selected) <= 1:
            raise ProviderContractError(
                "page-too-large",
                f"one entity cannot fit within {MAX_RESPONSE_BYTES} UTF-8 bytes",
            )
        selected.pop()


def snapshot_response(
    snapshot: PinnedProjection,
    *,
    cursor: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, object]:
    inventory = observations(snapshot)
    query_hash = _query_hash("provider-snapshot", (snapshot.generation,))

    def build(
        selected: list[EntityObservation], page: dict[str, object]
    ) -> dict[str, object]:
        return {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "kind": "provider-snapshot",
            "snapshot": _snapshot_header(snapshot),
            "page": page,
            "observations": [item.as_dict() for item in selected],
        }

    return _bounded_page(
        inventory,
        cursor=cursor,
        page_size=page_size,
        query_hash=query_hash,
        build=build,
    )


def compare_response(
    before: PinnedProjection,
    after: PinnedProjection,
    *,
    cursor: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, object]:
    if before.provider.get("id") != after.provider.get("id"):
        raise ProviderContractError(
            "provider-mismatch", "pinned generations belong to different providers"
        )
    changes, counts = compare_observations(observations(before), observations(after))
    query_hash = _query_hash(
        "provider-compare", (before.generation, after.generation)
    )

    def build(
        selected: list[EntityChange], page: dict[str, object]
    ) -> dict[str, object]:
        return {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "kind": "provider-compare",
            "provider_id": before.provider["id"],
            "before": _snapshot_header(before),
            "after": _snapshot_header(after),
            "summary": counts,
            "page": page,
            "changes": [item.as_dict() for item in selected],
        }

    return _bounded_page(
        changes,
        cursor=cursor,
        page_size=page_size,
        query_hash=query_hash,
        build=build,
    )
