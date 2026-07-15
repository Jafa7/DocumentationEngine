"""Deterministic sequencing for authored program roadmaps."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from docsystem.catalog import MarkdownCatalog, MarkdownDocument
from docsystem.graph import parse_address

PROGRAM_PLAN_FIELD = "program_plan"
PROGRAM_PLAN_VERSION = 1
MILESTONE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
AUTHORED_STATES = frozenset({"planned", "waiting", "deferred"})
ROADMAP_STATES = {
    "proposed": "planned",
    "planned": "planned",
    "waiting": "waiting",
    "ready": "ready",
    "active": "active",
    "blocked": "blocked",
    "completed": "delivered",
    "delivered": "delivered",
    "deferred": "deferred",
    "cancelled": "cancelled",
    "failed": "failed",
}


@dataclass(frozen=True)
class ProgramPlanViolation:
    """One precise authored-plan integrity failure."""

    document_id: str
    path: str
    code: str
    subject: str
    detail: str


@dataclass(frozen=True)
class ProgramMilestone:
    """One ordered milestone in a program plan."""

    key: str
    title: str
    order: int
    priority: int
    state: str
    roadmap_id: str | None
    prerequisites: tuple[str, ...]
    source_contracts: tuple[str, ...]
    waiting_for: str | None
    reopen_when: str | None
    unlocks: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class ProgramPlan:
    """A validated, body-free execution view of one authored program."""

    program_id: str
    path: str
    revision: int
    valid: bool
    action: str
    milestones: tuple[ProgramMilestone, ...]
    recommended: tuple[str, ...]
    violations: tuple[ProgramPlanViolation, ...]


def _thaw(value: object) -> object:
    if isinstance(value, tuple):
        if value and all(
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            for item in value
        ):
            return {key: _thaw(item) for key, item in value}
        return [_thaw(item) for item in value]
    return value


def _violation(
    document: MarkdownDocument, code: str, subject: str, detail: str
) -> ProgramPlanViolation:
    assert document.metadata is not None
    return ProgramPlanViolation(
        document.metadata.document_id,
        document.path.as_posix(),
        code,
        subject,
        detail,
    )


def _string_list(
    document: MarkdownDocument,
    item: dict[str, object],
    field: str,
    subject: str,
    violations: list[ProgramPlanViolation],
) -> tuple[str, ...]:
    value = item.get(field, [])
    if not isinstance(value, list) or any(
        not isinstance(entry, str) or not entry for entry in value
    ):
        violations.append(
            _violation(
                document,
                f"invalid-{field.replace('_', '-')}",
                subject,
                f"{field} must be a list of non-empty strings",
            )
        )
        return ()
    strings = tuple(value)
    if len(set(strings)) != len(strings):
        violations.append(
            _violation(
                document,
                f"duplicate-{field.replace('_', '-')}",
                subject,
                f"{field} must not contain duplicates",
            )
        )
    return strings


def _parse_milestone(
    document: MarkdownDocument,
    raw: object,
    position: int,
    documents: dict[str, MarkdownDocument],
    violations: list[ProgramPlanViolation],
) -> ProgramMilestone | None:
    subject = f"milestones[{position}]"
    if not isinstance(raw, dict):
        violations.append(
            _violation(document, "invalid-milestone", subject, "must be a mapping")
        )
        return None
    allowed = {
        "id",
        "title",
        "order",
        "priority",
        "state",
        "roadmap",
        "prerequisites",
        "source_contracts",
        "waiting_for",
        "reopen_when",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        violations.append(
            _violation(
                document,
                "unknown-milestone-field",
                subject,
                "unknown fields: " + ", ".join(unknown),
            )
        )

    key = raw.get("id")
    if not isinstance(key, str) or MILESTONE_KEY_PATTERN.fullmatch(key) is None:
        violations.append(
            _violation(
                document,
                "invalid-milestone-id",
                subject,
                "id must be a lowercase slug",
            )
        )
        return None
    subject = key
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        violations.append(
            _violation(document, "invalid-title", subject, "title must be non-empty")
        )
        title = key
    order = raw.get("order")
    if isinstance(order, bool) or not isinstance(order, int) or order < 1:
        violations.append(
            _violation(document, "invalid-order", subject, "order must be positive")
        )
        order = position + 1
    priority = raw.get("priority", order)
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 1:
        violations.append(
            _violation(
                document, "invalid-priority", subject, "priority must be positive"
            )
        )
        priority = order

    roadmap = raw.get("roadmap")
    state = raw.get("state")
    if roadmap is not None and state is not None:
        violations.append(
            _violation(
                document,
                "duplicated-state-authority",
                subject,
                "state must be omitted when roadmap derives lifecycle state",
            )
        )
    if roadmap is None:
        if state not in AUTHORED_STATES:
            violations.append(
                _violation(
                    document,
                    "invalid-state",
                    subject,
                    "state must be planned, waiting or deferred before a roadmap exists",
                )
            )
            state = "blocked"
    elif not isinstance(roadmap, str) or roadmap not in documents:
        violations.append(
            _violation(
                document,
                "unknown-roadmap",
                subject,
                f"roadmap document is not cataloged: {roadmap!r}",
            )
        )
        roadmap = None
        state = "blocked"
    elif roadmap == document.metadata.document_id:
        violations.append(
            _violation(
                document,
                "self-roadmap-owner",
                subject,
                "program document cannot be its own bounded roadmap owner",
            )
        )
        roadmap = None
        state = "blocked"
    else:
        target = documents[roadmap]
        assert target.metadata is not None
        target_state = target.metadata.status
        if target.metadata.document_type != "roadmap":
            violations.append(
                _violation(
                    document,
                    "invalid-roadmap-type",
                    subject,
                    f"{roadmap} must use type roadmap, found "
                    f"{target.metadata.document_type!r}",
                )
            )
            state = "blocked"
        elif target_state not in ROADMAP_STATES:
            violations.append(
                _violation(
                    document,
                    "unsupported-roadmap-state",
                    subject,
                    f"{roadmap} has unsupported status {target_state!r}",
                )
            )
            state = "blocked"
        else:
            state = ROADMAP_STATES[target_state]

    prerequisites = _string_list(
        document, raw, "prerequisites", subject, violations
    )
    source_contracts = _string_list(
        document, raw, "source_contracts", subject, violations
    )
    waiting_for = raw.get("waiting_for")
    reopen_when = raw.get("reopen_when")
    if waiting_for is not None and (
        not isinstance(waiting_for, str) or not waiting_for.strip()
    ):
        violations.append(
            _violation(
                document,
                "invalid-waiting-condition",
                subject,
                "waiting_for must be a non-empty string",
            )
        )
        waiting_for = None
    if reopen_when is not None and (
        not isinstance(reopen_when, str) or not reopen_when.strip()
    ):
        violations.append(
            _violation(
                document,
                "invalid-reopen-condition",
                subject,
                "reopen_when must be a non-empty string",
            )
        )
        reopen_when = None
    if state == "waiting" and waiting_for is None:
        violations.append(
            _violation(
                document,
                "missing-waiting-condition",
                subject,
                "waiting milestones require waiting_for, including roadmap-derived waiting state",
            )
        )
    elif state != "waiting" and waiting_for is not None:
        violations.append(
            _violation(
                document,
                "unexpected-waiting-condition",
                subject,
                "waiting_for is allowed only for waiting milestones",
            )
        )
    if state == "deferred" and reopen_when is None:
        violations.append(
            _violation(
                document,
                "missing-reopen-condition",
                subject,
                "deferred milestones require reopen_when, including roadmap-derived deferred state",
            )
        )
    elif state != "deferred" and reopen_when is not None:
        violations.append(
            _violation(
                document,
                "unexpected-reopen-condition",
                subject,
                "reopen_when is allowed only for deferred milestones",
            )
        )

    for contract in source_contracts:
        try:
            address = parse_address(contract)
        except ValueError as error:
            violations.append(
                _violation(
                    document, "invalid-source-contract", subject, str(error)
                )
            )
            continue
        target = documents.get(address.document_id)
        if address.anchor is None:
            violations.append(
                _violation(
                    document,
                    "document-only-source-contract",
                    subject,
                    f"source contract must use ID#anchor: {contract}",
                )
            )
        elif target is None:
            violations.append(
                _violation(
                    document,
                    "unknown-source-document",
                    subject,
                    f"source document is not cataloged: {address.document_id}",
                )
            )
        elif address.anchor not in {section.anchor for section in target.sections}:
            violations.append(
                _violation(
                    document,
                    "unknown-source-anchor",
                    subject,
                    f"source document has no matching anchor: {contract}",
                )
            )

    assert isinstance(title, str)
    assert isinstance(order, int)
    assert isinstance(priority, int)
    assert isinstance(state, str)
    return ProgramMilestone(
        key,
        title.strip(),
        order,
        priority,
        state,
        roadmap,
        prerequisites,
        source_contracts,
        waiting_for if isinstance(waiting_for, str) else None,
        reopen_when if isinstance(reopen_when, str) else None,
    )


def _cycle_nodes(milestones: dict[str, ProgramMilestone]) -> tuple[str, ...]:
    visiting: set[str] = set()
    visited: set[str] = set()
    cycles: set[str] = set()

    def visit(key: str, path: tuple[str, ...]) -> None:
        if key in visited:
            return
        if key in visiting:
            cycles.update(path[path.index(key) :])
            return
        visiting.add(key)
        for prerequisite in milestones[key].prerequisites:
            if prerequisite in milestones:
                visit(prerequisite, (*path, prerequisite))
        visiting.remove(key)
        visited.add(key)

    for key in sorted(milestones):
        visit(key, (key,))
    return tuple(sorted(cycles))


def _evaluate_document(
    document: MarkdownDocument, documents: dict[str, MarkdownDocument]
) -> ProgramPlan:
    assert document.metadata is not None
    violations: list[ProgramPlanViolation] = []
    if document.metadata.document_type != "roadmap":
        violations.append(
            _violation(
                document,
                "invalid-program-type",
                PROGRAM_PLAN_FIELD,
                "a program_plan owner must use type roadmap",
            )
        )
    additional = dict(document.metadata.additional_fields)
    raw = _thaw(additional[PROGRAM_PLAN_FIELD])
    if not isinstance(raw, dict):
        violations.append(
            _violation(
                document,
                "invalid-program-plan",
                PROGRAM_PLAN_FIELD,
                "program_plan must be a mapping",
            )
        )
        raw = {}
    unknown = sorted(set(raw) - {"version", "milestones"})
    if unknown:
        violations.append(
            _violation(
                document,
                "unknown-program-field",
                PROGRAM_PLAN_FIELD,
                "unknown fields: " + ", ".join(unknown),
            )
        )
    version = raw.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != PROGRAM_PLAN_VERSION
    ):
        violations.append(
            _violation(
                document,
                "unsupported-program-version",
                PROGRAM_PLAN_FIELD,
                f"version must be {PROGRAM_PLAN_VERSION}",
            )
        )
    raw_milestones = raw.get("milestones")
    if not isinstance(raw_milestones, list) or not raw_milestones:
        violations.append(
            _violation(
                document,
                "invalid-milestones",
                PROGRAM_PLAN_FIELD,
                "milestones must be a non-empty list",
            )
        )
        raw_milestones = []
    parsed = [
        milestone
        for position, item in enumerate(raw_milestones)
        if (milestone := _parse_milestone(
            document, item, position, documents, violations
        ))
        is not None
    ]
    keys = [item.key for item in parsed]
    orders = [item.order for item in parsed]
    roadmap_ids = [item.roadmap_id for item in parsed if item.roadmap_id is not None]
    for value, code, label in (
        (keys, "duplicate-milestone-id", "milestone id"),
        (orders, "duplicate-order", "order"),
        (roadmap_ids, "duplicate-roadmap-owner", "roadmap owner"),
    ):
        duplicates = sorted({item for item in value if value.count(item) > 1})
        for duplicate in duplicates:
            violations.append(
                _violation(
                    document,
                    code,
                    str(duplicate),
                    f"{label} must be unique within one program",
                )
            )
    by_key = {item.key: item for item in parsed}
    for item in parsed:
        for prerequisite in item.prerequisites:
            if prerequisite == item.key:
                violations.append(
                    _violation(
                        document,
                        "self-prerequisite",
                        item.key,
                        "milestone cannot depend on itself",
                    )
                )
            elif prerequisite not in by_key:
                violations.append(
                    _violation(
                        document,
                        "unknown-prerequisite",
                        item.key,
                        f"unknown milestone: {prerequisite}",
                    )
                )
    cycles = _cycle_nodes(by_key)
    if cycles:
        violations.append(
            _violation(
                document,
                "prerequisite-cycle",
                ",".join(cycles),
                "milestone prerequisite graph contains a cycle",
            )
        )

    unlocks: dict[str, list[str]] = {key: [] for key in by_key}
    for item in parsed:
        for prerequisite in item.prerequisites:
            if prerequisite in unlocks:
                unlocks[prerequisite].append(item.key)
    evaluated: list[ProgramMilestone] = []
    base_states = {item.key: item.state for item in parsed}
    for item in parsed:
        incomplete = tuple(
            prerequisite
            for prerequisite in item.prerequisites
            if base_states.get(prerequisite) != "delivered"
        )
        state = item.state
        reason = ""
        if state in {"planned", "ready"}:
            if incomplete:
                state = "waiting"
                reason = "prerequisites: " + ", ".join(incomplete)
            else:
                state = "ready"
                reason = "all prerequisites delivered"
        elif state == "waiting":
            reason = item.waiting_for or "roadmap status is waiting"
        elif state == "deferred":
            reason = item.reopen_when or "roadmap status is deferred"
        elif state in {"active", "delivered"} and incomplete:
            violations.append(
                _violation(
                    document,
                    "prerequisite-state-conflict",
                    item.key,
                    f"{state} milestone has undelivered prerequisites: "
                    + ", ".join(incomplete),
                )
            )
        elif state == "active":
            reason = "continue active roadmap"
        elif state == "delivered":
            reason = "roadmap completion state"
        elif state in {"blocked", "failed", "cancelled"}:
            reason = f"roadmap status is {state}"
        evaluated.append(
            replace(
                item,
                state=state,
                unlocks=tuple(sorted(unlocks[item.key])),
                reason=reason,
            )
        )
    evaluated.sort(key=lambda item: (item.order, item.key))
    active = tuple(item for item in evaluated if item.state == "active")
    ready = tuple(item for item in evaluated if item.state == "ready")
    if active:
        action = "continue"
        candidates = active
    elif ready:
        action = "start"
        best_priority = min(item.priority for item in ready)
        candidates = tuple(item for item in ready if item.priority == best_priority)
    elif evaluated and all(
        item.state in {"delivered", "cancelled"} for item in evaluated
    ):
        action = "complete"
        candidates = ()
    elif evaluated and all(
        item.state in {"delivered", "deferred", "cancelled"} for item in evaluated
    ):
        action = "deferred"
        candidates = ()
    else:
        action = "blocked"
        candidates = ()
    return ProgramPlan(
        document.metadata.document_id,
        document.path.as_posix(),
        document.metadata.revision,
        not violations,
        action,
        tuple(evaluated),
        tuple(item.key for item in candidates),
        tuple(violations),
    )


def evaluate_program_plans(catalog: MarkdownCatalog) -> tuple[ProgramPlan, ...]:
    """Evaluate every explicitly authored ``program_plan`` in a catalog."""

    documents = {
        document.metadata.document_id: document
        for document in catalog.documents
        if document.metadata is not None
    }
    program_documents = [
        document
        for document in documents.values()
        if PROGRAM_PLAN_FIELD in dict(document.metadata.additional_fields)
    ]
    plans = tuple(
        _evaluate_document(document, documents)
        for document in sorted(
            program_documents, key=lambda item: item.metadata.document_id
        )
    )
    owners: dict[str, list[ProgramPlan]] = {}
    for plan in plans:
        for milestone in plan.milestones:
            if milestone.roadmap_id is not None:
                owners.setdefault(milestone.roadmap_id, []).append(plan)
    collisions = {
        roadmap_id: owner_plans
        for roadmap_id, owner_plans in owners.items()
        if len({plan.program_id for plan in owner_plans}) > 1
    }
    if not collisions:
        return plans
    updated: list[ProgramPlan] = []
    for plan in plans:
        extra: list[ProgramPlanViolation] = []
        document = documents[plan.program_id]
        for roadmap_id, owner_plans in sorted(collisions.items()):
            owner_ids = tuple(sorted({item.program_id for item in owner_plans}))
            if plan.program_id not in owner_ids:
                continue
            extra.append(
                _violation(
                    document,
                    "cross-program-roadmap-owner",
                    roadmap_id,
                    "bounded roadmap is owned by multiple programs: "
                    + ", ".join(owner_ids),
                )
            )
        updated.append(
            replace(
                plan,
                valid=plan.valid and not extra,
                violations=(*plan.violations, *extra),
            )
        )
    return tuple(updated)


def select_program_plan(
    plans: tuple[ProgramPlan, ...], program_id: str | None
) -> ProgramPlan:
    """Select one program explicitly or require an unambiguous catalog."""

    if program_id is not None:
        for plan in plans:
            if plan.program_id == program_id:
                return plan
        raise ValueError(f"unknown program plan: {program_id}")
    if not plans:
        raise ValueError("catalog has no authored program_plan")
    if len(plans) > 1:
        identifiers = ", ".join(plan.program_id for plan in plans)
        raise ValueError(f"multiple program plans require --program: {identifiers}")
    return plans[0]


def select_milestone(plan: ProgramPlan, key_or_roadmap: str) -> ProgramMilestone:
    """Resolve one milestone by local key or assigned roadmap ID."""

    matches = tuple(
        item
        for item in plan.milestones
        if item.key == key_or_roadmap or item.roadmap_id == key_or_roadmap
    )
    if not matches:
        raise ValueError(f"unknown program milestone: {key_or_roadmap}")
    return matches[0]
