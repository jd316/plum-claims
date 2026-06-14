from app.rules.aggregator import aggregate
from app.models.schemas import RuleVerdict, FinancialBreakdown, LineItemDecision

FB = FinancialBreakdown(gross=1500, approved_amount=1350, copay_pct=10, copay_amount=150,
                        line_items=[LineItemDecision(description="Consultation Fee", amount=1000, approved=True)],
                        steps=["x"])

def _v(rule, status, code=None, detail="", items=()):
    return RuleVerdict(rule=rule, status=status, reason_code=code, detail=detail,
                       disallowed_items=list(items))

def test_all_pass_approves():
    d = aggregate([_v("waiting_period","PASS"), _v("limits","PASS"), _v("pre_auth","PASS"),
                   _v("coverage_exclusion","PASS"), _v("fraud_anomaly","PASS")], FB, auto_review_above=25000)
    assert d.status == "APPROVED" and d.approved_amount == 1350

def test_any_fail_rejects_with_ranked_reasons():
    d = aggregate([_v("pre_auth","FAIL","PRE_AUTH_MISSING","need pre-auth"),
                   _v("limits","FAIL","SUB_LIMIT_EXCEEDED","over"),
                   _v("waiting_period","PASS"), _v("coverage_exclusion","PASS"), _v("fraud_anomaly","PASS")],
                  FB, auto_review_above=25000)
    assert d.status == "REJECTED" and d.approved_amount == 0
    assert d.reason_codes[0].code == "PRE_AUTH_MISSING"

def test_permanent_denial_leads_over_temporary():
    # An obesity claim is both EXCLUDED (permanent) and inside its waiting period (temporary).
    # The member message must lead with the exclusion — telling them "eligible from <date>"
    # would be misleading because the treatment is never covered, not merely waiting.
    d = aggregate([_v("waiting_period","FAIL","WAITING_PERIOD","eligible from 2025-04-01"),
                   _v("coverage_exclusion","FAIL","EXCLUDED_CONDITION","Obesity and weight loss programs — not covered"),
                   _v("limits","FAIL","PER_CLAIM_EXCEEDED","over"),
                   _v("pre_auth","PASS"), _v("fraud_anomaly","PASS")],
                  FB, auto_review_above=25000)
    assert d.status == "REJECTED" and d.approved_amount == 0
    assert d.reason_codes[0].code == "EXCLUDED_CONDITION"
    assert "not covered" in d.member_message
    # The waiting-period reason is still present (containment), just not the lead.
    assert "WAITING_PERIOD" in [r.code for r in d.reason_codes]

def test_disallowed_items_make_partial():
    fb = FinancialBreakdown(gross=8000, approved_amount=8000, steps=["x"],
        line_items=[LineItemDecision(description="Root Canal Treatment", amount=8000, approved=True),
                    LineItemDecision(description="Teeth Whitening", amount=4000, approved=False, reason="excluded")])
    d = aggregate([_v("coverage_exclusion","PASS",items=["Teeth Whitening"]), _v("waiting_period","PASS"),
                   _v("limits","PASS"), _v("pre_auth","PASS"), _v("fraud_anomaly","PASS")],
                  fb, auto_review_above=25000)
    assert d.status == "PARTIAL" and d.approved_amount == 8000

def test_flag_routes_manual_review():
    d = aggregate([_v("fraud_anomaly","FLAG","FRAUD_SIGNALS","4 same-day claims"),
                   _v("waiting_period","PASS"), _v("limits","PASS"), _v("pre_auth","PASS"),
                   _v("coverage_exclusion","PASS")], FB, auto_review_above=25000)
    assert d.status == "MANUAL_REVIEW"
    assert any("same-day" in r.detail or "same day" in r.detail for r in d.reason_codes)
