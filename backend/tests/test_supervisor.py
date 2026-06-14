"""Deterministic tests for the adaptive agentic supervisor (app/graph/supervisor.py).

Two things are proven here:
  1. select_rules applies ONLY the provably-safe skips (pre_auth on non-DIAGNOSTIC,
     waiting_period past the policy maximum) with the right human reasons, and never
     skips a rule that could fire.
  2. EQUIVALENCE: across the full synthetic.generate_cases() set, the decision produced
     with adaptive routing (only the invoked rules) is byte-identical (status + amount +
     reason codes) to the decision with all-rules routing. This PROVES the skips never
     change any decision.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.config import settings
from app.models.schemas import (ClaimSubmission, ExtractionResult, SemanticMapping,
                                LineItem, NumField, StrField)
from app.services.policy_engine import PolicyEngine
from app.graph.supervisor import select_rules, ALL_RULES, _policy_max_waiting
from app.rules import waiting_period, coverage_exclusion, pre_auth, limits, fraud
from app.rules.base import RuleContext
from app.rules.financial import calculate
from app.rules.aggregator import aggregate
from app.evalrunner.synthetic import generate_cases


@pytest.fixture(scope="module")
def pe() -> PolicyEngine:
    return PolicyEngine(settings.policy_path)


_RULE_MODULES = {"waiting_period": waiting_period, "coverage_exclusion": coverage_exclusion,
                 "pre_auth": pre_auth, "limits": limits, "fraud_anomaly": fraud}


def _member(pe: PolicyEngine, mid: str) -> dict:
    return pe.member(mid)


def _skipped_names(skipped: list[dict]) -> set[str]:
    return {s["rule"] for s in skipped}


def _reason_for(skipped: list[dict], rule: str) -> str:
    return next(s["reason"] for s in skipped if s["rule"] == rule)


def _submission(pe, member, category, tdate, claimed, hospital="Apollo Hospitals"):
    return ClaimSubmission(
        member_id=member["member_id"], policy_id="PLUM_GHI_2024",
        claim_category=category, treatment_date=tdate,
        claimed_amount=claimed, hospital_name=hospital, claims_history=[], documents=[])


# --------------------------------------------------------------------------- #
# select_rules — skip conditions                                              #
# --------------------------------------------------------------------------- #

def test_consultation_recent_member_skips_only_pre_auth(pe):
    """CONSULTATION, member enrolled < 730d -> invoke {waiting, coverage, limits, fraud},
    skip pre_auth (no pre-auth-gated tests for CONSULTATION)."""
    member = _member(pe, "EMP001")  # joined 2024-04-01
    sub = _submission(pe, member, "CONSULTATION", date(2024, 6, 1), 1800.0)
    invoked, skipped = select_rules(sub, member, pe)
    assert set(invoked) == {"waiting_period", "coverage_exclusion", "limits", "fraud_anomaly"}
    assert _skipped_names(skipped) == {"pre_auth"}
    reason = _reason_for(skipped, "pre_auth")
    assert "pre_auth" in reason and "CONSULTATION" in reason


def test_diagnostic_invokes_all_five(pe):
    """DIAGNOSTIC carries pre-auth config -> pre_auth IS applicable -> invoke all 5
    (member recent enough that waiting_period also applies)."""
    member = _member(pe, "EMP001")
    sub = _submission(pe, member, "DIAGNOSTIC", date(2024, 6, 1), 7500.0)
    invoked, skipped = select_rules(sub, member, pe)
    assert set(invoked) == set(ALL_RULES)
    assert skipped == []


def test_enrolled_past_max_nondiagnostic_skips_both(pe):
    """Member enrolled > 730 days, non-DIAGNOSTIC -> skip BOTH pre_auth and
    waiting_period; invoke the three always-run rules."""
    member = _member(pe, "EMP001")  # joined 2024-04-01
    # 2024-04-01 + 800 days is comfortably > 730-day policy max.
    tdate = date.fromisoformat(member["join_date"])
    tdate = tdate.replace(year=tdate.year + 3)  # ~3 years later
    sub = _submission(pe, member, "PHARMACY", tdate, 3000.0)
    invoked, skipped = select_rules(sub, member, pe)
    assert set(invoked) == {"coverage_exclusion", "limits", "fraud_anomaly"}
    assert _skipped_names(skipped) == {"pre_auth", "waiting_period"}
    wp_reason = _reason_for(skipped, "waiting_period")
    assert "730" in wp_reason and "enrolled" in wp_reason


def test_diabetes_within_90_days_invokes_waiting_period(pe):
    """EMP005 (joined 2024-09-01), diabetes treatment 2024-10-15 (day 44 < 730):
    waiting_period MUST be invoked — it could FAIL (diabetes has a 90-day wait)."""
    member = _member(pe, "EMP005")
    sub = _submission(pe, member, "CONSULTATION", date(2024, 10, 15), 3000.0)
    invoked, skipped = select_rules(sub, member, pe)
    assert "waiting_period" in invoked
    assert "waiting_period" not in _skipped_names(skipped)


def test_boundary_just_past_max_skips_waiting(pe):
    """Strictly > policy_max days -> waiting_period skippable; exactly at max -> not."""
    policy_max = _policy_max_waiting(pe)
    member = _member(pe, "EMP001")
    join = date.fromisoformat(member["join_date"])
    from datetime import timedelta
    # exactly policy_max days since join -> NOT skipped (we use strict >)
    sub_at = _submission(pe, member, "PHARMACY", join + timedelta(days=policy_max), 3000.0)
    _, skipped_at = select_rules(sub_at, member, pe)
    assert "waiting_period" not in _skipped_names(skipped_at)
    # policy_max + 1 days -> skipped
    sub_over = _submission(pe, member, "PHARMACY", join + timedelta(days=policy_max + 1), 3000.0)
    _, skipped_over = select_rules(sub_over, member, pe)
    assert "waiting_period" in _skipped_names(skipped_over)


# --------------------------------------------------------------------------- #
# Skip-safety: never skip pre_auth where it WOULD fire                         #
# --------------------------------------------------------------------------- #

def test_does_not_skip_pre_auth_when_it_would_fire(pe):
    """DIAGNOSTIC MRI > 10k pre-auth threshold: pre_auth WOULD FAIL. Confirm the
    supervisor does NOT skip pre_auth, AND that pre_auth indeed FAILs here."""
    member = _member(pe, "EMP001")
    amount = 15000.0  # > 10000 threshold
    sub = _submission(pe, member, "DIAGNOSTIC", date(2024, 6, 1), amount)
    invoked, skipped = select_rules(sub, member, pe)
    assert "pre_auth" in invoked
    assert "pre_auth" not in _skipped_names(skipped)

    # And prove pre_auth really fires on this input (so the non-skip mattered).
    bill = ExtractionResult(
        file_id="MRI-1", doc_type="HOSPITAL_BILL",
        patient_name=StrField(value=member["name"], confidence=0.97),
        treatment=StrField(value="MRI scan", confidence=0.95),
        line_items=[LineItem(description="MRI scan", amount=amount)],
        total_amount=NumField(value=amount, confidence=0.96))
    ctx = RuleContext(sub, member, [bill], SemanticMapping(confidence=0.9), pe)
    v = pre_auth.check(ctx)
    assert v.status == "FAIL" and v.reason_code == "PRE_AUTH_MISSING"


# --------------------------------------------------------------------------- #
# EQUIVALENCE PROOF — adaptive routing == all-rules routing                    #
# --------------------------------------------------------------------------- #

def _decide(case, pe: PolicyEngine, rule_names: list[str]):
    """Run ONLY `rule_names` through the decide-equivalent path (mirrors
    decision_eval.decide_from_facts but with a configurable rule subset)."""
    s = case.submission
    ctx = RuleContext(s, pe.member(s.member_id), case.extractions,
                      case.semantic or SemanticMapping(confidence=0.3), pe)
    verdicts = [_RULE_MODULES[name].check(ctx) for name in rule_names]
    disallowed = [d for v in verdicts for d in v.disallowed_items]
    items = [i for e in case.extractions
             if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL") for i in e.line_items]
    if not items:
        total = next((e.total_amount.value for e in case.extractions
                      if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL")
                      and e.total_amount.value), None)
        fallback = total or (s.claimed_amount if s.claimed_amount and s.claimed_amount > 0 else None)
        if fallback:
            items = [LineItem(description="Claimed amount", amount=float(fallback))]
    hospital = s.hospital_name or next((e.hospital_name.value for e in case.extractions
                                        if e.hospital_name.value), None)
    fb = calculate(pe, s.claim_category, pe.is_network(hospital), items, disallowed)
    return aggregate(verdicts, fb, pe.fraud_thresholds()["auto_manual_review_above"])


def test_equivalence_adaptive_vs_all_rules(pe):
    """For every synthetic case (630), assert the adaptive-routing decision is
    byte-identical (status + amount + reason codes) to the all-rules decision.
    This PROVES the provably-safe skips never change a decision."""
    cases = generate_cases(pe)
    assert len(cases) >= 600  # sanity: the full generated set
    n_with_skips = 0
    for case in cases:
        member = pe.member(case.submission.member_id)
        invoked, skipped = select_rules(case.submission, member, pe)
        if skipped:
            n_with_skips += 1
        d_adaptive = _decide(case, pe, invoked)
        d_all = _decide(case, pe, list(ALL_RULES))
        assert d_adaptive.status == d_all.status, (
            f"{case.case_id}: status {d_adaptive.status} != {d_all.status} "
            f"(invoked={invoked}, skipped={[s['rule'] for s in skipped]})")
        assert abs(d_adaptive.approved_amount - d_all.approved_amount) < 1e-9, (
            f"{case.case_id}: amount {d_adaptive.approved_amount} != {d_all.approved_amount}")
        assert ([r.code for r in d_adaptive.reason_codes]
                == [r.code for r in d_all.reason_codes]), (
            f"{case.case_id}: reason codes differ "
            f"{[r.code for r in d_adaptive.reason_codes]} != "
            f"{[r.code for r in d_all.reason_codes]}")
    # The equivalence is only meaningful if skips actually happened on a real fraction.
    assert n_with_skips > 0, "expected the supervisor to skip rules on at least some cases"
