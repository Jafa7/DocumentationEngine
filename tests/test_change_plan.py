from docsystem.change_plan import (
    BOUNDED,
    COMPLETE,
    NOT_ENUMERATED,
    READ,
    REVIEW,
    UNKNOWN,
    build_change_plan,
)
from docsystem.graph import (
    AUTHORED,
    GENERATED,
    OBSERVED,
    Address,
    Boundary,
    TraversalPath,
    TraversalResult,
)


def _result(
    address: Address,
    *,
    relation: str,
    authority: str,
    origin: str = "metadata",
    distance: int = 1,
    path: tuple[Address, ...] | None = None,
    reason: str | None = None,
) -> TraversalResult:
    return TraversalResult(
        address=address,
        relation=relation,
        authority=authority,
        origin=origin,
        distance=distance,
        direct=distance == 1,
        path=TraversalPath(path or (Address("DOC-001"), address)),
        reason=reason,
    )


def test_target_is_a_read_item_at_distance_zero() -> None:
    plan = build_change_plan(
        Address("DOC-001"), reverse=False, transitive=False, forward_reasons=()
    )
    assert len(plan.items) == 1
    target = plan.items[0]
    assert target.address == Address("DOC-001")
    assert target.disposition == READ
    assert len(target.reasons) == 1
    assert target.reasons[0].distance == 0
    assert target.reasons[0].scope == "target"


def test_authored_depends_on_is_read() -> None:
    plan = build_change_plan(
        Address("DOC-001"),
        reverse=False,
        transitive=False,
        forward_reasons=(
            _result(Address("DOC-002"), relation="depends_on", authority=AUTHORED),
        ),
    )
    item = next(item for item in plan.items if item.address == Address("DOC-002"))
    assert item.disposition == READ


def test_related_is_never_promoted_to_read() -> None:
    plan = build_change_plan(
        Address("DOC-001"),
        reverse=False,
        transitive=False,
        forward_reasons=(
            _result(Address("DOC-004"), relation="related", authority=AUTHORED),
        ),
    )
    item = next(item for item in plan.items if item.address == Address("DOC-004"))
    assert item.disposition == REVIEW


def test_observed_forward_reference_is_review() -> None:
    plan = build_change_plan(
        Address("DOC-001"),
        reverse=False,
        transitive=False,
        forward_reasons=(
            _result(
                Address("DOC-002", "setup"),
                relation="references",
                authority=OBSERVED,
                origin="markdown-link",
            ),
        ),
    )
    item = next(item for item in plan.items if item.address == Address("DOC-002", "setup"))
    assert item.disposition == REVIEW


def test_reverse_depends_on_impact_is_review_not_read() -> None:
    """`--reverse` adds incoming impact/review scope: a dependent never becomes read."""

    plan = build_change_plan(
        Address("DOC-002"),
        reverse=True,
        transitive=False,
        forward_reasons=(),
        reverse_reasons=(
            _result(
                Address("DOC-001"),
                relation="depends_on",
                authority=AUTHORED,
                path=(Address("DOC-002"), Address("DOC-001")),
            ),
        ),
    )
    item = next(item for item in plan.items if item.address == Address("DOC-001"))
    assert item.disposition == REVIEW
    assert item.reasons[0].scope == "reverse"


def test_multiple_reasons_for_one_address_aggregate_without_loss() -> None:
    plan = build_change_plan(
        Address("DOC-001"),
        reverse=False,
        transitive=False,
        forward_reasons=(
            _result(Address("DOC-002"), relation="depends_on", authority=AUTHORED),
            _result(
                Address("DOC-002"),
                relation="references",
                authority=OBSERVED,
                origin="markdown-link",
                reason="line 7",
            ),
        ),
    )
    item = next(item for item in plan.items if item.address == Address("DOC-002"))
    assert item.disposition == READ  # depends_on reason alone is enough.
    assert len(item.reasons) == 2
    signatures = {(reason.relation, reason.authority) for reason in item.reasons}
    assert signatures == {("depends_on", "authored"), ("references", "observed")}
    # Deterministic order: repeated construction sorts identically.
    plan_again = build_change_plan(
        Address("DOC-001"),
        reverse=False,
        transitive=False,
        forward_reasons=(
            _result(
                Address("DOC-002"),
                relation="references",
                authority=OBSERVED,
                origin="markdown-link",
                reason="line 7",
            ),
            _result(Address("DOC-002"), relation="depends_on", authority=AUTHORED),
        ),
    )
    item_again = next(item for item in plan_again.items if item.address == Address("DOC-002"))
    assert item.reasons == item_again.reasons


def test_generated_containment_never_appears_as_a_reason() -> None:
    """Generated containment may prove a path but never becomes a plan item."""

    plan = build_change_plan(
        Address("DOC-001"),
        reverse=False,
        transitive=False,
        forward_reasons=(
            _result(Address("DOC-001", "intro"), relation="contains", authority=GENERATED,
                     origin="section-parser"),
        ),
    )
    assert [item.address for item in plan.items] == [Address("DOC-001")]


def test_completeness_layers_are_independent() -> None:
    boundary = Boundary(Address("DOC-001"), "../missing.md", "unknown-document", "not cataloged")

    forward_only = build_change_plan(
        Address("DOC-001"), reverse=False, transitive=False, forward_reasons=(), boundaries=()
    )
    assert forward_only.completeness.authored == COMPLETE
    assert forward_only.completeness.observed == COMPLETE
    assert forward_only.completeness.generated == NOT_ENUMERATED

    with_boundary = build_change_plan(
        Address("DOC-001"),
        reverse=False,
        transitive=False,
        forward_reasons=(),
        boundaries=(boundary,),
    )
    assert with_boundary.completeness.observed == BOUNDED
    assert with_boundary.boundaries == (boundary,)

    reverse_plan = build_change_plan(
        Address("DOC-001"), reverse=True, transitive=False, forward_reasons=()
    )
    assert reverse_plan.completeness.observed == UNKNOWN


def test_direct_and_transitive_reasons_keep_their_own_proving_path() -> None:
    plan = build_change_plan(
        Address("DOC-001"),
        reverse=False,
        transitive=True,
        forward_reasons=(
            _result(
                Address("DOC-002"),
                relation="depends_on",
                authority=AUTHORED,
                distance=1,
                path=(Address("DOC-001"), Address("DOC-002")),
            ),
            _result(
                Address("DOC-003"),
                relation="depends_on",
                authority=AUTHORED,
                distance=2,
                path=(Address("DOC-001"), Address("DOC-002"), Address("DOC-003")),
            ),
        ),
    )
    direct_item = next(item for item in plan.items if item.address == Address("DOC-002"))
    transitive_item = next(item for item in plan.items if item.address == Address("DOC-003"))
    assert direct_item.reasons[0].direct is True
    assert direct_item.reasons[0].distance == 1
    assert transitive_item.reasons[0].direct is False
    assert transitive_item.reasons[0].distance == 2
    assert [step.text for step in transitive_item.reasons[0].path] == [
        "DOC-001",
        "DOC-002",
        "DOC-003",
    ]


def test_items_are_sorted_with_target_first_then_address_order() -> None:
    plan = build_change_plan(
        Address("DOC-001"),
        reverse=False,
        transitive=False,
        forward_reasons=(
            _result(Address("DOC-003"), relation="depends_on", authority=AUTHORED),
            _result(Address("DOC-002"), relation="depends_on", authority=AUTHORED),
        ),
    )
    assert [item.address.text for item in plan.items] == ["DOC-001", "DOC-002", "DOC-003"]
