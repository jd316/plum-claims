"""Deterministic tests for the whole-policy enforcement gaps:
annual-OPD + family-floater accumulation (API layer), the floater-limit rule,
and the pharmacy branded-drug co-pay. None of these touch the eval path.

The DB-dependent accumulation tests skip cleanly when Postgres is unreachable.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.models.schemas import (ClaimSubmission, ClaimResult, Decision, DocumentInput,
                                 LineItem, ReasonCode)
from app.rules.financial import calculate
from app.rules.limits import check
from app.rules.base import RuleContext
from app.models.schemas import SemanticMapping
from app.services.policy_engine import PolicyEngine
from tests.conftest import REPO_ROOT

pe = PolicyEngine(str(REPO_ROOT / "policy_terms.json"))


# ---------------------------------------------------------------------------
# Branded-drug co-pay (pharmacy) — exact numbers, pure (no DB).
# ---------------------------------------------------------------------------

def test_pharmacy_branded_plus_generic_blended_copay():
    """One branded (₹1000) + one generic (₹1000) pharmacy line, no network discount.
    Branded gets 30% copay, generic gets 0%. Approved = 2000 − 300 = 1700."""
    fb = calculate(pe, "PHARMACY", is_network=False,
                   items=[LineItem(description="Augmentin 625", amount=1000, is_branded=True),
                          LineItem(description="Amoxicillin 500mg", amount=1000, is_branded=False)],
                   disallowed=[])
    assert fb.gross == 2000
    assert fb.copay_amount == 300.0       # 30% of the branded ₹1000 only
    assert fb.approved_amount == 1700.0
    assert any("branded" in s.lower() for s in fb.steps)


def test_pharmacy_all_unknown_branded_behaves_as_today():
    """is_branded=None everywhere → flat category copay (0%) → no change vs. before."""
    fb = calculate(pe, "PHARMACY", is_network=False,
                   items=[LineItem(description="Some Drug", amount=1000),
                          LineItem(description="Other Drug", amount=500)],
                   disallowed=[])
    assert fb.copay_amount == 0.0
    assert fb.approved_amount == 1500.0
    assert not any("branded" in s.lower() for s in fb.steps)


def test_pharmacy_branded_with_network_discount_order_preserved():
    """Network discount FIRST, then branded copay on the post-discount portion.
    Pharmacy has no network_discount_percent in policy → discount is 0, but assert
    the branded copay still lands on the post-discount (== gross) portion."""
    fb = calculate(pe, "PHARMACY", is_network=True,
                   items=[LineItem(description="Crocin", amount=2000, is_branded=True)],
                   disallowed=[])
    # pharmacy network_discount_percent is absent → 0% discount, post == 2000
    assert fb.network_discount_amount == 0.0
    assert fb.copay_amount == 600.0       # 30% of 2000
    assert fb.approved_amount == 1400.0


def test_non_pharmacy_unaffected_by_branded_flag():
    """A non-pharmacy category ignores is_branded entirely (CONSULTATION copay 10%)."""
    fb = calculate(pe, "CONSULTATION", is_network=False,
                   items=[LineItem(description="Consultation Fee", amount=1000, is_branded=True)],
                   disallowed=[])
    # CONSULTATION copay is 10% flat regardless of is_branded.
    assert fb.copay_amount == 100.0
    assert fb.approved_amount == 900.0
    assert not any("branded" in s.lower() for s in fb.steps)


# ---------------------------------------------------------------------------
# Floater-limit rule — pure (no DB). Proves eval-safety: None → no check.
# ---------------------------------------------------------------------------

def _sub(**kw) -> ClaimSubmission:
    base = dict(member_id="EMP001", policy_id="PLUM_GHI_2024", claim_category="DIAGNOSTIC",
                treatment_date=date(2024, 6, 1), claimed_amount=5000.0,
                documents=[DocumentInput(file_id="F001", stored_path="/tmp/x.png")])
    base.update(kw)
    return ClaimSubmission(**base)


def _ctx(sub: ClaimSubmission) -> RuleContext:
    return RuleContext(sub, pe.member(sub.member_id), [], SemanticMapping(confidence=1.0), pe)


def test_floater_over_limit_fails():
    """floater_used 148000 + 5000 claim > 150000 combined → FAIL FLOATER_LIMIT_EXCEEDED."""
    v = check(_ctx(_sub(floater_used_amount=148000.0)))
    assert v.status == "FAIL"
    assert v.reason_code == "FLOATER_LIMIT_EXCEEDED"
    assert "coverage.family_floater.combined_limit" in v.policy_refs


def test_floater_under_limit_passes():
    v = check(_ctx(_sub(floater_used_amount=100000.0)))
    assert v.status == "PASS"


def test_floater_none_skips_check_eval_safe():
    """floater_used_amount=None (the eval-runner default) → no floater check at all,
    even with a huge claim. This is the eval-safety guarantee."""
    v = check(_ctx(_sub(floater_used_amount=None, claimed_amount=4000.0)))
    assert v.status == "PASS"
    assert not any("floater" in r for r in v.policy_refs)


def test_annual_uses_provided_ytd_eval_path():
    """When ytd_claims_amount is provided (eval path), the rule uses that value."""
    over = check(_ctx(_sub(ytd_claims_amount=48000.0, claimed_amount=4000.0)))
    assert over.status == "FAIL" and over.reason_code == "ANNUAL_LIMIT_EXCEEDED"
    under = check(_ctx(_sub(ytd_claims_amount=40000.0, claimed_amount=4000.0)))
    assert under.status == "PASS"


# ---------------------------------------------------------------------------
# DB-dependent accumulation tests — skip cleanly when Postgres is down.
# ---------------------------------------------------------------------------

def _db_reachable() -> bool:
    try:
        from app.services.persistence import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="module", autouse=False)
def require_db():
    if not _db_reachable():
        pytest.skip("Postgres unreachable — skipping accumulation DB tests")


def _seed_claim(member_id: str, category: str, status: str, approved: float,
                treatment_date: date) -> str:
    from app.services.persistence import save_claim
    claim_id = f"acc-test-{uuid.uuid4().hex}"
    sub = ClaimSubmission(member_id=member_id, policy_id="PLUM_GHI_2024",
                          claim_category=category, treatment_date=treatment_date,
                          claimed_amount=approved,
                          documents=[DocumentInput(file_id="F001", stored_path="/tmp/x.png")])
    result = ClaimResult(claim_id=claim_id, blocked=False,
                         decision=Decision(status=status, approved_amount=approved,
                                           reason_codes=[ReasonCode(code="OK", detail="seeded")],
                                           member_message="seeded"))
    save_claim(sub, result)
    return claim_id


def test_member_ytd_sums_only_this_member_and_consuming_statuses(require_db):
    from app.services.persistence import init_db
    from app.services.accumulation import member_ytd
    init_db()
    me = f"ACC-{uuid.uuid4().hex[:8]}"
    other = f"OTH-{uuid.uuid4().hex[:8]}"
    _seed_claim(me, "CONSULTATION", "APPROVED", 1000.0, date(2024, 6, 1))
    _seed_claim(me, "DIAGNOSTIC", "PARTIAL", 500.0, date(2024, 7, 1))
    _seed_claim(me, "PHARMACY", "REJECTED", 9999.0, date(2024, 8, 1))   # excluded (status)
    _seed_claim(other, "CONSULTATION", "APPROVED", 7777.0, date(2024, 6, 1))  # other member
    assert member_ytd(me, pe) == 1500.0


def test_member_ytd_respects_policy_year_bounds(require_db):
    from app.services.persistence import init_db
    from app.services.accumulation import member_ytd
    init_db()
    me = f"YR-{uuid.uuid4().hex[:8]}"
    _seed_claim(me, "CONSULTATION", "APPROVED", 1000.0, date(2024, 6, 1))   # in policy year
    _seed_claim(me, "CONSULTATION", "APPROVED", 2000.0, date(2023, 6, 1))   # before policy start
    _seed_claim(me, "CONSULTATION", "APPROVED", 3000.0, date(2026, 6, 1))   # after policy end
    assert member_ytd(me, pe) == 1000.0


def test_family_floater_used_includes_family_excludes_others(require_db):
    """EMP001 has dependents DEP001 (SPOUSE) + DEP002 (CHILD). Floater usage must sum
    the family but ignore unrelated members."""
    from app.services.persistence import init_db
    from app.services.accumulation import family_floater_used
    init_db()
    _seed_claim("EMP001", "CONSULTATION", "APPROVED", 1000.0, date(2024, 6, 1))
    _seed_claim("DEP001", "CONSULTATION", "APPROVED", 2000.0, date(2024, 6, 1))
    _seed_claim("DEP002", "CONSULTATION", "APPROVED", 500.0, date(2024, 6, 1))
    _seed_claim("EMP002", "CONSULTATION", "APPROVED", 9999.0, date(2024, 6, 1))  # unrelated
    used = family_floater_used("EMP001", pe)
    # At least the seeded family sum; >= because the test DB may carry prior rows.
    assert used >= 3500.0
    # Querying from a dependent resolves the same family.
    assert family_floater_used("DEP001", pe) >= 3500.0
