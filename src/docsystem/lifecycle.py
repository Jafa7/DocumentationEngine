"""Cross-artifact validation for one provider-neutral workstream lifecycle."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from docsystem.admission import AdmissionEvaluation, AdmissionRequest
from docsystem.execution import ExecutionResultEvaluation
from docsystem.workstream import WorkstreamEvaluation, WorkstreamRecord


class LifecycleError(ValueError):
    """A deterministic cross-artifact lifecycle validation failure."""


@dataclass(frozen=True)
class LifecycleEvaluation:
    """Body-free evidence that one completed workstream has one lineage."""

    workstream_id: str
    admission_criterion: str
    workstream_criterion: str
    intake_request_sha256: str | None
    admission_request_sha256: str
    packet_sha256: str
    targets: tuple[str, ...]
    changed_paths: tuple[str, ...]
    attempts: int
    findings: int
    resolved_findings: int
    target_coverage: bool
    source_scope_complete: bool
    independent_review: bool
    ready_to_finish: bool


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise LifecycleError(f"{field} must be an object with string keys")
    return value


def _require_equal(
    actual: object, expected: object, field: str, *, source: str = "packet"
) -> None:
    if actual != expected:
        raise LifecycleError(f"{source} {field} does not match the lifecycle input")


def _packet_source_scope(packet: dict[str, object]) -> tuple[tuple[str, str | None], ...]:
    raw_scope = packet.get("source_scope")
    if not isinstance(raw_scope, list):
        raise LifecycleError("lifecycle requires a source-scoped admission")
    rows: list[tuple[str, str | None]] = []
    for index, raw in enumerate(raw_scope):
        item = _mapping(raw, f"packet.source_scope[{index}]")
        path = item.get("path")
        digest = item.get("sha256")
        if not isinstance(path, str) or not path:
            raise LifecycleError(
                f"packet.source_scope[{index}].path must be a non-empty string"
            )
        if digest is not None and not isinstance(digest, str):
            raise LifecycleError(
                f"packet.source_scope[{index}].sha256 must be null or a string"
            )
        rows.append((path, digest))
    if len({path for path, _ in rows}) != len(rows):
        raise LifecycleError("execution packet source_scope must not contain duplicates")
    return tuple(sorted(rows))


def _packet_targets(packet: dict[str, object]) -> tuple[str, ...]:
    raw_targets = packet.get("targets")
    if not isinstance(raw_targets, list):
        raise LifecycleError("packet.targets must be a list")
    targets: list[str] = []
    for index, raw in enumerate(raw_targets):
        row = _mapping(raw, f"packet.targets[{index}]")
        snapshot = _mapping(row.get("snapshot"), f"packet.targets[{index}].snapshot")
        address = snapshot.get("address")
        plans = row.get("change_plans")
        if not isinstance(address, str) or not address:
            raise LifecycleError(
                f"packet.targets[{index}].snapshot.address must be a non-empty string"
            )
        if not isinstance(plans, list) or not plans:
            raise LifecycleError(
                f"packet.targets[{index}].change_plans must be a non-empty list"
            )
        targets.append(address)
    return tuple(targets)


def _packet_mandate_sections(packet: dict[str, object]) -> tuple[str, ...]:
    mandate = _mapping(packet.get("mandate"), "packet.mandate")
    raw_sections = mandate.get("required_sections")
    if not isinstance(raw_sections, list):
        raise LifecycleError("packet.mandate.required_sections must be a list")
    addresses: list[str] = []
    for index, raw in enumerate(raw_sections):
        item = _mapping(raw, f"packet.mandate.required_sections[{index}]")
        address = item.get("address")
        if not isinstance(address, str) or not address:
            raise LifecycleError(
                "packet.mandate.required_sections"
                f"[{index}].address must be a non-empty string"
            )
        addresses.append(address)
    return tuple(addresses)


def evaluate_lifecycle(
    *,
    document_id: str,
    admission_request: AdmissionRequest,
    admission_evaluation: AdmissionEvaluation,
    packet: dict[str, object],
    execution: ExecutionResultEvaluation,
    record: WorkstreamRecord,
    workstream: WorkstreamEvaluation,
    required_mandate_sections: tuple[str, ...],
) -> LifecycleEvaluation:
    """Prove identity, integrity and coverage across lifecycle artifacts."""

    if admission_evaluation.blocked:
        raise LifecycleError(
            "execution admission is blocked: "
            + ", ".join(admission_evaluation.reasons)
        )
    if admission_request.workstream_id != document_id:
        raise LifecycleError(
            "admission request workstream_id does not match the requested ID"
        )
    if record.workstream_id != document_id:
        raise LifecycleError(
            "workstream record workstream_id does not match the requested ID"
        )
    if workstream.workstream_id != document_id:
        raise LifecycleError(
            "workstream evaluation workstream_id does not match the requested ID"
        )
    if execution.workstream_id != document_id:
        raise LifecycleError(
            "execution result workstream_id does not match the requested ID"
        )
    if packet.get("kind") != "execution-handoff":
        raise LifecycleError("execution packet kind must be 'execution-handoff'")
    _require_equal(packet.get("workstream_id"), document_id, "workstream_id")
    _require_equal(packet.get("packet_sha256"), execution.packet_sha256, "packet_sha256")

    admission = _mapping(packet.get("admission"), "packet.admission")
    comparisons = {
        "workstream_id": document_id,
        "criterion": admission_request.criterion,
        "intake_request_sha256": admission_request.intake_request_sha256,
        "request_sha256": admission_request.request_sha256,
        "outcome_sha256": hashlib.sha256(
            admission_request.outcome.encode()
        ).hexdigest(),
        "decision": "admitted",
        "blocked": False,
        "reasons": list(admission_evaluation.reasons),
        "required_autonomy": admission_evaluation.required_autonomy,
        "actions": list(admission_request.actions),
        "targets": list(admission_request.targets),
        "risk": admission_request.risk,
        "verification": admission_request.verification,
        "boundaries": {
            "authored_deletion": admission_request.boundaries.authored_deletion,
            "privacy_boundary": admission_request.boundaries.privacy_boundary,
            "permission_expansion": admission_request.boundaries.permission_expansion,
            "external_commitment": admission_request.boundaries.external_commitment,
        },
        "authorizations": [
            {
                "action": item.action,
                "authority": item.authority,
                "evidence": item.evidence,
            }
            for item in admission_request.authorizations
        ],
        "missing_authorizations": list(admission_evaluation.missing_authorizations),
        "assumptions": list(admission_request.assumptions),
    }
    for field, expected in comparisons.items():
        _require_equal(admission.get(field), expected, f"admission.{field}")

    expected_scope = tuple(
        sorted((item.path, item.sha256) for item in admission_request.source_scope)
    )
    _require_equal(
        _packet_source_scope(packet), expected_scope, "source_scope"
    )
    _require_equal(
        admission.get("source_scope"),
        [
            {"path": item.path, "sha256": item.sha256}
            for item in admission_request.source_scope
        ],
        "admission.source_scope",
    )
    _require_equal(
        _packet_targets(packet),
        tuple(admission_request.targets),
        "targets",
    )

    mandate = _mapping(packet.get("mandate"), "packet.mandate")
    _require_equal(mandate.get("id"), document_id, "mandate.id")
    _require_equal(
        _packet_mandate_sections(packet),
        tuple(f"{document_id}#{anchor}" for anchor in required_mandate_sections),
        "mandate.required_sections",
    )

    if not workstream.ready_to_finish or record.final_state != "completed":
        raise LifecycleError("workstream record is not ready to finish")
    targets = tuple(admission_request.targets)
    missing_targets = sorted(set(targets) - set(record.evidence.changes))
    if missing_targets:
        raise LifecycleError(
            "completed workstream evidence omits admitted target(s): "
            + ", ".join(missing_targets)
        )
    review = record.evidence.review
    independent_review = bool(
        review is not None and review.status == "accepted" and review.independent
    )
    if not independent_review:
        raise LifecycleError(
            "completed lifecycle requires accepted independent review evidence"
        )

    return LifecycleEvaluation(
        workstream_id=document_id,
        admission_criterion=admission_request.criterion,
        workstream_criterion=record.criterion,
        intake_request_sha256=admission_request.intake_request_sha256,
        admission_request_sha256=admission_request.request_sha256,
        packet_sha256=execution.packet_sha256,
        targets=targets,
        changed_paths=tuple(item.path for item in execution.changed_files),
        attempts=workstream.attempts,
        findings=workstream.findings,
        resolved_findings=workstream.resolved_findings,
        target_coverage=True,
        source_scope_complete=True,
        independent_review=True,
        ready_to_finish=True,
    )
