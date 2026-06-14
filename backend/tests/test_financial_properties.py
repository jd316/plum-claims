"""Property-based tests for the financial calculator (app.rules.financial.calculate).

Invariants that must hold for ALL valid inputs — driven by Hypothesis. Any failure
here means the production calculator has violated a documented policy invariant.

Run:
    cd backend && .venv/bin/pytest tests/test_financial_properties.py -v
"""
from __future__ import annotations

import math

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from app.models.schemas import LineItem
from app.rules.financial import calculate
from app.services.money import D, to_float
from app.services.policy_engine import PolicyEngine
from tests.conftest import REPO_ROOT

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PE = PolicyEngine(str(REPO_ROOT / "policy_terms.json"))

# All valid claim categories and their policy keys
ALL_CATEGORIES = ["CONSULTATION", "DIAGNOSTIC", "PHARMACY", "DENTAL", "VISION", "ALTERNATIVE_MEDICINE"]

# Strategies
# Amounts: positive, at most 1e6, 2-dp rounded values
amount_st = st.floats(min_value=1.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False).map(lambda x: round(x, 2))

# Descriptions: non-empty ASCII strings that are NOT any known disallowed procedure
description_st = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs")),
    min_size=1, max_size=80,
).filter(lambda s: s.strip())

category_st = st.sampled_from(ALL_CATEGORIES)
is_network_st = st.booleans()


def line_item_st(desc: str | None = None) -> st.SearchStrategy:
    """Strategy producing a single LineItem with a controlled description."""
    d = st.just(desc) if desc else description_st
    return st.builds(LineItem, description=d, amount=amount_st)


def items_st(min_n: int = 1, max_n: int = 10) -> st.SearchStrategy:
    """Strategy producing a list of LineItems (all descriptions distinct)."""
    return st.lists(
        st.builds(LineItem, description=description_st, amount=amount_st),
        min_size=min_n, max_size=max_n,
    )


# ---------------------------------------------------------------------------
# Money-field helpers
# ---------------------------------------------------------------------------

def is_valid_money(v: float) -> bool:
    """A money value must be finite, non-negative, and rounded to 2 dp."""
    return math.isfinite(v) and v >= 0.0 and round(v, 2) == v


# ---------------------------------------------------------------------------
# Invariant 1: All money fields are finite, non-negative, 2-dp rounded
# ---------------------------------------------------------------------------

@given(
    items=items_st(1, 8),
    category=category_st,
    is_network=is_network_st,
)
@settings(max_examples=300, deadline=None)
def test_money_fields_finite_nonneg_2dp(items, category, is_network):
    """Every monetary output must be finite, non-negative, and at most 2 decimal places."""
    fb = calculate(PE, category, is_network, items, disallowed=[])
    for field_name, val in [
        ("gross", fb.gross),
        ("network_discount_amount", fb.network_discount_amount),
        ("post_discount", fb.post_discount),
        ("copay_amount", fb.copay_amount),
        ("approved_amount", fb.approved_amount),
    ]:
        assert math.isfinite(val), f"{field_name}={val} is not finite"
        assert val >= 0.0, f"{field_name}={val} is negative"
        assert round(val, 2) == val, f"{field_name}={val} not rounded to 2 dp"


# ---------------------------------------------------------------------------
# Invariant 2: approved_amount is never greater than gross (never pay more
#              than billed) and is never negative.
# ---------------------------------------------------------------------------

@given(
    items=items_st(1, 8),
    category=category_st,
    is_network=is_network_st,
)
@settings(max_examples=300, deadline=None)
def test_approved_amount_within_zero_to_gross(items, category, is_network):
    """0 <= approved_amount <= gross for all inputs."""
    fb = calculate(PE, category, is_network, items, disallowed=[])
    assert fb.approved_amount >= 0.0, f"approved_amount={fb.approved_amount} is negative"
    assert fb.approved_amount <= fb.gross + 0.01, (
        f"approved_amount={fb.approved_amount} > gross={fb.gross}"
    )


# ---------------------------------------------------------------------------
# Invariant 3: gross equals sum of APPROVED line-item amounts
#              (disallowed items are excluded from gross)
# ---------------------------------------------------------------------------

@given(
    items=items_st(2, 8),
    is_network=is_network_st,
)
@settings(max_examples=300, deadline=None)
def test_gross_equals_approved_line_items_sum(items, is_network):
    """gross == sum of amounts for line items not in disallowed."""
    # Use CONSULTATION (no excluded_procedures in policy) so coverage_exclusion
    # does not silently disallow items that happen to match dental/vision exclusions.
    half = len(items) // 2
    disallowed_descs = [it.description for it in items[:half]]
    fb = calculate(PE, "CONSULTATION", is_network, items, disallowed=disallowed_descs)
    expected_gross = sum(
        it.amount for it in items if it.description.lower() not in {d.lower() for d in disallowed_descs}
    )
    assert abs(fb.gross - expected_gross) < 0.005, (
        f"gross={fb.gross} but approved items sum to {expected_gross}"
    )


@given(items=items_st(1, 8), is_network=is_network_st)
@settings(max_examples=200, deadline=None)
def test_gross_equals_sum_no_disallowed(items, is_network):
    """With no disallowed items, gross == sum of all line-item amounts."""
    fb = calculate(PE, "CONSULTATION", is_network, items, disallowed=[])
    expected = sum(it.amount for it in items)
    assert abs(fb.gross - expected) < 0.005, f"gross={fb.gross} expected={expected}"


# ---------------------------------------------------------------------------
# Invariant 4: Order invariant — network discount applied to gross FIRST,
#              co-pay applied to the post-discount amount SECOND.
#
#   post_discount  == round(gross * (1 - disc_pct/100), 2)
#   copay_amount   == round(post_discount * copay_pct/100, 2)
#   approved       == post_discount - copay_amount  (before sub-limit cap)
# ---------------------------------------------------------------------------

@given(items=items_st(1, 6), category=category_st, is_network=is_network_st)
@settings(max_examples=400, deadline=None)
def test_order_invariant_discount_then_copay(items, category, is_network):
    """Discount is applied to gross first, copay to the post-discount result.

    The production code implements a two-step rounding approach (Decimal,
    ROUND_HALF_UP, quantized to 2 dp via app.services.money):
      disc_amt  = money(gross * disc_pct / 100)
      post      = money(gross - disc_amt)
      copay_amt = money(post  * copay_pct / 100)

    We verify structural correctness using these same steps (not a merged single-
    step formula, which would produce a different rounding for borderline values).
    The ORDER invariant is: discount is computed from gross, copay from post-discount.
    """
    fb = calculate(PE, category, is_network, items, disallowed=[])
    cat = PE.category_rules(category)
    disc_pct = float(cat.get("network_discount_percent", 0)) if is_network else 0.0
    copay_pct = float(cat.get("copay_percent", 0))

    # Replicate the production arithmetic exactly (same Decimal ROUND_HALF_UP path).
    expected_disc_amt = to_float(D(fb.gross) * D(disc_pct) / 100)
    expected_post = to_float(D(fb.gross) - D(expected_disc_amt))
    expected_copay = to_float(D(expected_post) * D(copay_pct) / 100)

    assert fb.network_discount_amount == expected_disc_amt, (
        f"disc_amt={fb.network_discount_amount} expected={expected_disc_amt}"
    )
    assert fb.post_discount == expected_post, (
        f"post_discount={fb.post_discount} expected={expected_post} "
        f"(gross={fb.gross}, disc_pct={disc_pct})"
    )
    assert fb.copay_amount == expected_copay, (
        f"copay_amount={fb.copay_amount} expected={expected_copay} "
        f"(post_discount={fb.post_discount}, copay_pct={copay_pct})"
    )
    # approved = post_discount - copay_amount, unless capped by sub-limit
    pre_cap = to_float(D(fb.post_discount) - D(fb.copay_amount))
    sub_limit = cat.get("sub_limit")
    if category != "CONSULTATION" and sub_limit is not None and pre_cap > float(sub_limit):
        # Sub-limit cap should apply — approved must equal the sub-limit
        assert abs(fb.approved_amount - float(sub_limit)) < 0.005, (
            f"approved_amount={fb.approved_amount} should be capped at {sub_limit}"
        )
    else:
        # No cap — approved must equal post_discount - copay_amount
        assert abs(fb.approved_amount - pre_cap) < 0.005, (
            f"approved_amount={fb.approved_amount} expected pre-cap value {pre_cap}"
        )


# ---------------------------------------------------------------------------
# Invariant 5: Sub-limit cap — for non-CONSULTATION categories, approved_amount
#              must never exceed the category sub_limit.
# ---------------------------------------------------------------------------

@given(items=items_st(1, 8), is_network=is_network_st)
@settings(max_examples=200, deadline=None)
def test_sub_limit_cap_non_consultation(items, is_network):
    """For non-CONSULTATION categories, approved_amount <= category sub_limit."""
    for category in ["DIAGNOSTIC", "PHARMACY", "DENTAL", "VISION", "ALTERNATIVE_MEDICINE"]:
        sub_limit = float(PE.category_rules(category)["sub_limit"])
        fb = calculate(PE, category, is_network, items, disallowed=[])
        assert fb.approved_amount <= sub_limit + 0.005, (
            f"{category}: approved_amount={fb.approved_amount} > sub_limit={sub_limit}"
        )


@given(items=items_st(1, 4), is_network=is_network_st)
@settings(max_examples=150, deadline=None)
def test_consultation_not_capped_by_sub_limit(items, is_network):
    """CONSULTATION is explicitly NOT capped by its sub_limit in the financial calculator
    (the limits *rule* enforces the per_claim_limit; the financial calc skips the cap)."""
    fb = calculate(PE, "CONSULTATION", is_network, items, disallowed=[])
    # Just verify the calculation runs; the financial calc doesn't cap CONSULTATION.
    # The approved amount equals post_discount - copay_amount (no sub-limit application).
    cat = PE.category_rules("CONSULTATION")
    disc_pct = float(cat.get("network_discount_percent", 0)) if is_network else 0.0
    copay_pct = float(cat.get("copay_percent", 0))
    # Mirror the engine's two-step Decimal ROUND_HALF_UP path exactly.
    expected_disc = to_float(D(fb.gross) * D(disc_pct) / 100)
    expected_post = to_float(D(fb.gross) - D(expected_disc))
    expected_copay = to_float(D(expected_post) * D(copay_pct) / 100)
    expected_approved = to_float(D(expected_post) - D(expected_copay))
    assert abs(fb.approved_amount - expected_approved) < 0.005, (
        f"CONSULTATION approved_amount={fb.approved_amount} expected={expected_approved}"
    )


# ---------------------------------------------------------------------------
# Invariant 6: Network monotonicity — discount amount is 0 when not network,
#              and positive when network AND the category has a discount %.
# ---------------------------------------------------------------------------

@given(items=items_st(1, 6), category=category_st)
@settings(max_examples=300, deadline=None)
def test_network_discount_direction(items, category):
    """non-network: discount_amount == 0; network + disc%>0: discount_amount > 0."""
    cat = PE.category_rules(category)
    disc_pct = float(cat.get("network_discount_percent", 0))

    fb_non = calculate(PE, category, False, items, disallowed=[])
    assert fb_non.network_discount_amount == 0.0, (
        f"Non-network discount should be 0, got {fb_non.network_discount_amount}"
    )
    assert fb_non.network_discount_pct == 0.0

    fb_net = calculate(PE, category, True, items, disallowed=[])
    if disc_pct > 0 and fb_net.gross > 0:
        assert fb_net.network_discount_amount > 0.0, (
            f"Network discount should be >0 for {category} (disc%={disc_pct}), "
            f"got {fb_net.network_discount_amount}"
        )


@given(items=items_st(1, 6), category=category_st)
@settings(max_examples=300, deadline=None)
def test_network_approved_le_non_network(items, category):
    """A network claim (with a discount) never yields a HIGHER approved amount
    than the equivalent non-network claim, for categories with a discount percent."""
    cat = PE.category_rules(category)
    disc_pct = float(cat.get("network_discount_percent", 0))
    assume(disc_pct > 0)  # only meaningful when the discount applies

    fb_net = calculate(PE, category, True, items, disallowed=[])
    fb_non = calculate(PE, category, False, items, disallowed=[])

    # Network discount reduces the gross before copay, so post_discount_net <= post_discount_non.
    assert fb_net.post_discount <= fb_non.post_discount + 0.005, (
        f"{category}: network post_discount={fb_net.post_discount} > "
        f"non-network post_discount={fb_non.post_discount}"
    )


# ---------------------------------------------------------------------------
# Invariant 7: Monotonic in gross — adding a covered item never decreases
#              approved_amount (all else equal, more covered items -> more approved).
# ---------------------------------------------------------------------------

@given(
    base_items=items_st(1, 5),
    extra=st.builds(LineItem, description=st.just("Extra Covered Service"), amount=amount_st),
    category=category_st,
    is_network=is_network_st,
)
@settings(max_examples=300, deadline=None)
def test_monotonic_adding_covered_item(base_items, extra, category, is_network):
    """Adding a covered line item never decreases approved_amount."""
    fb_base = calculate(PE, category, is_network, base_items, disallowed=[])
    fb_extra = calculate(PE, category, is_network, base_items + [extra], disallowed=[])
    # The extra item increases gross; approved can only stay same (sub-limit already hit)
    # or increase. Sub-limit clamps but never reduces below the original.
    assert fb_extra.approved_amount >= fb_base.approved_amount - 0.005, (
        f"Adding a covered item reduced approved_amount from "
        f"{fb_base.approved_amount} to {fb_extra.approved_amount}"
    )


# ---------------------------------------------------------------------------
# Invariant 8: Disallowed items don't count toward gross
# ---------------------------------------------------------------------------

@given(
    covered_items=items_st(1, 5),
    extra_amount=amount_st,
    category=category_st,
    is_network=is_network_st,
)
@settings(max_examples=200, deadline=None)
def test_disallowed_items_excluded_from_gross(covered_items, extra_amount, category, is_network):
    """An item named in `disallowed` is not counted in gross."""
    EXCLUDED_DESC = "Definitely_Excluded_Item_XYZ"
    extra = LineItem(description=EXCLUDED_DESC, amount=extra_amount)
    all_items = covered_items + [extra]

    fb_with = calculate(PE, category, is_network, all_items, disallowed=[EXCLUDED_DESC])
    fb_without = calculate(PE, category, is_network, covered_items, disallowed=[])

    # Gross should be the same (excluded item not counted)
    assert abs(fb_with.gross - fb_without.gross) < 0.005, (
        f"Disallowed item still affects gross: "
        f"fb_with.gross={fb_with.gross} fb_without.gross={fb_without.gross}"
    )
    # The excluded item's LineItemDecision must be marked not approved
    excluded_decisions = [li for li in fb_with.line_items if li.description == EXCLUDED_DESC]
    assert excluded_decisions, "Excluded item not in line_items"
    assert not excluded_decisions[0].approved, "Excluded item should be marked approved=False"
    assert excluded_decisions[0].reason, "Excluded item must have a reason string"


# ---------------------------------------------------------------------------
# Invariant 9: Steps list is non-empty and always contains an approved-amount line
# ---------------------------------------------------------------------------

@given(items=items_st(1, 5), category=category_st, is_network=is_network_st)
@settings(max_examples=200, deadline=None)
def test_steps_contains_approved_amount(items, category, is_network):
    """The steps narrative always ends with an 'Approved amount' entry."""
    fb = calculate(PE, category, is_network, items, disallowed=[])
    assert fb.steps, "steps list must not be empty"
    assert any("approved amount" in s.lower() for s in fb.steps), (
        f"No 'Approved amount' step found in: {fb.steps}"
    )


# ---------------------------------------------------------------------------
# Invariant 10: line_items in the result correspond 1-to-1 with input items
# ---------------------------------------------------------------------------

@given(items=items_st(1, 8), category=category_st, is_network=is_network_st)
@settings(max_examples=200, deadline=None)
def test_line_items_count_matches_input(items, category, is_network):
    """Output line_items has exactly as many entries as input items."""
    fb = calculate(PE, category, is_network, items, disallowed=[])
    assert len(fb.line_items) == len(items), (
        f"Expected {len(items)} line item decisions, got {len(fb.line_items)}"
    )


# ---------------------------------------------------------------------------
# Invariant 11: post_discount == gross when not network (no discount applied)
# ---------------------------------------------------------------------------

@given(items=items_st(1, 6), category=category_st)
@settings(max_examples=200, deadline=None)
def test_post_discount_equals_gross_when_non_network(items, category):
    """When is_network=False, post_discount must equal gross (zero discount)."""
    fb = calculate(PE, category, False, items, disallowed=[])
    assert abs(fb.post_discount - fb.gross) < 0.005, (
        f"Non-network: post_discount={fb.post_discount} != gross={fb.gross}"
    )
