"""Bounded, provider-neutral semantic intake request evaluation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docsystem.config import IntakeCriterion

SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
MAX_TEXT_LENGTH = 4096
MAX_ITEMS = 50


class IntakeError(ValueError):
    """A deterministic intake request validation failure."""


@dataclass(frozen=True)
class IntakeCandidate:
    address: str
    authority: str


@dataclass(frozen=True)
class IntakeSignals:
    authority_conflict: bool
    incompatible_outcomes: bool
    independent_lifecycle: bool
    existing_owner_sufficient: bool


@dataclass(frozen=True)
class IntakeRequest:
    idea_id: str
    criterion: str
    outcome: str
    source: str
    candidates: tuple[IntakeCandidate, ...]
    signals: IntakeSignals
    assumptions: tuple[str, ...]
    request_sha256: str


@dataclass(frozen=True)
class IntakeEvaluation:
    decision: str
    reasons: tuple[str, ...]
    requested_decision: str | None

    @property
    def blocked(self) -> bool:
        return self.decision == "blocked"


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise IntakeError(f"{field} must be an object with string keys")
    return value


def _exact_keys(value: dict[str, Any], field: str, required: set[str]) -> None:
    missing = required - set(value)
    unknown = set(value) - required
    if missing:
        raise IntakeError(
            f"{field} is missing required key(s): {', '.join(sorted(missing))}"
        )
    if unknown:
        raise IntakeError(
            f"{field} has unknown key(s): {', '.join(sorted(unknown))}"
        )


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntakeError(f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > MAX_TEXT_LENGTH:
        raise IntakeError(
            f"{field} exceeds the bounded length of {MAX_TEXT_LENGTH} characters"
        )
    return result


def _string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise IntakeError(f"{field} must be a list")
    if len(value) > MAX_ITEMS:
        raise IntakeError(f"{field} exceeds the bounded limit of {MAX_ITEMS} items")
    result = tuple(
        _string(item, f"{field}[{index}]") for index, item in enumerate(value)
    )
    if len(set(result)) != len(result):
        raise IntakeError(f"{field} must not contain duplicates")
    return result


def _candidates(value: object) -> tuple[IntakeCandidate, ...]:
    if not isinstance(value, list):
        raise IntakeError("candidates must be a list")
    if len(value) > MAX_ITEMS:
        raise IntakeError(
            f"candidates exceeds the bounded limit of {MAX_ITEMS} items"
        )
    result: list[IntakeCandidate] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        field = f"candidates[{index}]"
        item = _object(raw, field)
        _exact_keys(item, field, {"address", "authority"})
        address = _string(item["address"], f"{field}.address")
        if address in seen:
            raise IntakeError(f"duplicate candidate address: {address}")
        seen.add(address)
        authority = _string(item["authority"], f"{field}.authority")
        if authority not in {"owner", "related"}:
            raise IntakeError(
                f"{field}.authority must be 'owner' or 'related'"
            )
        result.append(IntakeCandidate(address, authority))
    return tuple(sorted(result, key=lambda item: (item.authority, item.address)))


def _signals(value: object) -> IntakeSignals:
    item = _object(value, "signals")
    names = {
        "authority_conflict",
        "incompatible_outcomes",
        "independent_lifecycle",
        "existing_owner_sufficient",
    }
    _exact_keys(item, "signals", names)
    for name in names:
        if not isinstance(item[name], bool):
            raise IntakeError(f"signals.{name} must be a boolean")
    return IntakeSignals(
        authority_conflict=item["authority_conflict"],
        incompatible_outcomes=item["incompatible_outcomes"],
        independent_lifecycle=item["independent_lifecycle"],
        existing_owner_sufficient=item["existing_owner_sufficient"],
    )


def _request_hash(
    *,
    idea_id: str,
    criterion: str,
    outcome: str,
    source: str,
    candidates: tuple[IntakeCandidate, ...],
    signals: IntakeSignals,
    assumptions: tuple[str, ...],
) -> str:
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "idea_id": idea_id,
        "criterion": criterion,
        "outcome": outcome,
        "source": source,
        "candidates": [
            {"address": item.address, "authority": item.authority}
            for item in candidates
        ],
        "signals": {
            "authority_conflict": signals.authority_conflict,
            "incompatible_outcomes": signals.incompatible_outcomes,
            "independent_lifecycle": signals.independent_lifecycle,
            "existing_owner_sufficient": signals.existing_owner_sufficient,
        },
        "assumptions": list(assumptions),
    }
    text = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(text.encode()).hexdigest()


def load_request(path: Path) -> IntakeRequest:
    try:
        data = path.read_bytes()
        if len(data) > MAX_REQUEST_BYTES:
            raise IntakeError(
                f"intake request exceeds the bounded size of "
                f"{MAX_REQUEST_BYTES} bytes"
            )
        raw = json.loads(data.decode("utf-8"))
    except IntakeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise IntakeError(f"cannot read intake request: {error}") from error
    item = _object(raw, "request")
    _exact_keys(
        item,
        "request",
        {
            "schema_version",
            "idea_id",
            "criterion",
            "outcome",
            "source",
            "candidates",
            "signals",
            "assumptions",
        },
    )
    if item["schema_version"] != SCHEMA_VERSION:
        raise IntakeError("unsupported intake request schema_version")
    idea_id = _string(item["idea_id"], "idea_id")
    criterion = _string(item["criterion"], "criterion")
    outcome = _string(item["outcome"], "outcome")
    source = _string(item["source"], "source")
    candidates = _candidates(item["candidates"])
    signals = _signals(item["signals"])
    assumptions = tuple(
        sorted(_string_list(item["assumptions"], "assumptions"))
    )
    return IntakeRequest(
        idea_id=idea_id,
        criterion=criterion,
        outcome=outcome,
        source=source,
        candidates=candidates,
        signals=signals,
        assumptions=assumptions,
        request_sha256=_request_hash(
            idea_id=idea_id,
            criterion=criterion,
            outcome=outcome,
            source=source,
            candidates=candidates,
            signals=signals,
            assumptions=assumptions,
        ),
    )


def evaluate_request(
    request: IntakeRequest, criterion: IntakeCriterion
) -> IntakeEvaluation:
    if request.criterion != criterion.reference:
        raise IntakeError(
            f"request criterion {request.criterion!r} does not match "
            f"{criterion.reference!r}"
        )
    if len(request.candidates) > criterion.max_candidates:
        raise IntakeError(
            f"request has {len(request.candidates)} candidates; criterion allows "
            f"{criterion.max_candidates}"
        )

    reasons: list[str] = []
    if request.signals.authority_conflict:
        reasons.append("authority-conflict")
    if request.signals.incompatible_outcomes:
        reasons.append("incompatible-outcomes")
    if (
        request.signals.existing_owner_sufficient
        and request.signals.independent_lifecycle
    ):
        reasons.append("contradictory-owner-and-lifecycle")
    if reasons:
        return IntakeEvaluation("blocked", tuple(reasons), None)

    if request.signals.existing_owner_sufficient:
        owners = [
            item for item in request.candidates if item.authority == "owner"
        ]
        if len(owners) != 1:
            return IntakeEvaluation(
                "blocked", ("owner-evidence-ambiguous",), "update-existing"
            )
        desired = "update-existing"
        reason = "existing-owner-sufficient"
    elif request.signals.independent_lifecycle:
        desired = "create-workstream"
        reason = "independent-lifecycle"
    else:
        desired = "create-draft"
        reason = "no-sufficient-owner"

    if desired not in criterion.allowed_decisions:
        return IntakeEvaluation(
            "blocked", ("decision-not-authorized",), desired
        )
    return IntakeEvaluation(desired, (reason,), desired)
