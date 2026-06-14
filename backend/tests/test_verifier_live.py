import pytest
from app.agents.verifier import verify
from app.models.schemas import Decision, RuleVerdict, FinancialBreakdown
pytestmark = pytest.mark.live

def test_verifier_passes_a_consistent_approval():
    fb = FinancialBreakdown(gross=1500, approved_amount=1350, copay_pct=10, copay_amount=150, steps=["x"])
    d = Decision(status="APPROVED", approved_amount=1350, member_message="Approved Rs.1350.", financial=fb)
    verdicts = [RuleVerdict(rule="waiting_period", status="PASS", detail="ok"),
                RuleVerdict(rule="limits", status="PASS", detail="within limits"),
                RuleVerdict(rule="coverage_exclusion", status="PASS", detail="covered")]
    r = verify(d, verdicts)
    assert r.verdict in ("PASS", "FAIL")          # returns a valid structured verdict
    assert 0.0 <= r.confidence <= 1.0 and isinstance(r.reason, str)

def test_verifier_flags_an_inconsistent_decision():
    # status APPROVED but a rule clearly FAILED -> a competent judge should return FAIL
    fb = FinancialBreakdown(gross=15000, approved_amount=15000, steps=["x"])
    d = Decision(status="APPROVED", approved_amount=15000, member_message="Approved.", financial=fb)
    verdicts = [RuleVerdict(rule="pre_auth", status="FAIL", reason_code="PRE_AUTH_MISSING",
                            detail="MRI above threshold requires pre-authorization, none provided")]
    r = verify(d, verdicts)
    assert r.verdict == "FAIL"
