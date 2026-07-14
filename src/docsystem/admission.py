"""Bounded, provider-neutral execution admission request evaluation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from docsystem.config import (
    ADMISSION_ACTION_LEVELS,
    ADMISSION_AUTONOMY_LEVELS,
    ADMISSION_RISK_LEVELS,
    ADMISSION_VERIFICATION_LEVELS,
    AdmissionCriterion,
)

SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 128 * 1024
MAX_TEXT_LENGTH = 4096
MAX_ITEMS = 100
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BOUNDARY_NAMES = (
    "authored_deletion",
    "privacy_boundary",
    "permission_expansion",
    "external_commitment",
)


class AdmissionError(ValueError):
    """A deterministic admission request validation failure."""


@dataclass(frozen=True)
class AdmissionBoundaries:
    authored_deletion: bool
    privacy_boundary: bool
    permission_expansion: bool
    external_commitment: bool


@dataclass(frozen=True)
class AdmissionAuthorization:
    action: str
    authority: str
    evidence: str


@dataclass(frozen=True)
class AdmissionSource:
    path: str
    sha256: str | None


@dataclass(frozen=True)
class AdmissionRequest:
    workstream_id: str
    criterion: str
    intake_request_sha256: str | None
    outcome: str
    targets: tuple[str, ...]
    actions: tuple[str, ...]
    risk: str
    verification: str
    boundaries: AdmissionBoundaries
    authorizations: tuple[AdmissionAuthorization, ...]
    assumptions: tuple[str, ...]
    source_scope: tuple[AdmissionSource, ...]
    request_sha256: str


@dataclass(frozen=True)
class AdmissionEvaluation:
    decision: str
    reasons: tuple[str, ...]
    required_autonomy: str
    missing_authorizations: tuple[str, ...]

    @property
    def blocked(self) -> bool:
        return self.decision == "blocked"


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise AdmissionError(f"{field} must be an object with string keys")
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
        raise AdmissionError(
            f"{field} is missing required key(s): {', '.join(sorted(missing))}"
        )
    if unknown:
        raise AdmissionError(
            f"{field} has unknown key(s): {', '.join(sorted(unknown))}"
        )


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdmissionError(f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > MAX_TEXT_LENGTH:
        raise AdmissionError(
            f"{field} exceeds the bounded length of {MAX_TEXT_LENGTH} characters"
        )
    return result


def _string_list(
    value: object,
    field: str,
    *,
    allowed: set[str] | frozenset[str] | None = None,
    nonempty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise AdmissionError(f"{field} must be a list")
    if len(value) > MAX_ITEMS:
        raise AdmissionError(f"{field} exceeds the bounded limit of {MAX_ITEMS} items")
    result = tuple(
        _string(item, f"{field}[{index}]") for index, item in enumerate(value)
    )
    if nonempty and not result:
        raise AdmissionError(f"{field} must not be empty")
    if len(set(result)) != len(result):
        raise AdmissionError(f"{field} must not contain duplicates")
    if allowed is not None and any(item not in allowed for item in result):
        raise AdmissionError(
            f"{field} may contain only: {', '.join(sorted(allowed))}"
        )
    return tuple(sorted(result))


def _boundaries(value: object) -> AdmissionBoundaries:
    item = _object(value, "boundaries")
    _exact_keys(item, "boundaries", set(BOUNDARY_NAMES))
    for name in BOUNDARY_NAMES:
        if not isinstance(item[name], bool):
            raise AdmissionError(f"boundaries.{name} must be a boolean")
    return AdmissionBoundaries(**item)


def _authorizations(value: object) -> tuple[AdmissionAuthorization, ...]:
    if not isinstance(value, list):
        raise AdmissionError("authorizations must be a list")
    if len(value) > MAX_ITEMS:
        raise AdmissionError(
            f"authorizations exceeds the bounded limit of {MAX_ITEMS} items"
        )
    result: list[AdmissionAuthorization] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        field = f"authorizations[{index}]"
        item = _object(raw, field)
        _exact_keys(item, field, {"action", "authority", "evidence"})
        action = _string(item["action"], f"{field}.action")
        if action not in ADMISSION_ACTION_LEVELS:
            raise AdmissionError(
                f"{field}.action must be a supported admission action"
            )
        if action in seen:
            raise AdmissionError(f"duplicate authorization action: {action}")
        seen.add(action)
        result.append(
            AdmissionAuthorization(
                action=action,
                authority=_string(item["authority"], f"{field}.authority"),
                evidence=_string(item["evidence"], f"{field}.evidence"),
            )
        )
    return tuple(sorted(result, key=lambda item: item.action))


def normalize_source_path(value: object, field: str) -> str:
    path = _string(value, field)
    if "\\" in path:
        raise AdmissionError(f"{field} must use POSIX separators")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or path in {".", ".."} or ".." in parsed.parts:
        raise AdmissionError(f"{field} must be a relative non-escaping file path")
    normalized = parsed.as_posix()
    if normalized != path or normalized.startswith("./"):
        raise AdmissionError(f"{field} must be a normalized POSIX path")
    return normalized


def _source_scope(value: object) -> tuple[AdmissionSource, ...]:
    if not isinstance(value, list):
        raise AdmissionError("source_scope must be a list")
    if len(value) > MAX_ITEMS:
        raise AdmissionError(
            f"source_scope exceeds the bounded limit of {MAX_ITEMS} items"
        )
    result: list[AdmissionSource] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        field = f"source_scope[{index}]"
        item = _object(raw, field)
        _exact_keys(item, field, {"path", "sha256"})
        path = normalize_source_path(item["path"], f"{field}.path")
        if path in seen:
            raise AdmissionError(f"duplicate source_scope path: {path}")
        seen.add(path)
        raw_digest = item["sha256"]
        digest: str | None
        if raw_digest is None:
            digest = None
        else:
            digest = _string(raw_digest, f"{field}.sha256")
            if not SHA256_PATTERN.fullmatch(digest):
                raise AdmissionError(
                    f"{field}.sha256 must be null or a lowercase SHA-256"
                )
        result.append(AdmissionSource(path=path, sha256=digest))
    return tuple(sorted(result, key=lambda item: item.path))


def _request_hash(
    *,
    workstream_id: str,
    criterion: str,
    intake_request_sha256: str | None,
    outcome: str,
    targets: tuple[str, ...],
    actions: tuple[str, ...],
    risk: str,
    verification: str,
    boundaries: AdmissionBoundaries,
    authorizations: tuple[AdmissionAuthorization, ...],
    assumptions: tuple[str, ...],
    source_scope: tuple[AdmissionSource, ...],
) -> str:
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "workstream_id": workstream_id,
        "criterion": criterion,
        "intake_request_sha256": intake_request_sha256,
        "outcome": outcome,
        "targets": list(targets),
        "actions": list(actions),
        "risk": risk,
        "verification": verification,
        "boundaries": {
            name: getattr(boundaries, name) for name in BOUNDARY_NAMES
        },
        "authorizations": [
            {
                "action": item.action,
                "authority": item.authority,
                "evidence": item.evidence,
            }
            for item in authorizations
        ],
        "assumptions": list(assumptions),
    }
    if source_scope:
        normalized["source_scope"] = [
            {"path": item.path, "sha256": item.sha256} for item in source_scope
        ]
    text = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(text.encode()).hexdigest()


def load_request(path: Path) -> AdmissionRequest:
    try:
        data = path.read_bytes()
        if len(data) > MAX_REQUEST_BYTES:
            raise AdmissionError(
                f"admission request exceeds the bounded size of "
                f"{MAX_REQUEST_BYTES} bytes"
            )
        raw = json.loads(data.decode("utf-8"))
    except AdmissionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AdmissionError(f"cannot read admission request: {error}") from error
    item = _object(raw, "request")
    _exact_keys(
        item,
        "request",
        {
            "schema_version",
            "workstream_id",
            "criterion",
            "intake_request_sha256",
            "outcome",
            "targets",
            "actions",
            "risk",
            "verification",
            "boundaries",
            "authorizations",
            "assumptions",
        },
        {"source_scope"},
    )
    if item["schema_version"] != SCHEMA_VERSION:
        raise AdmissionError("unsupported admission request schema_version")
    workstream_id = _string(item["workstream_id"], "workstream_id")
    criterion = _string(item["criterion"], "criterion")
    raw_intake_hash = item["intake_request_sha256"]
    intake_hash: str | None
    if raw_intake_hash is None:
        intake_hash = None
    else:
        intake_hash = _string(raw_intake_hash, "intake_request_sha256")
        if not SHA256_PATTERN.fullmatch(intake_hash):
            raise AdmissionError(
                "intake_request_sha256 must be null or a lowercase SHA-256"
            )
    outcome = _string(item["outcome"], "outcome")
    targets = _string_list(item["targets"], "targets", nonempty=True)
    actions = _string_list(
        item["actions"],
        "actions",
        allowed=set(ADMISSION_ACTION_LEVELS),
        nonempty=True,
    )
    risk = _string(item["risk"], "risk")
    if risk not in ADMISSION_RISK_LEVELS:
        raise AdmissionError("risk must be low, medium or high")
    verification = _string(item["verification"], "verification")
    if verification not in ADMISSION_VERIFICATION_LEVELS:
        raise AdmissionError("verification must be structural, focused or full")
    boundaries = _boundaries(item["boundaries"])
    authorizations = _authorizations(item["authorizations"])
    unexpected = sorted(
        authorization.action
        for authorization in authorizations
        if authorization.action not in actions
    )
    if unexpected:
        raise AdmissionError(
            "authorization names unrequested action(s): " + ", ".join(unexpected)
        )
    assumptions = _string_list(item["assumptions"], "assumptions")
    source_scope = _source_scope(item.get("source_scope", []))
    return AdmissionRequest(
        workstream_id=workstream_id,
        criterion=criterion,
        intake_request_sha256=intake_hash,
        outcome=outcome,
        targets=targets,
        actions=actions,
        risk=risk,
        verification=verification,
        boundaries=boundaries,
        authorizations=authorizations,
        assumptions=assumptions,
        source_scope=source_scope,
        request_sha256=_request_hash(
            workstream_id=workstream_id,
            criterion=criterion,
            intake_request_sha256=intake_hash,
            outcome=outcome,
            targets=targets,
            actions=actions,
            risk=risk,
            verification=verification,
            boundaries=boundaries,
            authorizations=authorizations,
            assumptions=assumptions,
            source_scope=source_scope,
        ),
    )


def evaluate_request(
    request: AdmissionRequest, criterion: AdmissionCriterion
) -> AdmissionEvaluation:
    if request.criterion != criterion.reference:
        raise AdmissionError(
            f"request criterion {request.criterion!r} does not match "
            f"{criterion.reference!r}"
        )
    if len(request.targets) > criterion.max_targets:
        raise AdmissionError(
            f"request has {len(request.targets)} targets; criterion allows "
            f"{criterion.max_targets}"
        )
    required_level = max(ADMISSION_ACTION_LEVELS[action] for action in request.actions)
    required_autonomy = f"A{required_level}"
    reasons: list[str] = []
    for name in BOUNDARY_NAMES:
        if getattr(request.boundaries, name):
            reasons.append("boundary:" + name.replace("_", "-"))
    for action in request.actions:
        if action not in criterion.allowed_actions:
            reasons.append(f"action-not-allowed:{action}")
    if required_level > ADMISSION_AUTONOMY_LEVELS[criterion.max_autonomy]:
        reasons.append("autonomy-exceeds-policy")
    if ADMISSION_RISK_LEVELS[request.risk] > ADMISSION_RISK_LEVELS[criterion.max_risk]:
        reasons.append("risk-exceeds-policy")
    if request.verification not in criterion.allowed_verification:
        reasons.append("verification-not-allowed")
    authorized = {item.action for item in request.authorizations}
    missing = tuple(
        sorted(
            action
            for action in criterion.required_authorizations
            if action in request.actions and action not in authorized
        )
    )
    reasons.extend(f"authorization-missing:{action}" for action in missing)
    reasons.extend(
        f"source-scope-required:{action}"
        for action in criterion.require_source_scope_for
        if action in request.actions and not request.source_scope
    )
    if reasons:
        return AdmissionEvaluation(
            "blocked", tuple(reasons), required_autonomy, missing
        )
    return AdmissionEvaluation("admitted", ("policy-satisfied",), required_autonomy, ())
