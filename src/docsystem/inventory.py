"""Privacy-aware, deterministic metadata and document-graph inventory."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from docsystem.catalog import MarkdownCatalog
from docsystem.config import ProjectConfig
from docsystem.health import facts_from_catalog
from docsystem.metadata import PINNED_RELATION, RELATION_FIELDS

CORE_FIELDS = ("id", "revision", "type", "status")
RELATION_FIELDS_ALL = (*RELATION_FIELDS, PINNED_RELATION)


@dataclass(frozen=True)
class FieldInventory:
    """Observed coverage and types for one metadata field."""

    name: str
    category: str
    present_documents: int
    missing_documents: int
    observed_types: tuple[str, ...]
    document_types: tuple[str, ...]
    type_conflict: bool


@dataclass(frozen=True)
class DocumentInventory:
    """Body-free identity, lifecycle and graph facts for one document."""

    document_id: str
    revision: int
    role: str
    path: str
    document_type: str | None
    status: str | None
    additional_fields: tuple[str, ...]
    incoming_edges: int
    outgoing_edges: int
    boundaries: int


@dataclass(frozen=True)
class FieldOccurrence:
    """One explicitly requested field value."""

    document_id: str
    path: str
    value_type: str
    value: object


@dataclass(frozen=True)
class MetadataInventory:
    """Complete derived inventory for one valid catalog snapshot."""

    document_count: int
    fields: tuple[FieldInventory, ...]
    documents: tuple[DocumentInventory, ...]


def _json_safe(value: object, value_type: str | None = None) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"encoding": "hex", "value": value.hex()}
    if isinstance(value, tuple):
        is_mapping = value_type == "mapping" or (
            value_type is None
            and bool(value)
            and all(
                isinstance(item, tuple)
                and len(item) == 2
                and isinstance(item[0], str)
                for item in value
            )
        )
        if is_mapping:
            return {str(key): _json_safe(item) for key, item in value}
        return [_json_safe(item) for item in value]
    return value


def build_metadata_inventory(
    catalog: MarkdownCatalog, config: ProjectConfig
) -> MetadataInventory:
    """Build body-free metadata coverage and document-level graph facts."""

    documents = tuple(
        document for document in catalog.documents if document.metadata is not None
    )
    facts = facts_from_catalog(catalog, config)
    incoming: Counter[str] = Counter()
    outgoing: Counter[str] = Counter()
    for edge in facts.edges:
        if edge.authority == "generated" or edge.source_id == edge.target_id:
            continue
        outgoing[edge.source_id] += 1
        incoming[edge.target_id] += 1
    boundaries = Counter(item.source_id for item in facts.boundaries)

    field_types: dict[str, set[str]] = defaultdict(set)
    field_doc_types: dict[str, set[str]] = defaultdict(set)
    present: Counter[str] = Counter()
    for document in documents:
        metadata = document.metadata
        assert metadata is not None
        values: dict[str, tuple[bool, str]] = {
            "id": (True, "string"),
            "revision": (True, "integer"),
            "type": (metadata.document_type is not None, "string"),
            "status": (metadata.status is not None, "string"),
        }
        relation_names = Counter(reference.relation for reference in metadata.references)
        relation_names.update(relation for relation, _ in metadata.legacy_references)
        values.update(
            (relation, (relation_names[relation] > 0, "sequence"))
            for relation in RELATION_FIELDS_ALL
        )
        values.update(
            (name, (True, value_type))
            for name, value_type in metadata.additional_field_types
        )
        document_type = metadata.document_type or "<unset>"
        for name, (is_present, value_type) in values.items():
            if not is_present:
                continue
            present[name] += 1
            field_types[name].add(value_type)
            field_doc_types[name].add(document_type)

    names = sorted({*CORE_FIELDS, *RELATION_FIELDS_ALL, *field_types})
    fields = tuple(
        FieldInventory(
            name=name,
            category=(
                "core"
                if name in CORE_FIELDS
                else "relation"
                if name in RELATION_FIELDS_ALL
                else "additional"
            ),
            present_documents=present[name],
            missing_documents=len(documents) - present[name],
            observed_types=tuple(sorted(field_types[name])),
            document_types=tuple(sorted(field_doc_types[name])),
            type_conflict=len(field_types[name]) > 1,
        )
        for name in names
    )
    inventory_documents = tuple(
        DocumentInventory(
            document_id=document.metadata.document_id,
            revision=document.metadata.revision,
            role=document.role,
            path=document.path.as_posix(),
            document_type=document.metadata.document_type,
            status=document.metadata.status,
            additional_fields=tuple(
                name for name, _ in document.metadata.additional_fields
            ),
            incoming_edges=incoming[document.metadata.document_id],
            outgoing_edges=outgoing[document.metadata.document_id],
            boundaries=boundaries[document.metadata.document_id],
        )
        for document in sorted(documents, key=lambda item: item.metadata.document_id)
    )
    return MetadataInventory(len(documents), fields, inventory_documents)


def field_occurrences(
    catalog: MarkdownCatalog, field_name: str
) -> tuple[FieldOccurrence, ...]:
    """Return values only for one explicitly selected known field."""

    occurrences: list[FieldOccurrence] = []
    for document in catalog.documents:
        metadata = document.metadata
        if metadata is None:
            continue
        value_type: str | None = None
        value: object = None
        if field_name == "id":
            value_type, value = "string", metadata.document_id
        elif field_name == "revision":
            value_type, value = "integer", metadata.revision
        elif field_name == "type" and metadata.document_type is not None:
            value_type, value = "string", metadata.document_type
        elif field_name == "status" and metadata.status is not None:
            value_type, value = "string", metadata.status
        elif field_name in RELATION_FIELDS_ALL:
            values = [
                (
                    f"{reference.target_id}@{reference.expected_revision}"
                    if reference.expected_revision is not None
                    else reference.target_id
                )
                for reference in metadata.references
                if reference.relation == field_name
            ]
            values.extend(
                raw
                for relation, raw in metadata.legacy_references
                if relation == field_name
            )
            if values:
                value_type, value = "sequence", sorted(values)
        else:
            additional = dict(metadata.additional_fields)
            additional_types = dict(metadata.additional_field_types)
            if field_name in additional:
                value_type = additional_types[field_name]
                value = _json_safe(additional[field_name], value_type)
        if value_type is not None:
            occurrences.append(
                FieldOccurrence(
                    metadata.document_id,
                    document.path.as_posix(),
                    value_type,
                    value,
                )
            )
    return tuple(sorted(occurrences, key=lambda item: item.document_id))
