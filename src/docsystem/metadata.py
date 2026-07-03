"""YAML front matter models and deterministic parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

RELATION_FIELDS = ("derived_from", "depends_on", "related", "supersedes")
PINNED_RELATION = "validated_against"
DOCUMENT_ID_PATTERN = re.compile(r"^([A-Z][A-Z0-9]{1,15})-([0-9]+)$")


@dataclass(frozen=True)
class MetadataReference:
    """A normalized semantic reference from one document to another."""

    relation: str
    target_id: str
    expected_revision: int | None = None


@dataclass(frozen=True)
class DocumentMetadata:
    """The provider-neutral metadata required by the context engine."""

    document_id: str
    revision: int
    document_type: str | None
    status: str | None
    references: tuple[MetadataReference, ...]
    additional_fields: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class FrontMatterResult:
    """Parsed metadata plus recoverable validation messages."""

    metadata: DocumentMetadata | None
    end_line: int
    issues: tuple[str, ...]


def _freeze(value: Any) -> object:
    if isinstance(value, dict):
        return tuple(
            (str(key), _freeze(item))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    return value


def _valid_id(value: object, prefixes: frozenset[str]) -> bool:
    if not isinstance(value, str):
        return False
    match = DOCUMENT_ID_PATTERN.fullmatch(value)
    return match is not None and match.group(1) in prefixes


def _optional_string(raw: dict[str, Any], field: str, issues: list[str]) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        issues.append(f"metadata.{field} must be a non-empty string when present")
        return None
    return value


def _references(
    raw: dict[str, Any], prefixes: frozenset[str], issues: list[str]
) -> tuple[MetadataReference, ...]:
    references: list[MetadataReference] = []
    for relation in RELATION_FIELDS:
        values = raw.get(relation, [])
        if not isinstance(values, list):
            issues.append(f"metadata.{relation} must be a list")
            continue
        seen: set[str] = set()
        for value in values:
            if not _valid_id(value, prefixes):
                issues.append(
                    f"metadata.{relation} entry {value!r} must use a configured "
                    "stable ID"
                )
                continue
            assert isinstance(value, str)
            if value in seen:
                issues.append(f"metadata.{relation} contains duplicate reference {value}")
                continue
            seen.add(value)
            references.append(MetadataReference(relation, value))

    pins = raw.get(PINNED_RELATION, [])
    if not isinstance(pins, list):
        issues.append(f"metadata.{PINNED_RELATION} must be a list")
    else:
        seen_pins: set[tuple[str, int]] = set()
        for value in pins:
            if not isinstance(value, str) or "@" not in value:
                issues.append(
                    f"metadata.{PINNED_RELATION} entries must use ID@revision"
                )
                continue
            target_id, revision_raw = value.rsplit("@", 1)
            if not _valid_id(target_id, prefixes) or not revision_raw.isdigit():
                issues.append(
                    f"metadata.{PINNED_RELATION} entries must use ID@revision"
                )
                continue
            revision = int(revision_raw)
            if revision < 1:
                issues.append(
                    f"metadata.{PINNED_RELATION} revisions must be positive"
                )
                continue
            key = (target_id, revision)
            if key in seen_pins:
                issues.append(
                    f"metadata.{PINNED_RELATION} contains duplicate reference {value}"
                )
                continue
            seen_pins.add(key)
            references.append(
                MetadataReference(PINNED_RELATION, target_id, revision)
            )
    return tuple(references)


def parse_front_matter(text: str, prefixes: frozenset[str]) -> FrontMatterResult:
    """Parse leading YAML front matter without rejecting additional fields."""

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return FrontMatterResult(None, 0, ("YAML front matter is required",))

    closing_line = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_line is None:
        return FrontMatterResult(None, 0, ("YAML front matter is not closed",))

    try:
        loaded = yaml.safe_load("\n".join(lines[1:closing_line]))
    except yaml.YAMLError as error:
        summary = str(error).splitlines()[0]
        return FrontMatterResult(
            None,
            closing_line + 1,
            (f"invalid YAML front matter: {summary}",),
        )
    if not isinstance(loaded, dict):
        return FrontMatterResult(
            None,
            closing_line + 1,
            ("YAML front matter must be a mapping",),
        )

    raw = {str(key): value for key, value in loaded.items()}
    issues: list[str] = []
    document_id = raw.get("id")
    if not _valid_id(document_id, prefixes):
        issues.append("metadata.id must use a configured stable ID prefix")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        issues.append("metadata.revision must be a positive integer")

    document_type = _optional_string(raw, "type", issues)
    status = _optional_string(raw, "status", issues)
    references = _references(raw, prefixes, issues)
    metadata: DocumentMetadata | None = None
    if _valid_id(document_id, prefixes) and isinstance(revision, int) and not isinstance(
        revision, bool
    ) and revision >= 1:
        known = {
            "id",
            "revision",
            "type",
            "status",
            *RELATION_FIELDS,
            PINNED_RELATION,
        }
        additional = tuple(
            (key, _freeze(value))
            for key, value in sorted(raw.items())
            if key not in known
        )
        assert isinstance(document_id, str)
        metadata = DocumentMetadata(
            document_id=document_id,
            revision=revision,
            document_type=document_type,
            status=status,
            references=references,
            additional_fields=additional,
        )
    return FrontMatterResult(metadata, closing_line + 1, tuple(issues))
