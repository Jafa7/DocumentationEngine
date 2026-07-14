"""YAML front matter models and deterministic parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

RELATION_FIELDS = ("derived_from", "depends_on", "related", "supersedes")
PINNED_RELATION = "validated_against"
DOCUMENT_ID_PATTERN = re.compile(r"^([A-Z][A-Z0-9]{1,15})-([0-9]+)$")
QUALIFIED_DOCUMENT_PATTERN = re.compile(
    r"^([a-z][a-z0-9-]{0,31})::([A-Z][A-Z0-9]{1,15}-[0-9]+)$"
)


@dataclass(frozen=True)
class MetadataReference:
    """A normalized semantic reference from one document to another."""

    relation: str
    target_id: str
    expected_revision: int | None = None


@dataclass(frozen=True)
class FederatedMetadataReference:
    """An authored semantic reference whose target belongs to another source."""

    relation: str
    target: str
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
    additional_field_types: tuple[tuple[str, str], ...] = ()
    legacy_references: tuple[tuple[str, str], ...] = ()
    federated_references: tuple[FederatedMetadataReference, ...] = ()


@dataclass(frozen=True)
class FrontMatterResult:
    """Parsed metadata plus recoverable validation messages."""

    metadata: DocumentMetadata | None
    end_line: int
    issues: tuple[str, ...]
    graph_issues: tuple[str, ...]


class DuplicateKeyError(yaml.YAMLError):
    """A duplicate YAML mapping key with a Markdown source location."""


class UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys at every mapping level."""


def _construct_unique_mapping(
    loader: UniqueKeySafeLoader, node: MappingNode, deep: bool = False
) -> dict[object, object]:
    if not isinstance(node, MappingNode):
        raise ConstructorError(
            None,
            None,
            f"expected a mapping node, but found {node.id}",
            node.start_mark,
        )
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            line = key_node.start_mark.line + 2
            column = key_node.start_mark.column + 1
            raise DuplicateKeyError(
                f"duplicate mapping key {key!r} at line {line}, column {column}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeySafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


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


def metadata_value_type(value: object) -> str:
    """Return a stable, JSON-like type name for one authored YAML value."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, dict):
        return "mapping"
    if isinstance(value, (list, tuple, set)):
        return "sequence"
    return "other"


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
    raw: dict[str, Any],
    prefixes: frozenset[str],
    issues: list[str],
    graph_issues: list[str],
) -> tuple[
    tuple[MetadataReference, ...],
    tuple[tuple[str, str], ...],
    tuple[FederatedMetadataReference, ...],
]:
    def report(message: str) -> None:
        issues.append(message)
        graph_issues.append(message)

    references: list[MetadataReference] = []
    legacy_references: list[tuple[str, str]] = []
    federated_references: list[FederatedMetadataReference] = []
    for relation in RELATION_FIELDS:
        values = raw.get(relation, [])
        if not isinstance(values, list):
            report(f"metadata.{relation} must be a list")
            continue
        seen: set[str] = set()
        for value in values:
            if isinstance(value, str):
                if value in seen:
                    report(
                        f"metadata.{relation} contains duplicate reference {value}"
                    )
                    continue
                seen.add(value)
            if isinstance(value, str) and QUALIFIED_DOCUMENT_PATTERN.fullmatch(value):
                federated_references.append(
                    FederatedMetadataReference(relation, value)
                )
                continue
            if isinstance(value, str) and "::" in value:
                report(
                    f"metadata.{relation} entry {value!r} must use "
                    "source::stable-ID syntax"
                )
                continue
            if not _valid_id(value, prefixes):
                id_shaped = (
                    isinstance(value, str)
                    and DOCUMENT_ID_PATTERN.fullmatch(value) is not None
                )
                if isinstance(value, str) and value and not id_shaped:
                    # A non-ID plain string is always a legacy-reference
                    # candidate; the catalog layer resolves it against
                    # `relations.legacy_paths` and classifies it as either a
                    # migratable document relation or a permanent boundary
                    # (URL/resource) that is never itself a stable ID.
                    legacy_references.append((relation, value))
                    continue
                report(
                    f"metadata.{relation} entry {value!r} must use a configured "
                    "stable ID"
                )
                continue
            assert isinstance(value, str)
            references.append(MetadataReference(relation, value))

    pins = raw.get(PINNED_RELATION, [])
    if not isinstance(pins, list):
        report(f"metadata.{PINNED_RELATION} must be a list")
    else:
        seen_pins: set[tuple[str, int]] = set()
        revisions_by_target: dict[str, set[int]] = {}
        for value in pins:
            if not isinstance(value, str) or "@" not in value:
                report(
                    f"metadata.{PINNED_RELATION} entries must use ID@revision "
                    "or source::ID@revision"
                )
                continue
            target_id, revision_raw = value.rsplit("@", 1)
            qualified = QUALIFIED_DOCUMENT_PATTERN.fullmatch(target_id)
            if (
                not (_valid_id(target_id, prefixes) or qualified is not None)
                or not revision_raw.isdigit()
            ):
                report(
                    f"metadata.{PINNED_RELATION} entries must use ID@revision "
                    "or source::ID@revision"
                )
                continue
            revision = int(revision_raw)
            if revision < 1:
                report(f"metadata.{PINNED_RELATION} revisions must be positive")
                continue
            key = (target_id, revision)
            if key in seen_pins:
                report(
                    f"metadata.{PINNED_RELATION} contains duplicate reference {value}"
                )
                continue
            seen_pins.add(key)
            revisions = revisions_by_target.setdefault(target_id, set())
            if revisions:
                previous_revision = min(revisions)
                report(
                    f"metadata.{PINNED_RELATION} has conflicting revisions for "
                    f"{target_id}: {previous_revision} and {revision}"
                )
            revisions.add(revision)
        for target_id, revisions in revisions_by_target.items():
            if len(revisions) != 1:
                continue
            revision = next(iter(revisions))
            if QUALIFIED_DOCUMENT_PATTERN.fullmatch(target_id):
                federated_references.append(
                    FederatedMetadataReference(PINNED_RELATION, target_id, revision)
                )
            else:
                references.append(
                    MetadataReference(PINNED_RELATION, target_id, revision)
                )
    return (
        tuple(references),
        tuple(legacy_references),
        tuple(federated_references),
    )


def parse_front_matter(
    text: str,
    prefixes: frozenset[str],
) -> FrontMatterResult:
    """Parse leading YAML front matter without rejecting additional fields."""

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        message = "YAML front matter is required"
        return FrontMatterResult(None, 0, (message,), (message,))

    closing_line = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_line is None:
        message = "YAML front matter is not closed"
        return FrontMatterResult(None, 0, (message,), (message,))

    try:
        loaded = yaml.load(
            "\n".join(lines[1:closing_line]), Loader=UniqueKeySafeLoader
        )
    except yaml.YAMLError as error:
        summary = str(error).splitlines()[0]
        message = f"invalid YAML front matter: {summary}"
        return FrontMatterResult(
            None,
            closing_line + 1,
            (message,),
            (message,),
        )
    if not isinstance(loaded, dict):
        message = "YAML front matter must be a mapping"
        return FrontMatterResult(
            None,
            closing_line + 1,
            (message,),
            (message,),
        )

    raw = {str(key): value for key, value in loaded.items()}
    issues: list[str] = []
    graph_issues: list[str] = []
    document_id = raw.get("id")
    if not _valid_id(document_id, prefixes):
        message = "metadata.id must use a configured stable ID prefix"
        issues.append(message)
        graph_issues.append(message)
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        message = "metadata.revision must be a positive integer"
        issues.append(message)
        graph_issues.append(message)

    document_type = _optional_string(raw, "type", issues)
    status = _optional_string(raw, "status", issues)
    references, legacy_references, federated_references = _references(
        raw, prefixes, issues, graph_issues
    )
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
        additional_types = tuple(
            (key, metadata_value_type(value))
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
            additional_field_types=additional_types,
            legacy_references=legacy_references,
            federated_references=federated_references,
        )
    return FrontMatterResult(
        metadata,
        closing_line + 1,
        tuple(issues),
        tuple(graph_issues),
    )
