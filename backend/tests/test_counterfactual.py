"""Counterfactual + what-if tests — DETERMINISTIC, no Gemini.

Every assertion runs the REAL deterministic decision layer (the 5 rule agents +
financial.calculate + aggregator) via app.services.counterfactual, with exact
expected numbers (no doubles, no approximations beyond float rounding). These
mirror the eval-runner's same-facts→same-decision guarantee but exercise the
perturb-and-re-decide paths the explainability features rely on.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.models.schemas import (ClaimSubmission, ExtractionResult, SemanticMapping,
                                DocumentInput, StrField, NumField, LineItem)
from app.evalrunner.synthetic import SyntheticCase, NETWORK_HOSPITAL, NON_NETWORK_HOSPITAL
from app.services.counterfactual import (counterfactuals, what_if, reconstruct_facts,
                                         _decide, _engine)


# --------------------------------------------------------------------------- #
# Fact builders                                                               #
# --------------------------------------------------------------------------- #

def _member() -> dict:
    return _engine().members()[0]


def _bill(items: list[LineItem], hospital: str | None, doc_type: str = "HOSPITAL_BILL"):
    total = round(sum(i.amount for i in items), 2)
    return ExtractionResult(
        file_id="F001", doc_type=doc_type,
        patient_name=StrField(value=_member()["name"], confidence=0.95),
        hospital_name=StrField(value=hospital, confidence=0.95) if hospital else StrField(),
        line_items=items, total_amount=NumField(value=total, confidence=0.95),
    )


def _case(category: str, amount: float, hospital: str | None, tdate: date,
          semantic: SemanticMapping | None = None) -> SyntheticCase:
    m = _member()
    sub = ClaimSubmission(
        member_id=m["member_id"], policy_id="POL-1", claim_category=category,
        treatment_date=tdate, claimed_amount=amount, hospital_name=hospital,
        documents=[DocumentInput(file_id="F001", file_name="bill.png", stored_path="/tmp/x.png")],
    )
    bill = _bill([LineItem(description=f"{category.title()} service", amount=amount)], hospital)
    case = SyntheticCase(case_id="t", template="test", submission=sub,
                         extractions=[bill],
                         semantic=semantic or SemanticMapping(category_match=True, confidence=0.95),
                         expected={})
    case.member = m  # type: ignore[attr-defined]
    return case


# --------------------------------------------------------------------------- #
# Per-claim exceeded                                                          #
# --------------------------------------------------------------------------- #

def test_per_claim_exceeded_counterfactual_and_whatif():
    pe = _engine()
    limit = pe.per_claim_limit()  # 5000
    case = _case("CONSULTATION", 7500.0, NON_NETWORK_HOSPITAL, date(2026, 1, 15))

    before, _ = _decide(case, pe)
    assert before.status == "REJECTED"

    cfs = counterfactuals(case, pe)
    pcl = next(c for c in cfs if c["reason"] == "PER_CLAIM_EXCEEDED")
    assert pcl["achievable"] is True
    assert pcl["resulting_decision"] == "APPROVED"
    # 5000 covered, non-network, CONSULTATION copay 10% -> 4500 exactly.
    assert pcl["resulting_amount"] == 4500.0
    assert f"{int(limit):,}" in pcl["change"] or str(int(limit)) in pcl["change"]

    wi = what_if(case, {"claimed_amount": 4500.0}, pe)
    assert wi["before"]["status"] == "REJECTED"
    assert wi["after"]["status"] == "APPROVED"
    # 4500 covered, non-network, copay 10% -> 4050 exactly.
    assert wi["after"]["approved_amount"] == 4050.0
    assert wi["diff"]["status_changed"] is True
    assert any(r["rule"] == "limits" for r in wi["diff"]["changed_rules"])


# --------------------------------------------------------------------------- #
# Network what-if: toggling network reduces payout by the discount exactly     #
# --------------------------------------------------------------------------- #

def test_network_whatif_reduces_payout_by_discount():
    pe = _engine()
    # A clean non-network CONSULTATION approval at 2000.
    case = _case("CONSULTATION", 2000.0, NON_NETWORK_HOSPITAL, date(2026, 1, 15))
    before, _ = _decide(case, pe)
    assert before.status == "APPROVED"
    # non-network: 2000 * (1 - 0.10 copay) = 1800.
    assert before.approved_amount == 1800.0

    wi = what_if(case, {"is_network": True}, pe)
    assert wi["before"]["approved_amount"] == 1800.0
    # network: 2000 * 0.80 (20% discount) * 0.90 (10% copay) = 1440.
    assert wi["after"]["approved_amount"] == 1440.0
    # Discount reduces the payout by exactly 360.
    assert wi["diff"]["amount_delta"] == -360.0
    assert wi["after"]["status"] == "APPROVED"


# --------------------------------------------------------------------------- #
# Waiting period                                                              #
# --------------------------------------------------------------------------- #

def test_waiting_period_counterfactual_and_clear():
    pe = _engine()
    m = _member()
    join = date.fromisoformat(m["join_date"])
    # diabetes has a 90-day specific waiting period; pick a treatment day inside it
    # but past the 30-day initial window so the specific-condition branch fires.
    tdate = join + timedelta(days=35)
    eligible = join + timedelta(days=90)
    case = _case("CONSULTATION", 3000.0, NETWORK_HOSPITAL, tdate,
                 semantic=SemanticMapping(category_match=True, waiting_condition="diabetes",
                                          confidence=0.9))
    before, _ = _decide(case, pe)
    assert before.status == "REJECTED"

    cfs = counterfactuals(case, pe)
    wp = next(c for c in cfs if c["reason"] == "WAITING_PERIOD")
    # The counterfactual states the eligible-from date.
    assert eligible.isoformat() in wp["change"]
    assert wp["achievable"] is True
    assert wp["resulting_decision"] in ("APPROVED", "PARTIAL")

    # Moving the treatment date to/after the eligible date clears the waiting reason.
    wi = what_if(case, {"treatment_date": eligible.isoformat()}, pe)
    assert wi["before"]["status"] == "REJECTED"
    assert wi["after"]["status"] in ("APPROVED", "PARTIAL")
    assert any(r["rule"] == "waiting_period" for r in wi["diff"]["changed_rules"])


# --------------------------------------------------------------------------- #
# Excluded condition: honest — no fake approval                               #
# --------------------------------------------------------------------------- #

def test_excluded_condition_not_achievable():
    pe = _engine()
    excl = pe.exclusion_conditions()[0]
    case = _case("CONSULTATION", 4000.0, NETWORK_HOSPITAL, date(2026, 1, 15),
                 semantic=SemanticMapping(category_match=True, exclusion_candidates=[excl],
                                          confidence=0.9))
    before, _ = _decide(case, pe)
    assert before.status == "REJECTED"

    cfs = counterfactuals(case, pe)
    ce = next(c for c in cfs if c["reason"] == "EXCLUDED_CONDITION")
    assert ce["achievable"] is False
    assert ce["resulting_decision"] == "REJECTED"
    assert ce["resulting_amount"] == 0.0


# --------------------------------------------------------------------------- #
# reconstruct_facts round-trips a stored ClaimResult shape                    #
# --------------------------------------------------------------------------- #

def test_reconstruct_facts_round_trip():
    pe = _engine()
    case = _case("CONSULTATION", 7500.0, NON_NETWORK_HOSPITAL, date(2026, 1, 15))
    decision, _ = _decide(case, pe)

    # Build a stored-ClaimResult-shaped dict (submission bundled in, like replay).
    stored = {
        "claim_id": "CLM-TEST",
        "blocked": False,
        "decision": decision.model_dump(mode="json"),
        "extractions": [e.model_dump(mode="json") for e in case.extractions],
        "semantic": case.semantic.model_dump(mode="json"),
        "member": case.member,  # type: ignore[attr-defined]
        "submission": case.submission.model_dump(mode="json"),
    }
    facts = reconstruct_facts(stored)
    # The reconstructed facts re-decide to the SAME verdict + amount.
    d2, _ = _decide(facts, pe)
    assert d2.status == decision.status
    assert d2.approved_amount == decision.approved_amount

    # And the counterfactual computed off the reconstructed facts is correct.
    cfs = counterfactuals(facts, pe)
    pcl = next(c for c in cfs if c["reason"] == "PER_CLAIM_EXCEEDED")
    assert pcl["resulting_decision"] == "APPROVED"
    assert pcl["resulting_amount"] == 4500.0


def test_approved_claim_has_no_counterfactuals():
    pe = _engine()
    case = _case("CONSULTATION", 1800.0, NETWORK_HOSPITAL, date(2026, 1, 15))
    before, _ = _decide(case, pe)
    assert before.status == "APPROVED"
    assert counterfactuals(case, pe) == []
