"""Read-only, explainable change-planning models over the section/reference graph.

This module is pure and provider-neutral: it turns already-computed forward
and reverse traversal reasons (see `docsystem.graph.traverse_reasons`) and
graph boundaries into an immutable plan. It never edits Markdown, never reads
Markdown or a projection itself, and grants no write authority -- only `read`
and `review` dispositions exist in this milestone. `docsystem.cli.change_plan`
owns direct/projected resolution and fallback, exactly like `references`. The
public contract is documented in `docs/agent-contract.md`.
"""

from __future__ import annotations

from dataclasses import dataclass

from docsystem.delivery import DeliveryReport
from docsystem.graph import AUTHORED, GENERATED, Address, Boundary, TraversalResult

# Dispositions. This milestone never produces anything beyond `read`/`review`:
# no edge, however authored, grants write permission.
READ = "read"
REVIEW = "review"

# Completeness vocabulary shared by all three graph authority layers.
COMPLETE = "complete"  # layer fully resolved; no unresolved boundary remains.
BOUNDED = "bounded"  # layer visible; every unresolved target is a listed boundary.
UNKNOWN = "unknown"  # completeness cannot be proven (reverse-observed evidence).
NOT_ENUMERATED = "not-enumerated"  # layer is intentionally traversal-only evidence.

# `InclusionReason.scope`: which side of the query produced this reason.
TARGET_SCOPE = "target"
FORWARD_SCOPE = "forward"
REVERSE_SCOPE = "reverse"

# The only relation whose authored authority promotes an item to `read`.
# `related`, `derived_from`, `supersedes` and `validated_against` stay
# `review`: they are provenance, lineage or navigation, not a semantic
# dependency the target cannot be safely changed without reading.
_READ_RELATION = "depends_on"


@dataclass(frozen=True)
class InclusionReason:
    """One deterministic, provable cause an address is part of the plan."""

    scope: str  # "target" | "forward" | "reverse"
    relation: str
    authority: str
    origin: str
    distance: int
    direct: bool
    path: tuple[Address, ...]
    detail: str | None = None


@dataclass(frozen=True)
class PlanItem:
    """One address with its disposition and every deterministic inclusion reason."""

    address: Address
    disposition: str  # "read" | "review"
    reasons: tuple[InclusionReason, ...]


@dataclass(frozen=True)
class Completeness:
    """Independent completeness state per graph authority layer.

    Each field is one of `COMPLETE`, `BOUNDED` or `UNKNOWN`. No single boolean
    ever summarizes all three: an unresolved layer is never hidden behind
    another layer's success.
    """

    authored: str
    observed: str
    generated: str


@dataclass(frozen=True)
class ChangePlan:
    """An immutable, explainable read-only change plan for one query."""

    address: Address
    reverse: bool
    transitive: bool
    items: tuple[PlanItem, ...]
    boundaries: tuple[Boundary, ...]
    completeness: Completeness


@dataclass(frozen=True)
class DeliveryReviewItem:
    """One delivery owner or evidence address, always review-only."""

    address: Address
    role: str
    disposition: str
    owner_id: str
    owner_status: str | None
    delivery_disposition: str


@dataclass(frozen=True)
class DeliveryReview:
    """Task-sized delivery evidence attached to an exact-contract plan."""

    contract: Address
    configured: bool
    state: str
    items: tuple[DeliveryReviewItem, ...]


def build_delivery_review(
    contract: Address, report: DeliveryReport
) -> DeliveryReview:
    """Translate a valid targeted report into immutable review-only evidence."""

    items: list[DeliveryReviewItem] = []
    for mapping in report.mappings:
        items.extend(
            (
                DeliveryReviewItem(
                    address=Address(mapping.owner_id),
                    role="owner",
                    disposition=REVIEW,
                    owner_id=mapping.owner_id,
                    owner_status=mapping.owner_status,
                    delivery_disposition=mapping.disposition,
                ),
                DeliveryReviewItem(
                    address=Address(*mapping.evidence.split("#", 1)),
                    role="evidence",
                    disposition=REVIEW,
                    owner_id=mapping.owner_id,
                    owner_status=mapping.owner_status,
                    delivery_disposition=mapping.disposition,
                ),
            )
        )
    role_order = {"owner": 0, "evidence": 1}
    ordered = tuple(
        sorted(
            items,
            key=lambda item: (
                item.owner_id,
                role_order[item.role],
                item.address.text,
            ),
        )
    )
    if not report.configured:
        state = "unconfigured"
    elif report.overlaps:
        state = "overlap"
    elif report.mappings:
        state = "owned"
    else:
        state = "unowned"
    return DeliveryReview(contract, report.configured, state, ordered)


def _reason_sort_key(reason: InclusionReason) -> tuple[object, ...]:
    return (
        reason.scope,
        reason.relation,
        reason.authority,
        reason.origin,
        reason.distance,
        reason.detail or "",
        tuple(step.text for step in reason.path),
    )


def _group_reasons(
    results: tuple[TraversalResult, ...], scope: str
) -> dict[Address, list[InclusionReason]]:
    grouped: dict[Address, list[InclusionReason]] = {}
    for result in results:
        if result.authority == GENERATED:
            # Generated containment remains in traversal paths so section-owned
            # references are discoverable, but it is never promoted to a plan
            # item or read/review disposition.
            continue
        grouped.setdefault(result.address, []).append(
            InclusionReason(
                scope=scope,
                relation=result.relation,
                authority=result.authority,
                origin=result.origin,
                distance=result.distance,
                direct=result.direct,
                path=result.path.addresses,
                detail=result.reason,
            )
        )
    return grouped


def _disposition(reasons: tuple[InclusionReason, ...]) -> str:
    is_read = any(
        reason.scope == FORWARD_SCOPE
        and reason.relation == _READ_RELATION
        and reason.authority == AUTHORED
        for reason in reasons
    )
    return READ if is_read else REVIEW


def build_change_plan(
    address: Address,
    *,
    reverse: bool,
    transitive: bool,
    forward_reasons: tuple[TraversalResult, ...],
    reverse_reasons: tuple[TraversalResult, ...] = (),
    boundaries: tuple[Boundary, ...] = (),
) -> ChangePlan:
    """Assemble an immutable change plan from already-computed traversal reasons.

    `forward_reasons`/`reverse_reasons` must come from `graph.traverse_reasons`.
    Generated containment may participate in a transitive proving path so a
    document query can discover section-owned references, but `_group_reasons`
    filters generated results from plan items. Unlike `graph.traverse`,
    `traverse_reasons` keeps every
    distinct edge signature reaching an address instead of one deterministic
    edge, so alternate authored/observed evidence is never discarded merely
    because BFS found one address first.
    """

    grouped = _group_reasons(forward_reasons, FORWARD_SCOPE)
    for other_address, reasons in _group_reasons(reverse_reasons, REVERSE_SCOPE).items():
        grouped.setdefault(other_address, []).extend(reasons)
    grouped.pop(address, None)

    target_item = PlanItem(
        address=address,
        disposition=READ,
        reasons=(
            InclusionReason(
                scope=TARGET_SCOPE,
                relation="target",
                authority="target",
                origin="target",
                distance=0,
                direct=True,
                path=(address,),
                detail=None,
            ),
        ),
    )
    other_items = tuple(
        sorted(
            (
                PlanItem(
                    address=other_address,
                    disposition=_disposition(tuple(reasons)),
                    reasons=tuple(sorted(reasons, key=_reason_sort_key)),
                )
                for other_address, reasons in grouped.items()
            ),
            key=lambda item: item.address.text,
        )
    )
    items = (target_item, *other_items)

    boundaries_sorted = tuple(
        sorted(
            boundaries,
            key=lambda boundary: (
                boundary.source.text,
                boundary.category,
                boundary.raw_target,
            ),
        )
    )
    observed = UNKNOWN if reverse else (BOUNDED if boundaries_sorted else COMPLETE)
    # Generated containment participates in proving paths but is deliberately
    # omitted from plan items. This is not an unresolved boundary, so report a
    # distinct state instead of overloading `bounded`.
    completeness = Completeness(
        authored=COMPLETE,
        observed=observed,
        generated=NOT_ENUMERATED,
    )
    return ChangePlan(
        address=address,
        reverse=reverse,
        transitive=transitive,
        items=items,
        boundaries=boundaries_sorted,
        completeness=completeness,
    )
