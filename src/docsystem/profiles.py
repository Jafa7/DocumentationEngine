"""Read-only validation against project-authored document profiles."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from docsystem.catalog import MarkdownCatalog, MarkdownDocument
from docsystem.config import DocumentProfile, ProjectConfig


@dataclass(frozen=True)
class ProfileViolation:
    """One deterministic mismatch between authored policy and one document."""

    document_id: str
    path: str
    profile: str
    code: str
    subject: str
    detail: str


@dataclass(frozen=True)
class ProfileDocument:
    """Body-free profile assignment and validation state for one document."""

    document_id: str
    path: str
    document_type: str | None
    status: str | None
    profile: str | None
    history_mode: str | None
    valid: bool | None


@dataclass(frozen=True)
class ProfileSummary:
    """Observed use of one authored profile."""

    name: str
    document_types: tuple[str, ...]
    history_mode: str
    document_count: int
    violation_count: int


@dataclass(frozen=True)
class ProfileReport:
    """Complete deterministic profile evaluation for a valid catalog."""

    valid: bool
    profiles: tuple[ProfileSummary, ...]
    documents: tuple[ProfileDocument, ...]
    unprofiled_documents: tuple[str, ...]
    violations: tuple[ProfileViolation, ...]


def _metadata_fields(document: MarkdownDocument) -> frozenset[str]:
    metadata = document.metadata
    assert metadata is not None
    fields = {"id", "revision"}
    if metadata.document_type is not None:
        fields.add("type")
    if metadata.status is not None:
        fields.add("status")
    fields.update(reference.relation for reference in metadata.references)
    fields.update(relation for relation, _ in metadata.legacy_references)
    fields.update(name for name, _ in metadata.additional_fields)
    return frozenset(fields)


def _document_relations(document: MarkdownDocument) -> frozenset[str]:
    metadata = document.metadata
    assert metadata is not None
    return frozenset(
        [reference.relation for reference in metadata.references]
        + [relation for relation, _ in metadata.legacy_references]
    )


def _violations(
    document: MarkdownDocument, profile: DocumentProfile
) -> tuple[ProfileViolation, ...]:
    metadata = document.metadata
    assert metadata is not None
    document_id = metadata.document_id
    path = document.path.as_posix()
    result: list[ProfileViolation] = []
    fields = _metadata_fields(document)
    for field in profile.required_metadata:
        if field not in fields:
            result.append(
                ProfileViolation(
                    document_id,
                    path,
                    profile.name,
                    "missing-metadata",
                    field,
                    f"required metadata field {field!r} is absent",
                )
            )
    anchors = {section.anchor for section in document.sections}
    roles = {role.name: role.anchors for role in profile.roles}
    for role_name in profile.required_roles:
        aliases = roles[role_name]
        if anchors.isdisjoint(aliases):
            result.append(
                ProfileViolation(
                    document_id,
                    path,
                    profile.name,
                    "missing-role",
                    role_name,
                    "none of the configured anchors are present: "
                    + ", ".join(aliases),
                )
            )
    if profile.allowed_relations is not None:
        forbidden = _document_relations(document) - set(profile.allowed_relations)
        for relation in sorted(forbidden):
            result.append(
                ProfileViolation(
                    document_id,
                    path,
                    profile.name,
                    "relation-not-allowed",
                    relation,
                    f"relation {relation!r} is outside the profile allowlist",
                )
            )
    if (
        profile.allowed_statuses is not None
        and metadata.status not in profile.allowed_statuses
    ):
        status = metadata.status or "<unset>"
        result.append(
            ProfileViolation(
                document_id,
                path,
                profile.name,
                "status-not-allowed",
                status,
                f"status {status!r} is outside the profile allowlist",
            )
        )
    return tuple(result)


def evaluate_profiles(catalog: MarkdownCatalog, config: ProjectConfig) -> ProfileReport:
    """Evaluate every valid document without reading or returning body text."""

    by_type = {
        document_type: profile
        for profile in config.document_profiles
        for document_type in profile.document_types
    }
    violations: list[ProfileViolation] = []
    documents: list[ProfileDocument] = []
    unprofiled: list[str] = []
    profile_counts: Counter[str] = Counter()
    violation_counts: Counter[str] = Counter()
    for document in sorted(
        (item for item in catalog.documents if item.metadata is not None),
        key=lambda item: item.metadata.document_id,
    ):
        metadata = document.metadata
        assert metadata is not None
        profile = by_type.get(metadata.document_type or "")
        document_violations: tuple[ProfileViolation, ...] = ()
        if profile is None:
            unprofiled.append(metadata.document_id)
        else:
            document_violations = _violations(document, profile)
            violations.extend(document_violations)
            profile_counts[profile.name] += 1
            violation_counts[profile.name] += len(document_violations)
        documents.append(
            ProfileDocument(
                document_id=metadata.document_id,
                path=document.path.as_posix(),
                document_type=metadata.document_type,
                status=metadata.status,
                profile=profile.name if profile is not None else None,
                history_mode=profile.history_mode if profile is not None else None,
                valid=(not document_violations) if profile is not None else None,
            )
        )
    summaries = tuple(
        ProfileSummary(
            profile.name,
            profile.document_types,
            profile.history_mode,
            profile_counts[profile.name],
            violation_counts[profile.name],
        )
        for profile in config.document_profiles
    )
    ordered_violations = tuple(
        sorted(
            violations,
            key=lambda item: (
                item.document_id,
                item.code,
                item.subject,
                item.profile,
            ),
        )
    )
    return ProfileReport(
        valid=not ordered_violations,
        profiles=summaries,
        documents=tuple(documents),
        unprofiled_documents=tuple(sorted(unprofiled)),
        violations=ordered_violations,
    )
