"""Read-only source-contract to delivery-evidence traceability."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from docsystem.catalog import MarkdownCatalog, MarkdownDocument
from docsystem.config import DocumentProfile, ProjectConfig
from docsystem.graph import Address, parse_address


@dataclass(frozen=True)
class DeliveryViolation:
    document_id: str
    path: str
    code: str
    subject: str
    detail: str


@dataclass(frozen=True)
class DeliveryMapping:
    source: str
    owner_id: str
    owner_path: str
    owner_status: str | None
    evidence: str
    disposition: str


@dataclass(frozen=True)
class DeliveryReport:
    configured: bool
    valid: bool
    metadata_field: str | None
    mappings: tuple[DeliveryMapping, ...]
    untracked_documents: tuple[str, ...]
    overlaps: tuple[str, ...]
    violations: tuple[DeliveryViolation, ...]


def _violation(
    document: MarkdownDocument, code: str, subject: str, detail: str
) -> DeliveryViolation:
    assert document.metadata is not None
    return DeliveryViolation(
        document.metadata.document_id,
        document.path.as_posix(),
        code,
        subject,
        detail,
    )


def _evidence_address(
    document: MarkdownDocument,
    profile: DocumentProfile,
    role_name: str,
) -> tuple[str | None, tuple[DeliveryViolation, ...]]:
    role = next(role for role in profile.roles if role.name == role_name)
    available = {section.anchor for section in document.sections}
    matches = tuple(anchor for anchor in role.anchors if anchor in available)
    if not matches:
        return None, (
            _violation(
                document,
                "missing-evidence-role",
                role_name,
                "none of the configured evidence anchors are present: "
                + ", ".join(role.anchors),
            ),
        )
    if len(matches) > 1:
        return None, (
            _violation(
                document,
                "ambiguous-evidence-role",
                role_name,
                "multiple configured evidence anchors are present: "
                + ", ".join(matches),
            ),
        )
    assert document.metadata is not None
    return f"{document.metadata.document_id}#{matches[0]}", ()


def _source_address_issue(
    document: MarkdownDocument,
    address: Address,
    documents: dict[str, MarkdownDocument],
) -> DeliveryViolation | None:
    assert document.metadata is not None
    if address.anchor is None:
        return _violation(
            document,
            "document-only-source",
            address.text,
            "delivery source must use an exact ID#anchor address",
        )
    target = documents.get(address.document_id)
    if target is None:
        return _violation(
            document,
            "unknown-source-document",
            address.text,
            f"source document is not cataloged: {address.document_id}",
        )
    if address.document_id == document.metadata.document_id:
        return _violation(
            document,
            "self-delivery",
            address.text,
            "delivery owner cannot claim one of its own sections",
        )
    if address.anchor not in {section.anchor for section in target.sections}:
        return _violation(
            document,
            "unknown-source-anchor",
            address.text,
            "source document has no matching canonical anchor",
        )
    return None


def evaluate_delivery(catalog: MarkdownCatalog, config: ProjectConfig) -> DeliveryReport:
    """Build a deterministic body-free reverse delivery mapping."""

    policy = config.delivery_policy
    if policy is None:
        return DeliveryReport(False, True, None, (), (), (), ())
    documents = {
        document.metadata.document_id: document
        for document in catalog.documents
        if document.metadata is not None
    }
    profiles_by_type = {
        document_type: profile
        for profile in config.document_profiles
        for document_type in profile.document_types
    }
    mappings: list[DeliveryMapping] = []
    violations: list[DeliveryViolation] = []
    untracked: list[str] = []
    for document in sorted(documents.values(), key=lambda item: item.metadata.document_id):
        metadata = document.metadata
        assert metadata is not None
        if metadata.document_type not in policy.document_types:
            continue
        additional = dict(metadata.additional_fields)
        additional_types = dict(metadata.additional_field_types)
        if policy.metadata_field not in additional:
            untracked.append(metadata.document_id)
            continue
        if additional_types[policy.metadata_field] != "sequence":
            violations.append(
                _violation(
                    document,
                    "invalid-delivery-field",
                    policy.metadata_field,
                    "traceability metadata must be a list of ID#anchor strings",
                )
            )
            continue
        values = additional[policy.metadata_field]
        assert isinstance(values, tuple)
        if not values:
            violations.append(
                _violation(
                    document,
                    "empty-delivery-field",
                    policy.metadata_field,
                    "traceability metadata must not be empty",
                )
            )
            continue
        profile = profiles_by_type[metadata.document_type]
        evidence, evidence_issues = _evidence_address(
            document, profile, policy.evidence_role
        )
        violations.extend(evidence_issues)
        seen: set[str] = set()
        parsed: list[Address] = []
        for raw in values:
            if not isinstance(raw, str):
                violations.append(
                    _violation(
                        document,
                        "invalid-source-address",
                        repr(raw),
                        "delivery source must be an ID#anchor string",
                    )
                )
                continue
            if raw in seen:
                violations.append(
                    _violation(
                        document,
                        "duplicate-source-address",
                        raw,
                        "delivery source is duplicated in one owner",
                    )
                )
                continue
            seen.add(raw)
            try:
                address = parse_address(raw)
            except ValueError as error:
                violations.append(
                    _violation(document, "invalid-source-address", raw, str(error))
                )
                continue
            issue = _source_address_issue(document, address, documents)
            if issue is not None:
                violations.append(issue)
                continue
            parsed.append(address)
        if evidence is None:
            continue
        disposition = (
            "delivered"
            if metadata.status in policy.terminal_statuses
            else "active"
        )
        for address in parsed:
            mappings.append(
                DeliveryMapping(
                    source=address.text,
                    owner_id=metadata.document_id,
                    owner_path=document.path.as_posix(),
                    owner_status=metadata.status,
                    evidence=evidence,
                    disposition=disposition,
                )
            )
    ordered_mappings = tuple(
        sorted(mappings, key=lambda item: (item.source, item.owner_id, item.evidence))
    )
    counts = Counter(item.source for item in ordered_mappings)
    overlaps = tuple(sorted(source for source, count in counts.items() if count > 1))
    ordered_violations = tuple(
        sorted(
            violations,
            key=lambda item: (item.document_id, item.code, item.subject),
        )
    )
    return DeliveryReport(
        configured=True,
        valid=not ordered_violations,
        metadata_field=policy.metadata_field,
        mappings=ordered_mappings,
        untracked_documents=tuple(sorted(untracked)),
        overlaps=overlaps,
        violations=ordered_violations,
    )
