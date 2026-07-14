"""Provider-neutral validation for bounded workstream evidence records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from docsystem.config import WorkstreamCriterion

SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 256 * 1024
MAX_TEXT_LENGTH = 4096
MAX_LIST_ITEMS = 1000
EVIDENCE_FIELDS = frozenset(
    {"changes", "checks", "review", "omissions", "risks", "returns"}
)
TERMINAL_STATES = frozenset({"completed", "cancelled", "failed"})
TRANSITIONS = {
    "mandated": frozenset({"planned", "blocked", "cancelled", "failed"}),
    "planned": frozenset({"implementing", "blocked", "cancelled", "failed"}),
    "implementing": frozenset({"validating", "blocked", "cancelled", "failed"}),
    "validating": frozenset({"reviewing", "correcting", "blocked", "failed"}),
    "reviewing": frozenset({"correcting", "accepted", "blocked", "failed"}),
    "correcting": frozenset({"validating", "blocked", "failed"}),
    "accepted": frozenset({"finishing", "correcting", "blocked", "failed"}),
    "finishing": frozenset({"completed", "correcting", "blocked", "failed"}),
    "blocked": frozenset(),
    "completed": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
}


class WorkstreamError(ValueError):
    """A deterministic workstream record or evidence validation failure."""


@dataclass(frozen=True)
class HistoryEntry:
    state: str
    attempt: int
    evidence: str


@dataclass(frozen=True)
class Finding:
    finding_id: str
    attempt: int
    severity: str
    target: str
    evidence: str
    correction: str
    resolved_in_attempt: int | None


@dataclass(frozen=True)
class CheckEvidence:
    name: str
    status: str
    evidence: str


@dataclass(frozen=True)
class ReviewEvidence:
    status: str
    independent: bool
    reviewer: str
    evidence: str


@dataclass(frozen=True)
class WorkstreamEvidence:
    present: frozenset[str]
    changes: tuple[str, ...] = ()
    checks: tuple[CheckEvidence, ...] = ()
    review: ReviewEvidence | None = None
    omissions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    returns: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkstreamRecord:
    workstream_id: str
    criterion: str
    history: tuple[HistoryEntry, ...]
    findings: tuple[Finding, ...]
    evidence: WorkstreamEvidence

    @property
    def final_state(self) -> str:
        return self.history[-1].state

    @property
    def attempts(self) -> int:
        return max(entry.attempt for entry in self.history)


@dataclass(frozen=True)
class WorkstreamEvaluation:
    workstream_id: str
    criterion: str
    final_state: str
    attempts: int
    max_attempts: int
    findings: int
    resolved_findings: int
    ready_to_finish: bool


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise WorkstreamError(f"{field} must be an object with string keys")
    return value


def _exact_keys(
    value: dict[str, Any],
    field: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise WorkstreamError(
            f"{field} is missing required key(s): {', '.join(sorted(missing))}"
        )
    if unknown:
        raise WorkstreamError(
            f"{field} has unknown key(s): {', '.join(sorted(unknown))}"
        )


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkstreamError(f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > MAX_TEXT_LENGTH:
        raise WorkstreamError(
            f"{field} exceeds the bounded length of {MAX_TEXT_LENGTH} characters"
        )
    return result


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise WorkstreamError(f"{field} must be a positive integer")
    return value


def _string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise WorkstreamError(f"{field} must be a list")
    if len(value) > MAX_LIST_ITEMS:
        raise WorkstreamError(
            f"{field} exceeds the bounded limit of {MAX_LIST_ITEMS} items"
        )
    result = tuple(
        _string(item, f"{field}[{index}]") for index, item in enumerate(value)
    )
    if len(set(result)) != len(result):
        raise WorkstreamError(f"{field} must not contain duplicates")
    return result


def _history(value: object) -> tuple[HistoryEntry, ...]:
    if not isinstance(value, list) or not value:
        raise WorkstreamError("history must be a non-empty list")
    if len(value) > MAX_LIST_ITEMS:
        raise WorkstreamError(
            f"history exceeds the bounded limit of {MAX_LIST_ITEMS} items"
        )
    result: list[HistoryEntry] = []
    for index, raw in enumerate(value):
        field = f"history[{index}]"
        entry = _object(raw, field)
        _exact_keys(entry, field, {"state", "attempt", "evidence"})
        state = _string(entry["state"], f"{field}.state")
        if state not in TRANSITIONS:
            raise WorkstreamError(f"{field}.state is unsupported: {state}")
        result.append(
            HistoryEntry(
                state,
                _positive_int(entry["attempt"], f"{field}.attempt"),
                _string(entry["evidence"], f"{field}.evidence"),
            )
        )
    if result[0].state != "mandated" or result[0].attempt != 1:
        raise WorkstreamError("history must start with mandated attempt 1")
    for previous, current in pairwise(result):
        if current.state not in TRANSITIONS[previous.state]:
            raise WorkstreamError(
                f"illegal workstream transition: {previous.state} -> {current.state}"
            )
        expected_attempt = previous.attempt + (previous.state == "correcting")
        if current.attempt != expected_attempt:
            raise WorkstreamError(
                f"transition {previous.state} -> {current.state} requires attempt "
                f"{expected_attempt}, got {current.attempt}"
            )
    return tuple(result)


def _findings(value: object) -> tuple[Finding, ...]:
    if not isinstance(value, list):
        raise WorkstreamError("findings must be a list")
    if len(value) > MAX_LIST_ITEMS:
        raise WorkstreamError(
            f"findings exceeds the bounded limit of {MAX_LIST_ITEMS} items"
        )
    result: list[Finding] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        field = f"findings[{index}]"
        item = _object(raw, field)
        _exact_keys(
            item,
            field,
            {
                "id",
                "attempt",
                "severity",
                "target",
                "evidence",
                "correction",
                "resolved_in_attempt",
            },
        )
        finding_id = _string(item["id"], f"{field}.id")
        if finding_id in seen:
            raise WorkstreamError(f"duplicate finding ID: {finding_id}")
        seen.add(finding_id)
        attempt = _positive_int(item["attempt"], f"{field}.attempt")
        severity = _string(item["severity"], f"{field}.severity")
        if severity not in {"low", "medium", "high", "critical"}:
            raise WorkstreamError(f"{field}.severity is unsupported: {severity}")
        resolved = item["resolved_in_attempt"]
        if resolved is not None:
            resolved = _positive_int(resolved, f"{field}.resolved_in_attempt")
            if resolved <= attempt:
                raise WorkstreamError(
                    f"{field}.resolved_in_attempt must be greater than its attempt"
                )
        result.append(
            Finding(
                finding_id,
                attempt,
                severity,
                _string(item["target"], f"{field}.target"),
                _string(item["evidence"], f"{field}.evidence"),
                _string(item["correction"], f"{field}.correction"),
                resolved,
            )
        )
    return tuple(result)


def _checks(value: object) -> tuple[CheckEvidence, ...]:
    if not isinstance(value, list):
        raise WorkstreamError("evidence.checks must be a list")
    if len(value) > MAX_LIST_ITEMS:
        raise WorkstreamError(
            f"evidence.checks exceeds the bounded limit of {MAX_LIST_ITEMS} items"
        )
    result: list[CheckEvidence] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        field = f"evidence.checks[{index}]"
        item = _object(raw, field)
        _exact_keys(item, field, {"name", "status", "evidence"})
        name = _string(item["name"], f"{field}.name")
        if name in seen:
            raise WorkstreamError(f"duplicate check name: {name}")
        seen.add(name)
        status = _string(item["status"], f"{field}.status")
        if status not in {"passed", "failed", "skipped"}:
            raise WorkstreamError(f"{field}.status is unsupported: {status}")
        result.append(
            CheckEvidence(
                name,
                status,
                _string(item["evidence"], f"{field}.evidence"),
            )
        )
    return tuple(result)


def _review(value: object) -> ReviewEvidence:
    item = _object(value, "evidence.review")
    _exact_keys(
        item,
        "evidence.review",
        {"status", "independent", "reviewer", "evidence"},
    )
    status = _string(item["status"], "evidence.review.status")
    if status not in {"accepted", "rejected", "pending"}:
        raise WorkstreamError(f"evidence.review.status is unsupported: {status}")
    independent = item["independent"]
    if not isinstance(independent, bool):
        raise WorkstreamError("evidence.review.independent must be a boolean")
    return ReviewEvidence(
        status,
        independent,
        _string(item["reviewer"], "evidence.review.reviewer"),
        _string(item["evidence"], "evidence.review.evidence"),
    )


def _evidence(value: object) -> WorkstreamEvidence:
    item = _object(value, "evidence")
    unknown = set(item) - EVIDENCE_FIELDS
    if unknown:
        raise WorkstreamError(
            "evidence has unknown key(s): " + ", ".join(sorted(unknown))
        )
    return WorkstreamEvidence(
        present=frozenset(item),
        changes=(
            _string_list(item["changes"], "evidence.changes")
            if "changes" in item
            else ()
        ),
        checks=_checks(item["checks"]) if "checks" in item else (),
        review=_review(item["review"]) if "review" in item else None,
        omissions=(
            _string_list(item["omissions"], "evidence.omissions")
            if "omissions" in item
            else ()
        ),
        risks=(
            _string_list(item["risks"], "evidence.risks")
            if "risks" in item
            else ()
        ),
        returns=(
            _string_list(item["returns"], "evidence.returns")
            if "returns" in item
            else ()
        ),
    )


def load_record(path: Path) -> WorkstreamRecord:
    try:
        data = path.read_bytes()
        if len(data) > MAX_RECORD_BYTES:
            raise WorkstreamError(
                f"workstream record exceeds the bounded size of "
                f"{MAX_RECORD_BYTES} bytes"
            )
        raw = json.loads(data.decode("utf-8"))
    except WorkstreamError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise WorkstreamError(f"cannot read workstream record: {error}") from error
    item = _object(raw, "record")
    _exact_keys(
        item,
        "record",
        {
            "schema_version",
            "workstream_id",
            "criterion",
            "history",
            "findings",
            "evidence",
        },
    )
    if item["schema_version"] != SCHEMA_VERSION:
        raise WorkstreamError("unsupported workstream record schema_version")
    return WorkstreamRecord(
        _string(item["workstream_id"], "workstream_id"),
        _string(item["criterion"], "criterion"),
        _history(item["history"]),
        _findings(item["findings"]),
        _evidence(item["evidence"]),
    )


def evaluate_record(
    record: WorkstreamRecord,
    criterion: WorkstreamCriterion,
    *,
    section_anchors: frozenset[str],
) -> WorkstreamEvaluation:
    if record.criterion != criterion.reference:
        raise WorkstreamError(
            f"record criterion {record.criterion!r} does not match "
            f"{criterion.reference!r}"
        )
    missing_sections = set(criterion.required_sections) - section_anchors
    if missing_sections:
        raise WorkstreamError(
            "workstream mandate is missing required section(s): "
            + ", ".join(sorted(missing_sections))
        )
    if record.attempts > criterion.max_attempts:
        raise WorkstreamError(
            f"workstream uses {record.attempts} attempts; criterion allows "
            f"{criterion.max_attempts} and requires safe fallback "
            f"{criterion.safe_fallback}"
        )
    correction_attempts = {
        entry.attempt for entry in record.history if entry.state == "correcting"
    }
    finding_attempts = {finding.attempt for finding in record.findings}
    missing_findings = correction_attempts - finding_attempts
    if missing_findings:
        raise WorkstreamError(
            "correcting state has no finding evidence for attempt(s): "
            + ", ".join(str(item) for item in sorted(missing_findings))
        )
    for finding in record.findings:
        if finding.attempt > record.attempts:
            raise WorkstreamError(
                f"finding {finding.finding_id} names unavailable attempt "
                f"{finding.attempt}"
            )
        if (
            finding.resolved_in_attempt is not None
            and finding.resolved_in_attempt > record.attempts
        ):
            raise WorkstreamError(
                f"finding {finding.finding_id} resolves in unavailable attempt "
                f"{finding.resolved_in_attempt}"
            )

    ready = record.final_state == "completed"
    if ready:
        missing_evidence = set(criterion.required_evidence) - record.evidence.present
        if missing_evidence:
            raise WorkstreamError(
                "completed workstream is missing required evidence: "
                + ", ".join(sorted(missing_evidence))
            )
        unresolved = [
            finding.finding_id
            for finding in record.findings
            if finding.resolved_in_attempt is None
        ]
        if unresolved:
            raise WorkstreamError(
                "completed workstream has unresolved finding(s): "
                + ", ".join(sorted(unresolved))
            )
        if "changes" in criterion.required_evidence and not record.evidence.changes:
            raise WorkstreamError(
                "completed workstream requires at least one change address"
            )
        if "checks" in criterion.required_evidence:
            if not record.evidence.checks:
                raise WorkstreamError("completed workstream requires check evidence")
            failed = [
                check.name
                for check in record.evidence.checks
                if check.status != "passed"
            ]
            if failed:
                raise WorkstreamError(
                    "completed workstream has non-passing check(s): "
                    + ", ".join(sorted(failed))
                )
        if "review" in criterion.required_evidence:
            review = record.evidence.review
            if review is None or review.status != "accepted" or not review.independent:
                raise WorkstreamError(
                    "completed workstream requires accepted independent review evidence"
                )
        if "returns" in criterion.required_evidence and not record.evidence.returns:
            raise WorkstreamError(
                "completed workstream requires at least one return address"
            )

    return WorkstreamEvaluation(
        workstream_id=record.workstream_id,
        criterion=criterion.reference,
        final_state=record.final_state,
        attempts=record.attempts,
        max_attempts=criterion.max_attempts,
        findings=len(record.findings),
        resolved_findings=sum(
            finding.resolved_in_attempt is not None for finding in record.findings
        ),
        ready_to_finish=ready,
    )
