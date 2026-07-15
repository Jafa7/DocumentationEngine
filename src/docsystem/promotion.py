"""Read-only knowledge-promotion planning over authored document authority."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from docsystem.catalog import MarkdownCatalog, MarkdownDocument
from docsystem.config import ProjectConfig
from docsystem.graph import Address, parse_address

SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
KNOWLEDGE_STATES = frozenset({"fact", "inference", "hypothesis", "decision", "reviewer-position"})
DISPOSITIONS = frozenset({"accepted", "partial", "rejected", "deferred", "contested"})
_AUTHORITY_KEY = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "source",
        "destination",
        "authority_key",
        "knowledge_state",
        "disposition",
        "evidence",
    }
)


class PromotionError(ValueError):
    """A promotion request or authored authority contract is invalid."""


@dataclass(frozen=True)
class PromotionRequest:
    source: str
    destination: str
    authority_key: str
    knowledge_state: str
    disposition: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class PromotionPlan:
    state: str
    action: str
    reason: str
    source: dict[str, object]
    destination: dict[str, object]
    authority_key: str
    evidence: tuple[str, ...]
    provenance_pins: tuple[str, ...]
    impacted_documents: tuple[str, ...]
    impact_scope: str
    conflicts: tuple[str, ...]
    omissions: tuple[str, ...]


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise PromotionError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _exact_address(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise PromotionError(f"{field} must be an ID#anchor string")
    try:
        address = parse_address(value)
    except ValueError as error:
        raise PromotionError(f"{field} is invalid: {error}") from error
    if address.anchor is None:
        raise PromotionError(f"{field} must use an exact ID#anchor address")
    return address.text


def load_promotion_request(path: Path) -> PromotionRequest:
    """Load a bounded strict JSON promotion request."""

    try:
        raw_bytes = path.read_bytes()
    except OSError as error:
        raise PromotionError("promotion request is unreadable") from error
    if len(raw_bytes) > MAX_REQUEST_BYTES:
        raise PromotionError("promotion request exceeds 65536 bytes")
    try:
        raw = json.loads(raw_bytes, object_pairs_hook=_reject_duplicate_keys)
    except UnicodeDecodeError as error:
        raise PromotionError("promotion request must be UTF-8") from error
    except json.JSONDecodeError as error:
        raise PromotionError(f"invalid promotion JSON: {error.msg}") from error
    if not isinstance(raw, dict):
        raise PromotionError("promotion request must be a JSON object")
    unknown = sorted(set(raw) - _ROOT_KEYS)
    if unknown:
        raise PromotionError(f"promotion request has unknown key(s): {', '.join(unknown)}")
    if raw.get("schema_version") != SCHEMA_VERSION or isinstance(raw.get("schema_version"), bool):
        raise PromotionError("promotion schema_version must be exactly 1")
    source = _exact_address(raw.get("source"), "source")
    destination = _exact_address(raw.get("destination"), "destination")
    if source == destination:
        raise PromotionError("source and destination must be different sections")
    authority_key = raw.get("authority_key")
    if not isinstance(authority_key, str) or not _AUTHORITY_KEY.fullmatch(authority_key):
        raise PromotionError("authority_key must be a lowercase authority slug")
    knowledge_state = raw.get("knowledge_state")
    if knowledge_state not in KNOWLEDGE_STATES:
        raise PromotionError("knowledge_state is not supported")
    disposition = raw.get("disposition")
    if disposition not in DISPOSITIONS:
        raise PromotionError("disposition is not supported")
    evidence_raw = raw.get("evidence")
    if not isinstance(evidence_raw, list):
        raise PromotionError("evidence must be an array of ID#anchor strings")
    evidence = tuple(_exact_address(item, "evidence item") for item in evidence_raw)
    if len(set(evidence)) != len(evidence):
        raise PromotionError("evidence must not contain duplicate addresses")
    return PromotionRequest(
        source,
        destination,
        authority_key,
        str(knowledge_state),
        str(disposition),
        tuple(sorted(evidence)),
    )


def _documents(catalog: MarkdownCatalog) -> dict[str, MarkdownDocument]:
    return {
        document.metadata.document_id: document
        for document in catalog.documents
        if document.metadata is not None
    }


def _resolve(
    address_text: str, documents: dict[str, MarkdownDocument], field: str
) -> tuple[Address, MarkdownDocument]:
    address = parse_address(address_text)
    document = documents.get(address.document_id)
    if document is None:
        raise PromotionError(f"{field} document is not cataloged: {address.document_id}")
    assert address.anchor is not None
    if address.anchor not in {section.anchor for section in document.sections}:
        raise PromotionError(f"{field} anchor is unknown: {address.text}")
    return address, document


def _authority_values(document: MarkdownDocument) -> tuple[str, ...]:
    assert document.metadata is not None
    fields = dict(document.metadata.additional_fields)
    types = dict(document.metadata.additional_field_types)
    if "authority_for" not in fields:
        return ()
    if types.get("authority_for") != "sequence":
        raise PromotionError(
            f"{document.metadata.document_id}: authority_for must be a list of slugs"
        )
    values = fields["authority_for"]
    assert isinstance(values, tuple)
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not _AUTHORITY_KEY.fullmatch(value):
            raise PromotionError(
                f"{document.metadata.document_id}: authority_for has an invalid slug"
            )
        if value in result:
            raise PromotionError(
                f"{document.metadata.document_id}: authority_for contains a duplicate slug"
            )
        result.append(value)
    return tuple(result)


def build_promotion_plan(
    catalog: MarkdownCatalog, config: ProjectConfig, request: PromotionRequest
) -> PromotionPlan:
    """Build a deterministic plan without returning or changing section bodies."""

    documents = _documents(catalog)
    source_address, source_document = _resolve(request.source, documents, "source")
    destination_address, destination_document = _resolve(
        request.destination, documents, "destination"
    )
    for evidence in request.evidence:
        _resolve(evidence, documents, "evidence")

    owners = tuple(
        sorted(
            document.metadata.document_id
            for document in documents.values()
            if request.authority_key in _authority_values(document)
        )
    )
    destination_id = destination_address.document_id
    if destination_id not in owners:
        raise PromotionError(f"destination does not declare authority_for: {request.authority_key}")
    conflicts = tuple(owner for owner in owners if owner != destination_id)

    destination_metadata = destination_document.metadata
    source_metadata = source_document.metadata
    assert destination_metadata is not None and source_metadata is not None
    profile_by_type = {
        document_type: profile
        for profile in config.document_profiles
        for document_type in profile.document_types
    }
    profile = profile_by_type.get(destination_metadata.document_type or "")
    if profile is None:
        raise PromotionError(
            "destination type has no authored profile/history authority: "
            f"{destination_metadata.document_type or '<unset>'}"
        )

    promotable = request.disposition in {"accepted", "partial"} and (
        request.knowledge_state in {"fact", "inference", "decision"}
    )
    if promotable and not request.evidence:
        raise PromotionError("accepted/partial promotion requires exact evidence")
    if conflicts:
        state = "blocked"
        action = "resolve-authority-conflict"
        reason = "multiple documents declare the authority key"
    elif not promotable:
        state = "blocked"
        action = "retain-candidate"
        reason = "knowledge state or review disposition is not promotable"
    else:
        state = "ready"
        action = {
            "living": "revise-owner",
            "append-only": "append-record",
            "immutable-after-state": "create-superseding-document",
        }[profile.history_mode]
        reason = f"authority is unique and history mode is {profile.history_mode}"

    impacted = tuple(
        sorted(
            document.metadata.document_id
            for document in documents.values()
            if document.metadata is not None
            and any(
                reference.target_id == destination_id for reference in document.metadata.references
            )
            and document.metadata.document_id != destination_id
        )
    )
    pins = tuple(
        sorted(
            {f"{source_address.document_id}@{source_metadata.revision}"}.union(
                {
                    f"{address.document_id}@{documents[address.document_id].metadata.revision}"
                    for address in (parse_address(item) for item in request.evidence)
                }
            )
        )
    )
    return PromotionPlan(
        state=state,
        action=action,
        reason=reason,
        source={
            "address": source_address.text,
            "type": source_metadata.document_type,
            "status": source_metadata.status,
            "revision": source_metadata.revision,
            "knowledge_state": request.knowledge_state,
            "disposition": request.disposition,
        },
        destination={
            "address": destination_address.text,
            "type": destination_metadata.document_type,
            "status": destination_metadata.status,
            "revision": destination_metadata.revision,
            "profile": profile.name,
            "history_mode": profile.history_mode,
        },
        authority_key=request.authority_key,
        evidence=request.evidence,
        provenance_pins=pins,
        impacted_documents=impacted,
        impact_scope="destination-document-metadata-consumers",
        conflicts=conflicts,
        omissions=(
            "document-body-not-included",
            "semantic-merge-not-generated",
            "write-not-performed",
            "actor-not-authenticated",
        ),
    )
