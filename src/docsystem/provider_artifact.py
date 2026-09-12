"""Complete, transport-neutral artifacts for documentation provider evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path, PurePosixPath

from docsystem.projection import PinnedProjection
from docsystem.provider import (
    MAX_PAGE_SIZE,
    MAX_RESPONSE_BYTES,
    prepare_compare_query,
    prepare_snapshot_query,
    prepared_compare_response,
    prepared_snapshot_response,
)
from docsystem.sections import is_valid_anchor
from docsystem.strict_json import DuplicateJsonMemberError, loads_unique_json

ARTIFACT_SCHEMA_VERSION = 1
PROTOCOL_NAME = "traceability-provider"
PROTOCOL_VERSION = 1
PROTOCOL_CAPABILITY = "traceability-provider-v1"
GENERATION_REQUIRED_CAPABILITIES = {
    "bounded-entity-observations-v1",
    "pinned-generation-compare-v1",
}
SNAPSHOT_ARTIFACT_KIND = "provider-snapshot-artifact"
COMPARE_ARTIFACT_KIND = "provider-compare-artifact"
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ProviderArtifactError(ValueError):
    """A stable process-boundary failure for provider artifact operations."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical_json(value: object, *, indent: int | None = None) -> str:
    separators = (",", ":") if indent is None else None
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=indent,
        sort_keys=True,
        separators=separators,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def encode_artifact(value: dict[str, object]) -> str:
    """Return deterministic human-inspectable JSON bytes for one artifact."""

    return _canonical_json(value, indent=2) + "\n"


def _policy() -> dict[str, object]:
    return {
        "assembly": "bounded-pages",
        "completeness": "complete-or-fail",
        "page_size": MAX_PAGE_SIZE,
        "page_max_response_bytes": MAX_RESPONSE_BYTES,
        "ordering": "kind-document-id-canonical-anchor",
        "markdown_bodies": "omitted",
    }


def _protocol() -> dict[str, object]:
    return {
        "name": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
        "capabilities": [PROTOCOL_CAPABILITY],
    }


def _seal(value: dict[str, object]) -> dict[str, object]:
    return {
        **value,
        "content_digest": {
            "algorithm": "sha256",
            "value": _digest(value),
        },
    }


def _collect_pages(
    fetch: Callable[[str | None], dict[str, object]], item_key: str
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    items: list[dict[str, object]] = []
    pages: list[dict[str, object]] = []
    cursor: str | None = None
    seen: set[str] = set()
    expected_total: int | None = None
    while True:
        response = fetch(cursor)
        page = response.get("page")
        current = response.get(item_key)
        if not isinstance(page, dict) or not isinstance(current, list):
            raise ProviderArtifactError(
                "artifact-incomplete", "provider page is structurally incomplete"
            )
        if any(not isinstance(item, dict) for item in current):
            raise ProviderArtifactError(
                "artifact-incomplete", "provider page contains an invalid entity"
            )
        total = page.get("total")
        returned = page.get("returned")
        if (
            not isinstance(total, int)
            or isinstance(total, bool)
            or total < 0
            or returned != len(current)
        ):
            raise ProviderArtifactError(
                "artifact-incomplete", "provider page count evidence is invalid"
            )
        if expected_total is None:
            expected_total = total
        elif expected_total != total:
            raise ProviderArtifactError(
                "artifact-incomplete", "provider page totals changed during export"
            )
        pages.append(response)
        items.extend(current)
        next_cursor = page.get("next_cursor")
        if next_cursor is None:
            break
        if not isinstance(next_cursor, str) or next_cursor in seen:
            raise ProviderArtifactError(
                "artifact-incomplete", "provider pagination did not make progress"
            )
        seen.add(next_cursor)
        cursor = next_cursor
    if expected_total != len(items):
        raise ProviderArtifactError(
            "artifact-incomplete", "provider pages do not reconstruct the full result"
        )
    return items, pages


def snapshot_artifact(snapshot: PinnedProjection) -> dict[str, object]:
    """Assemble every bounded page into one complete immutable snapshot artifact."""

    query = prepare_snapshot_query(snapshot)
    observations, pages = _collect_pages(
        lambda cursor: prepared_snapshot_response(
            query, cursor=cursor, page_size=MAX_PAGE_SIZE
        ),
        "observations",
    )
    first = pages[0]
    header = first.get("snapshot")
    if not isinstance(header, dict):
        raise ProviderArtifactError(
            "artifact-incomplete", "provider snapshot header is missing"
        )
    for page in pages[1:]:
        if page.get("snapshot") != header:
            raise ProviderArtifactError(
                "artifact-incomplete", "provider snapshot header changed between pages"
            )
    value: dict[str, object] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_kind": SNAPSHOT_ARTIFACT_KIND,
        "protocol": _protocol(),
        "provider": {
            "id": header["provider_id"],
            "contract_version": PROTOCOL_VERSION,
            "visibility": header["visibility"],
            "capabilities": header["capabilities"],
        },
        "query": {
            "kind": "snapshot",
            "provider_id": header["provider_id"],
            "generation": header["generation"],
            "scope": header["scope"],
        },
        "completeness": {
            "complete": True,
            "coverage": header["coverage"],
            "boundaries": header["boundaries"],
            "observations": len(observations),
        },
        "policy": _policy(),
        "observations": observations,
    }
    artifact = _seal(value)
    verify_artifact(artifact)
    return artifact


def compare_artifact(
    before: PinnedProjection, after: PinnedProjection
) -> dict[str, object]:
    """Assemble every bounded page into one complete immutable compare artifact."""

    query = prepare_compare_query(before, after)
    changes, pages = _collect_pages(
        lambda cursor: prepared_compare_response(
            query, cursor=cursor, page_size=MAX_PAGE_SIZE
        ),
        "changes",
    )
    first = pages[0]
    before_header = first.get("before")
    after_header = first.get("after")
    summary = first.get("summary")
    provider_id = first.get("provider_id")
    if (
        not isinstance(before_header, dict)
        or not isinstance(after_header, dict)
        or not isinstance(summary, dict)
        or not isinstance(provider_id, str)
    ):
        raise ProviderArtifactError(
            "artifact-incomplete", "provider comparison header is missing"
        )
    for page in pages[1:]:
        if any(
            page.get(key) != first.get(key)
            for key in ("provider_id", "before", "after", "summary")
        ):
            raise ProviderArtifactError(
                "artifact-incomplete", "provider comparison header changed between pages"
            )
    value: dict[str, object] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_kind": COMPARE_ARTIFACT_KIND,
        "protocol": _protocol(),
        "provider": {
            "id": provider_id,
            "contract_version": PROTOCOL_VERSION,
            "visibility": after_header["visibility"],
            "capabilities": sorted(
                set(before_header["capabilities"])
                & set(after_header["capabilities"])
            ),
        },
        "query": {
            "kind": "compare",
            "before": {
                "provider_id": before_header["provider_id"],
                "generation": before_header["generation"],
                "scope": before_header["scope"],
            },
            "after": {
                "provider_id": after_header["provider_id"],
                "generation": after_header["generation"],
                "scope": after_header["scope"],
            },
        },
        "completeness": {
            "complete": True,
            "before": {
                "coverage": before_header["coverage"],
                "boundaries": before_header["boundaries"],
                "visibility": before_header["visibility"],
                "capabilities": before_header["capabilities"],
            },
            "after": {
                "coverage": after_header["coverage"],
                "boundaries": after_header["boundaries"],
                "visibility": after_header["visibility"],
                "capabilities": after_header["capabilities"],
            },
            "changes": len(changes),
            "summary": summary,
        },
        "policy": _policy(),
        "changes": changes,
    }
    artifact = _seal(value)
    verify_artifact(artifact)
    return artifact


def write_artifact(path: Path, value: dict[str, object]) -> None:
    """Atomically create one deterministic artifact without replacing evidence."""

    verify_artifact(value)
    temporary: Path | None = None
    try:
        destination = path.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ProviderArtifactError(
                "artifact-output-exists", f"refusing to replace artifact: {path}"
            )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(encode_artifact(value))
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.link(temporary, destination)
        except FileExistsError as error:
            raise ProviderArtifactError(
                "artifact-output-exists", f"refusing to replace artifact: {path}"
            ) from error
        except OSError as error:
            raise ProviderArtifactError(
                "artifact-write-failed", f"cannot atomically create artifact: {path}"
            ) from error
        completed_temporary = temporary
        temporary = None
        with suppress(OSError):
            completed_temporary.unlink(missing_ok=True)
    except ProviderArtifactError:
        raise
    except OSError as error:
        raise ProviderArtifactError(
            "artifact-write-failed", f"cannot atomically create artifact: {path}"
        ) from error
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def _artifact_error(message: str) -> ProviderArtifactError:
    return ProviderArtifactError("artifact-corrupt", message)


def _nonnegative_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _artifact_error(f"{label} is invalid")
    return value


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
        raise _artifact_error(f"{label} is invalid")
    return value


def _observation(value: object) -> tuple[tuple[str, str, str], dict[str, object]]:
    if not isinstance(value, dict):
        raise _artifact_error("entity observation is not an object")
    kind = value.get("kind")
    document_id = value.get("document_id")
    anchor = value.get("anchor")
    anchor_kind = value.get("anchor_kind")
    path_value = value.get("path")
    lines = value.get("lines")
    visibility = value.get("visibility")
    if kind not in {"document", "section"} or not isinstance(document_id, str) or not document_id:
        raise _artifact_error("entity stable identity is invalid")
    if kind == "document":
        if anchor is not None or anchor_kind is not None:
            raise _artifact_error("document observation has a section anchor")
    elif (
        not isinstance(anchor, str)
        or not is_valid_anchor(anchor)
        or anchor_kind not in {"explicit", "generated"}
    ):
        raise _artifact_error("section observation anchor is invalid")
    _hash(value.get("content_hash"), "entity content hash")
    if not isinstance(path_value, str) or not path_value or "\\" in path_value:
        raise _artifact_error("entity path is invalid")
    path = PurePosixPath(path_value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != path_value:
        raise _artifact_error("entity path is invalid")
    if not isinstance(lines, dict):
        raise _artifact_error("entity line hints are invalid")
    start = _nonnegative_int(lines.get("start"), "entity start line")
    end = _nonnegative_int(lines.get("end"), "entity end line")
    if start < 1 or end < start:
        raise _artifact_error("entity line hints are invalid")
    if visibility not in {"private", "public"}:
        raise _artifact_error("entity visibility is invalid")
    return (kind, document_id, anchor or ""), value


def _validate_observation_relationships(
    parsed: list[tuple[tuple[str, str, str], dict[str, object]]],
    label: str,
) -> None:
    """Prove that every section belongs to matching document evidence."""

    documents = {
        key[1]: item for key, item in parsed if key[0] == "document"
    }
    for key, item in parsed:
        if key[0] == "document":
            if item["lines"]["start"] != 1:
                raise ProviderArtifactError(
                    "artifact-incomplete", f"{label} document line range is invalid"
                )
            continue
        document = documents.get(key[1])
        if document is None:
            raise ProviderArtifactError(
                "artifact-incomplete", f"{label} section has no document observation"
            )
        if (
            item["path"] != document["path"]
            or item["visibility"] != document["visibility"]
            or item["lines"]["end"] > document["lines"]["end"]
        ):
            raise ProviderArtifactError(
                "artifact-incomplete",
                f"{label} section conflicts with its document observation",
            )


def _coverage(value: object, label: str) -> tuple[int, int]:
    if not isinstance(value, dict):
        raise ProviderArtifactError("artifact-incomplete", f"{label} coverage is missing")
    documents = _nonnegative_int(value.get("documents"), f"{label} documents")
    sections = _nonnegative_int(value.get("sections"), f"{label} sections")
    _nonnegative_int(value.get("excluded_markdown"), f"{label} excluded markdown")
    unmapped = _nonnegative_int(value.get("unmapped_markdown"), f"{label} unmapped markdown")
    if unmapped:
        raise ProviderArtifactError(
            "artifact-incomplete", f"{label} contains unmapped Markdown"
        )
    return documents, sections


def _scope(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), str)
        for key in ("catalog", "path_base", "sections")
    ):
        raise ProviderArtifactError("artifact-incomplete", f"{label} scope is invalid")
    return value


def _boundaries(value: object, label: str) -> None:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) for item in value)
        or value != sorted(set(value))
    ):
        raise ProviderArtifactError(
            "artifact-incomplete", f"{label} boundaries are invalid"
        )


def _validate_protocol(value: object) -> None:
    if not isinstance(value, dict):
        raise ProviderArtifactError("artifact-incompatible", "artifact protocol is missing")
    capabilities = value.get("capabilities")
    if (
        value.get("name") != PROTOCOL_NAME
        or value.get("version") != PROTOCOL_VERSION
        or not isinstance(capabilities, list)
        or any(not isinstance(item, str) for item in capabilities)
        or PROTOCOL_CAPABILITY not in capabilities
    ):
        raise ProviderArtifactError(
            "artifact-incompatible", "artifact protocol or capability is unsupported"
        )


def _validate_provider(value: object) -> tuple[str, str, set[str]]:
    if not isinstance(value, dict):
        raise ProviderArtifactError("artifact-mixed-provider", "provider identity is missing")
    provider_id = value.get("id")
    visibility = value.get("visibility")
    capabilities = value.get("capabilities")
    if (
        not isinstance(provider_id, str)
        or not provider_id
        or value.get("contract_version") != PROTOCOL_VERSION
        or visibility not in {"private", "public"}
        or not isinstance(capabilities, list)
        or any(not isinstance(item, str) for item in capabilities)
        or not GENERATION_REQUIRED_CAPABILITIES.issubset(capabilities)
    ):
        raise ProviderArtifactError("artifact-mixed-provider", "provider identity is invalid")
    return provider_id, visibility, set(capabilities)


def _operand_provider(value: dict[str, object], label: str) -> tuple[str, set[str]]:
    visibility = value.get("visibility")
    capabilities = value.get("capabilities")
    if (
        visibility not in {"private", "public"}
        or not isinstance(capabilities, list)
        or any(not isinstance(item, str) for item in capabilities)
        or not GENERATION_REQUIRED_CAPABILITIES.issubset(capabilities)
    ):
        raise ProviderArtifactError(
            "artifact-mixed-provider", f"{label} provider evidence is invalid"
        )
    return visibility, set(capabilities)


def _validate_policy(value: object) -> None:
    if not isinstance(value, dict) or any(
        value.get(key) != expected
        for key, expected in _policy().items()
    ):
        raise ProviderArtifactError(
            "artifact-incompatible", "artifact assembly policy is unsupported"
        )


def _verify_snapshot(
    value: dict[str, object], provider_id: str, provider_visibility: str
) -> dict[str, object]:
    query = value.get("query")
    completeness = value.get("completeness")
    raw = value.get("observations")
    if (
        not isinstance(query, dict)
        or query.get("kind") != "snapshot"
        or query.get("provider_id") != provider_id
        or not isinstance(query.get("scope"), dict)
        or not isinstance(completeness, dict)
        or completeness.get("complete") is not True
        or not isinstance(raw, list)
    ):
        raise ProviderArtifactError("artifact-incomplete", "snapshot artifact is incomplete")
    generation = _hash(query.get("generation"), "snapshot generation")
    _scope(query.get("scope"), "snapshot")
    documents, sections = _coverage(completeness.get("coverage"), "snapshot")
    _boundaries(completeness.get("boundaries"), "snapshot")
    if completeness.get("observations") != len(raw):
        raise ProviderArtifactError("artifact-incomplete", "snapshot observation count differs")
    parsed = [_observation(item) for item in raw]
    if any(item["visibility"] != provider_visibility for _, item in parsed):
        raise ProviderArtifactError(
            "artifact-mixed-provider", "snapshot mixes provider visibility"
        )
    keys = [key for key, _ in parsed]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise ProviderArtifactError(
            "artifact-incomplete", "snapshot observations are unordered or duplicated"
        )
    actual = Counter(key[0] for key in keys)
    if actual["document"] != documents or actual["section"] != sections:
        raise ProviderArtifactError("artifact-incomplete", "snapshot coverage differs")
    _validate_observation_relationships(parsed, "snapshot")
    return {
        "artifact_kind": SNAPSHOT_ARTIFACT_KIND,
        "provider_id": provider_id,
        "generations": [generation],
        "observations": len(raw),
        "changes": 0,
    }


def _expected_classification(
    before: dict[str, object] | None, after: dict[str, object] | None
) -> str:
    if before is None:
        return "added"
    if after is None:
        return "missing"
    if before["content_hash"] != after["content_hash"]:
        return "changed"
    if (before["path"], before["lines"]) != (after["path"], after["lines"]):
        return "relocated"
    raise _artifact_error("comparison contains an unchanged entity")


def _verify_compare(
    value: dict[str, object],
    provider_id: str,
    provider_visibility: str,
    provider_capabilities: set[str],
) -> dict[str, object]:
    query = value.get("query")
    completeness = value.get("completeness")
    raw = value.get("changes")
    if (
        not isinstance(query, dict)
        or query.get("kind") != "compare"
        or not isinstance(completeness, dict)
        or completeness.get("complete") is not True
        or not isinstance(raw, list)
    ):
        raise ProviderArtifactError("artifact-incomplete", "compare artifact is incomplete")
    before_query = query.get("before")
    after_query = query.get("after")
    if not isinstance(before_query, dict) or not isinstance(after_query, dict):
        raise ProviderArtifactError("artifact-incomplete", "compare query is incomplete")
    if (
        before_query.get("provider_id") != provider_id
        or after_query.get("provider_id") != provider_id
    ):
        raise ProviderArtifactError(
            "artifact-mixed-provider", "compare query mixes provider identities"
        )
    before_scope = _scope(before_query.get("scope"), "before")
    after_scope = _scope(after_query.get("scope"), "after")
    if before_scope != after_scope:
        raise ProviderArtifactError(
            "artifact-mixed-query", "compare query mixes incompatible scopes"
        )
    before_generation = _hash(before_query.get("generation"), "before generation")
    after_generation = _hash(after_query.get("generation"), "after generation")
    before = completeness.get("before")
    after = completeness.get("after")
    summary = completeness.get("summary")
    if not isinstance(before, dict) or not isinstance(after, dict) or not isinstance(summary, dict):
        raise ProviderArtifactError("artifact-incomplete", "compare coverage is missing")
    before_documents, before_sections = _coverage(before.get("coverage"), "before")
    after_documents, after_sections = _coverage(after.get("coverage"), "after")
    _boundaries(before.get("boundaries"), "before")
    _boundaries(after.get("boundaries"), "after")
    before_visibility, before_capabilities = _operand_provider(before, "before")
    after_visibility, after_capabilities = _operand_provider(after, "after")
    if after_visibility != provider_visibility:
        raise ProviderArtifactError(
            "artifact-mixed-provider", "current provider visibility differs"
        )
    if provider_capabilities != before_capabilities & after_capabilities:
        raise ProviderArtifactError(
            "artifact-mixed-provider", "provider capability evidence differs"
        )
    if completeness.get("changes") != len(raw):
        raise ProviderArtifactError("artifact-incomplete", "compare change count differs")
    counts = Counter()
    keys: list[tuple[str, str, str]] = []
    old_observations: list[tuple[tuple[str, str, str], dict[str, object]]] = []
    new_observations: list[tuple[tuple[str, str, str], dict[str, object]]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise _artifact_error("change is not an object")
        old_raw = item.get("before")
        new_raw = item.get("after")
        old = None if old_raw is None else _observation(old_raw)
        new = None if new_raw is None else _observation(new_raw)
        if old is not None and old[1]["visibility"] != before_visibility:
            raise ProviderArtifactError(
                "artifact-mixed-provider", "before observation visibility differs"
            )
        if new is not None and new[1]["visibility"] != after_visibility:
            raise ProviderArtifactError(
                "artifact-mixed-provider", "after observation visibility differs"
            )
        if old is None and new is None:
            raise _artifact_error("change has no observation")
        if old is not None:
            old_observations.append(old)
        if new is not None:
            new_observations.append(new)
        key = old[0] if old is not None else new[0]
        if old is not None and new is not None and old[0] != new[0]:
            raise ProviderArtifactError("artifact-mixed-provider", "change mixes entity identities")
        entity = item.get("entity")
        if not isinstance(entity, dict) or (
            entity.get("kind"),
            entity.get("document_id"),
            entity.get("anchor") or "",
        ) != key:
            raise _artifact_error("change entity identity differs from observations")
        expected = _expected_classification(
            None if old is None else old[1], None if new is None else new[1]
        )
        if item.get("classification") != expected:
            raise _artifact_error("change classification is invalid")
        keys.append(key)
        counts[expected] += 1
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise ProviderArtifactError(
            "artifact-incomplete", "compare changes are unordered or duplicated"
        )
    _validate_observation_relationships(old_observations, "before comparison")
    _validate_observation_relationships(new_observations, "after comparison")
    expected_counts = {
        name: _nonnegative_int(summary.get(name), f"summary {name}")
        for name in ("unchanged", "relocated", "changed", "missing", "added", "ambiguous")
    }
    if expected_counts["ambiguous"] != 0 or any(
        expected_counts[name] != counts[name]
        for name in ("relocated", "changed", "missing", "added")
    ):
        raise ProviderArtifactError("artifact-incomplete", "compare summary differs")
    unchanged = expected_counts["unchanged"]
    shared = unchanged + counts["relocated"] + counts["changed"]
    if (
        before_documents + before_sections != shared + counts["missing"]
        or after_documents + after_sections != shared + counts["added"]
    ):
        raise ProviderArtifactError("artifact-incomplete", "compare coverage differs")
    return {
        "artifact_kind": COMPARE_ARTIFACT_KIND,
        "provider_id": provider_id,
        "generations": [before_generation, after_generation],
        "observations": 0,
        "changes": len(raw),
    }


def verify_artifact(value: object) -> dict[str, object]:
    """Verify integrity, compatibility and completeness without project state."""

    if not isinstance(value, dict):
        raise _artifact_error("artifact root is not an object")
    if value.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ProviderArtifactError(
            "artifact-schema-unsupported", "artifact schema version is unsupported"
        )
    digest = value.get("content_digest")
    if (
        not isinstance(digest, dict)
        or digest.get("algorithm") != "sha256"
        or not isinstance(digest.get("value"), str)
    ):
        raise _artifact_error("artifact digest is missing")
    unsigned = {key: item for key, item in value.items() if key != "content_digest"}
    if _digest(unsigned) != digest["value"]:
        raise _artifact_error("artifact digest does not match its content")
    _validate_protocol(value.get("protocol"))
    provider_id, provider_visibility, provider_capabilities = _validate_provider(
        value.get("provider")
    )
    _validate_policy(value.get("policy"))
    kind = value.get("artifact_kind")
    if kind == SNAPSHOT_ARTIFACT_KIND:
        result = _verify_snapshot(value, provider_id, provider_visibility)
    elif kind == COMPARE_ARTIFACT_KIND:
        result = _verify_compare(
            value, provider_id, provider_visibility, provider_capabilities
        )
    else:
        raise ProviderArtifactError("artifact-incompatible", "artifact kind is unsupported")
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": "provider-artifact-verification",
        "valid": True,
        "content_digest": digest,
        **result,
    }


def load_and_verify_artifact(path: Path) -> dict[str, object]:
    """Read and verify one artifact without consulting a project or provider."""

    try:
        value = loads_unique_json(path.read_text(encoding="utf-8"))
    except DuplicateJsonMemberError as error:
        raise _artifact_error(
            f"artifact contains duplicate JSON member: {error.member}"
        ) from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _artifact_error(f"artifact is unreadable: {path}") from error
    return verify_artifact(value)
