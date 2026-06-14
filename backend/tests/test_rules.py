from app.rules.base import RuleContext
from app.rules import waiting_period, coverage_exclusion, pre_auth, limits, fraud
from app.models.schemas import (ClaimSubmission, DocumentInput, ExtractionResult, LineItem, NumField, SemanticMapping, ClaimHistoryItem)
from app.services.policy_engine import PolicyEngine
from tests.conftest import REPO_ROOT

pe = PolicyEngine(str(REPO_ROOT / "policy_terms.json"))

def _ctx(category="CONSULTATION", member_id="EMP001", amount=1500, tdate="2024-11-01",
         items=(("Consultation Fee", 1000),), semantic=None, history=(), hospital=None, ytd=None):
    sub = ClaimSubmission(member_id=member_id, policy_id="PLUM_GHI_2024", claim_category=category,
                          treatment_date=tdate, claimed_amount=amount, hospital_name=hospital,
                          ytd_claims_amount=ytd,
                          claims_history=[ClaimHistoryItem(**h) for h in history],
                          documents=[DocumentInput(file_id="F", stored_path="/dev/null")])
    bill = ExtractionResult(doc_type="HOSPITAL_BILL",
                            line_items=[LineItem(description=d, amount=a) for d, a in items],
                            total_amount=NumField(value=float(sum(a for _, a in items)), confidence=.95))
    return RuleContext(sub, pe.member(member_id), [bill], semantic or SemanticMapping(confidence=.9), pe)

def test_waiting_period_rejects_diabetes_within_90_days():
    v = waiting_period.check(_ctx(member_id="EMP005", tdate="2024-10-15", amount=3000,
                                  semantic=SemanticMapping(waiting_condition="diabetes", confidence=.9)))
    assert v.status == "FAIL" and v.reason_code == "WAITING_PERIOD"
    assert "2024-11-30" in v.detail

def test_waiting_period_passes_clean():
    assert waiting_period.check(_ctx()).status == "PASS"

def test_dental_line_item_exclusion():
    v = coverage_exclusion.check(_ctx(category="DENTAL", member_id="EMP002", amount=12000,
        items=(("Root Canal Treatment", 8000), ("Teeth Whitening", 4000))))
    assert v.status == "PASS" and v.disallowed_items == ["Teeth Whitening"]

def test_excluded_condition_rejects_whole_claim():
    v = coverage_exclusion.check(_ctx(member_id="EMP009", amount=8000,
        items=(("Bariatric Consultation", 3000), ("Personalised Diet and Nutrition Program", 5000)),
        semantic=SemanticMapping(exclusion_candidates=["Obesity and weight loss programs"], confidence=.95)))
    assert v.status == "FAIL" and v.reason_code == "EXCLUDED_CONDITION"

def test_mri_above_threshold_requires_preauth():
    v = pre_auth.check(_ctx(category="DIAGNOSTIC", member_id="EMP007", amount=15000,
                            items=(("MRI Lumbar Spine", 15000),)))
    assert v.status == "FAIL" and v.reason_code == "PRE_AUTH_MISSING"
    assert "pre-auth" in v.detail.lower() and "resubmit" in v.detail.lower()

def test_cheap_diagnostic_passes():
    assert pre_auth.check(_ctx(category="DIAGNOSTIC", amount=1500, items=(("CBC Test", 300),))).status == "PASS"

def test_consultation_over_per_claim_limit():
    v = limits.check(_ctx(member_id="EMP003", amount=7500, items=(("Consultation Fee",2000),("Medicines",5500))))
    assert v.status == "FAIL" and v.reason_code == "PER_CLAIM_EXCEEDED"
    assert "5,000" in v.detail.replace("₹","").replace("Rs. ","") or "5000" in v.detail

def test_consultation_4500_within_limit():
    assert limits.check(_ctx(member_id="EMP010", amount=4500)).status == "PASS"

def test_dental_governed_by_sub_limit_not_per_claim():
    assert limits.check(_ctx(category="DENTAL", member_id="EMP002", amount=12000,
        items=(("Root Canal Treatment", 8000), ("Teeth Whitening", 4000)))).status == "PASS"

def test_diagnostic_over_sub_limit():
    v = limits.check(_ctx(category="DIAGNOSTIC", member_id="EMP007", amount=15000,
                          items=(("MRI Lumbar Spine", 15000),)))
    assert v.status == "FAIL" and v.reason_code == "SUB_LIMIT_EXCEEDED"

def test_same_day_claims_flag():
    h = [{"claim_id": f"C{i}", "date": "2024-10-30", "amount": 1200, "provider": "X"} for i in range(3)]
    v = fraud.check(_ctx(member_id="EMP008", amount=4800, tdate="2024-10-30", history=h))
    assert v.status == "FLAG" and "same day" in v.detail.lower() and "4" in v.detail

def test_no_history_no_flag():
    # claimed_amount matches the bill total (1000) and no history -> no fraud signals
    assert fraud.check(_ctx(amount=1000)).status == "PASS"

def test_monthly_claims_limit_flags():
    # 6 prior claims in the same calendar month as the (different-day) treatment date -> 7 > 6.
    # Dates differ from treatment_date so the same-day rule does not fire; only monthly does.
    h = [{"claim_id": f"M{i}", "date": "2024-11-%02d" % (i + 1), "amount": 800, "provider": "X"}
         for i in range(6)]
    v = fraud.check(_ctx(member_id="EMP001", amount=1000, tdate="2024-11-20", history=h))
    assert v.status == "FLAG" and "monthly" in v.detail.lower() and "2024-11" in v.detail

def test_claimed_amount_vs_bill_total_mismatch_flags():
    # claimed 1500 but the bill total reads 5000 -> reconciliation fraud signal
    sub = ClaimSubmission(member_id="EMP001", policy_id="PLUM_GHI_2024", claim_category="CONSULTATION",
                          treatment_date="2024-11-01", claimed_amount=1500,
                          documents=[DocumentInput(file_id="F", stored_path="/dev/null")])
    bill = ExtractionResult(file_id="F1", doc_type="HOSPITAL_BILL",
                            line_items=[LineItem(description="Consultation Fee", amount=5000)],
                            total_amount=NumField(value=5000.0, confidence=.95))
    ctx = RuleContext(sub, pe.member("EMP001"), [bill], SemanticMapping(confidence=.9), pe)
    v = fraud.check(ctx)
    assert v.status == "FLAG" and "does not match" in v.detail.lower()

def test_line_item_total_mismatch_flags():
    # a bill whose line items (sum 1000) do NOT match its stated total (5000) -> fraud signal
    sub = ClaimSubmission(member_id="EMP001", policy_id="PLUM_GHI_2024", claim_category="CONSULTATION",
                          treatment_date="2024-11-01", claimed_amount=1000,
                          documents=[DocumentInput(file_id="F", stored_path="/dev/null")])
    bill = ExtractionResult(file_id="F1", doc_type="HOSPITAL_BILL",
                            line_items=[LineItem(description="Consultation Fee", amount=1000)],
                            total_amount=NumField(value=5000.0, confidence=.95))
    ctx = RuleContext(sub, pe.member("EMP001"), [bill], SemanticMapping(confidence=.9), pe)
    v = fraud.check(ctx)
    assert v.status == "FLAG" and "sum" in v.detail.lower()
