"""Deterministic tests for the synthetic generator + decision-layer eval harness.

No Gemini: everything runs against the REAL rules in-process. PURE-ADDITIVE.
"""
from datetime import date

import pytest

from app.config import settings
from app.services.policy_engine import PolicyEngine
from app.models.schemas import (
    ClaimSubmission, ExtractionResult, SemanticMapping, LineItem, StrField, NumField,
)
from app.evalrunner.synthetic import (
    generate_cases, reference_payout, SyntheticCase, NETWORK_HOSPITAL,
    ELIGIBLE_DATE,
)
from app.evalrunner.decision_eval import (
    decide_from_facts, run_decision_eval, _case_matches, STATUSES,
)


@pytest.fixture(scope="module")
def pe() -> PolicyEngine:
    return PolicyEngine(settings.policy_path)


@pytest.fixture(scope="module")
def cases(pe) -> list[SyntheticCase]:
    return generate_cases(pe)


# --------------------------------------------------------------------------- #
# Generator                                                                    #
# --------------------------------------------------------------------------- #

def test_generator_yields_enough_labeled_cases(cases):
    assert len(cases) >= 150
    templates = {c.template for c in cases}
    # Every documented template must be represented.
    assert templates == {
        "clean_approval", "waiting_period", "excluded_condition", "per_claim_exceeded",
        "sub_limit_exceeded", "pre_auth_missing", "dental_partial", "same_day_fraud",
        "high_value",
    }


def test_every_case_has_valid_expected_and_template(cases):
    for c in cases:
        assert c.template
        assert c.expected["status"] in STATUSES
        if c.expected["status"] == "REJECTED":
            assert "reason_code" in c.expected
        if c.expected["status"] in ("APPROVED", "PARTIAL"):
            assert "expected_amount" in c.expected
            assert c.expected["expected_amount"] >= 0


def test_every_member_referenced_exists_in_policy(cases, pe):
    valid_ids = {m["member_id"] for m in pe.members()}
    for c in cases:
        assert c.submission.member_id in valid_ids


def test_generation_is_reproducible(pe):
    a = generate_cases(pe)
    b = generate_cases(pe)
    assert [c.case_id for c in a] == [c.case_id for c in b]
    assert [c.expected for c in a] == [c.expected for c in b]


def test_reference_payout_matches_known_consultation():
    pe = PolicyEngine(settings.policy_path)
    # CONSULTATION network: gross 1875 -> x0.8 (20% net) -> x0.9 (10% copay) = 1350.
    assert reference_payout(pe, "CONSULTATION", True, 1875.0) == 1350.0
    # Non-network: only copay applies -> 1875 x 0.9 = 1687.5
    assert reference_payout(pe, "CONSULTATION", False, 1875.0) == 1687.5


# --------------------------------------------------------------------------- #
# decide_from_facts on hand-built cases                                        #
# --------------------------------------------------------------------------- #

def _hand_clean_approval(pe) -> SyntheticCase:
    member = pe.member("EMP001")
    gross = 1875.0
    items = [LineItem(description="Consultation service", amount=gross)]
    bill = ExtractionResult(file_id="B1", doc_type="HOSPITAL_BILL",
                            patient_name=StrField(value=member["name"], confidence=0.97),
                            hospital_name=StrField(value=NETWORK_HOSPITAL, confidence=0.95),
                            line_items=items, total_amount=NumField(value=gross, confidence=0.96))
    sub = ClaimSubmission(member_id="EMP001", policy_id="PLUM_GHI_2024",
                          claim_category="CONSULTATION", treatment_date=ELIGIBLE_DATE,
                          claimed_amount=gross, hospital_name=NETWORK_HOSPITAL, documents=[])
    return SyntheticCase(case_id="hand-clean", template="clean_approval", submission=sub,
                         extractions=[bill], semantic=SemanticMapping(confidence=0.95),
                         expected={"status": "APPROVED", "expected_amount": 1350.0})


def test_decide_from_facts_clean_approval(pe):
    case = _hand_clean_approval(pe)
    d = decide_from_facts(case, pe)
    assert d.status == "APPROVED"
    assert abs(d.approved_amount - 1350.0) <= 1


def test_decide_from_facts_waiting_period(pe):
    member = pe.member("EMP001")  # joined 2024-04-01
    # 40 days after join -> outside initial 30d window but inside diabetes 90d.
    tdate = date(2024, 5, 11)
    items = [LineItem(description="Consultation service", amount=3000.0)]
    bill = ExtractionResult(file_id="W1", doc_type="HOSPITAL_BILL",
                            patient_name=StrField(value=member["name"], confidence=0.97),
                            hospital_name=StrField(value=NETWORK_HOSPITAL, confidence=0.95),
                            line_items=items, total_amount=NumField(value=3000.0, confidence=0.96))
    sub = ClaimSubmission(member_id="EMP001", policy_id="PLUM_GHI_2024",
                          claim_category="CONSULTATION", treatment_date=tdate,
                          claimed_amount=3000.0, hospital_name=NETWORK_HOSPITAL, documents=[])
    case = SyntheticCase(case_id="hand-wait", template="waiting_period", submission=sub,
                         extractions=[bill],
                         semantic=SemanticMapping(waiting_condition="diabetes", confidence=0.9),
                         expected={"status": "REJECTED", "reason_code": "WAITING_PERIOD"})
    d = decide_from_facts(case, pe)
    assert d.status == "REJECTED"
    assert "WAITING_PERIOD" in [c.code for c in d.reason_codes]


# --------------------------------------------------------------------------- #
# Metrics computation on a tiny hand-set (with one deliberate "wrong")         #
# --------------------------------------------------------------------------- #

def test_metrics_on_tiny_handset(pe):
    """3 cases: 2 correctly labeled, 1 deliberately mislabeled -> accuracy 2/3,
    confusion-matrix counts correct."""
    good_clean = _hand_clean_approval(pe)  # really APPROVED, labeled APPROVED -> correct

    # A waiting-period case but DELIBERATELY mislabeled as APPROVED -> a wrong prediction.
    member = pe.member("EMP001")
    items = [LineItem(description="Consultation service", amount=3000.0)]
    bill = ExtractionResult(file_id="X1", doc_type="HOSPITAL_BILL",
                            patient_name=StrField(value=member["name"], confidence=0.97),
                            hospital_name=StrField(value=NETWORK_HOSPITAL, confidence=0.95),
                            line_items=items, total_amount=NumField(value=3000.0, confidence=0.96))
    bad = SyntheticCase(case_id="hand-bad", template="excluded_condition",
                        submission=ClaimSubmission(
                            member_id="EMP001", policy_id="PLUM_GHI_2024",
                            claim_category="CONSULTATION", treatment_date=date(2024, 5, 11),
                            claimed_amount=3000.0, hospital_name=NETWORK_HOSPITAL, documents=[]),
                        extractions=[bill],
                        semantic=SemanticMapping(waiting_condition="diabetes", confidence=0.9),
                        # Deliberately WRONG expected label: real outcome is REJECTED.
                        expected={"status": "APPROVED", "expected_amount": 100.0})

    # A genuine reject correctly labeled.
    items2 = [LineItem(description="Consultation service", amount=6000.0)]
    bill2 = ExtractionResult(file_id="X2", doc_type="HOSPITAL_BILL",
                             patient_name=StrField(value=member["name"], confidence=0.97),
                             hospital_name=StrField(value=NETWORK_HOSPITAL, confidence=0.95),
                             line_items=items2, total_amount=NumField(value=6000.0, confidence=0.96))
    good_reject = SyntheticCase(case_id="hand-reject", template="per_claim_exceeded",
                                submission=ClaimSubmission(
                                    member_id="EMP001", policy_id="PLUM_GHI_2024",
                                    claim_category="CONSULTATION", treatment_date=ELIGIBLE_DATE,
                                    claimed_amount=6000.0, hospital_name=NETWORK_HOSPITAL,
                                    documents=[]),
                                extractions=[bill2], semantic=SemanticMapping(confidence=0.95),
                                expected={"status": "REJECTED",
                                          "reason_code": "PER_CLAIM_EXCEEDED"})

    result = run_decision_eval([good_clean, bad, good_reject], pe)
    assert result["n"] == 3
    assert result["correct"] == 2
    assert abs(result["overall_accuracy"] - 2 / 3) < 1e-9

    # Confusion counts: good_clean -> APPROVED/APPROVED; bad expected APPROVED but
    # predicted REJECTED; good_reject -> REJECTED/REJECTED.
    conf = result["confusion"]
    assert conf["APPROVED"]["APPROVED"] == 1
    assert conf["APPROVED"]["REJECTED"] == 1  # the mislabeled one
    assert conf["REJECTED"]["REJECTED"] == 1


def test_case_matches_helper(pe):
    case = _hand_clean_approval(pe)
    d = decide_from_facts(case, pe)
    ok, _ = _case_matches(case, d)
    assert ok
    # Wrong expected amount -> mismatch.
    case.expected["expected_amount"] = 9999.0
    ok2, why = _case_matches(case, d)
    assert not ok2 and "amount" in why


# --------------------------------------------------------------------------- #
# Full generated set -> high accuracy                                          #
# --------------------------------------------------------------------------- #

def test_full_eval_high_accuracy(cases, pe):
    result = run_decision_eval(cases, pe)
    assert result["n"] >= 150
    assert result["overall_accuracy"] >= 0.95, (
        f"accuracy {result['overall_accuracy']:.3f}; mismatches: "
        f"{result['mismatches'][:5]}")
    # Amount cross-check: the independent reference formula agrees with financial.py.
    assert result["amount_max_error"] <= 1.0
    # Reason codes on rejects are correct.
    assert result["reason_code_accuracy"] >= 0.95


def test_per_class_metrics_present(cases, pe):
    result = run_decision_eval(cases, pe)
    for cls in STATUSES:
        assert cls in result["per_class"]
        c = result["per_class"][cls]
        assert set(c) >= {"precision", "recall", "f1", "support"}
