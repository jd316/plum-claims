from app.evalrunner.matching import match_case
from app.models.schemas import ClaimResult, Decision, ReasonCode

def _case(expected): return {"expected": expected}

def test_blocked_case_matches_when_blocked():
    r = ClaimResult(claim_id="x", blocked=True)
    ok, notes = match_case(_case({"decision": None}), r)
    assert ok and notes == []

def test_blocked_case_fails_when_decided():
    r = ClaimResult(claim_id="x", blocked=False, decision=Decision(status="APPROVED"))
    ok, _ = match_case(_case({"decision": None}), r)
    assert not ok

def test_status_mismatch_flagged():
    r = ClaimResult(claim_id="x", decision=Decision(status="APPROVED", approved_amount=100))
    ok, _ = match_case(_case({"decision": "REJECTED"}), r)
    assert not ok

def test_amount_and_reason_and_confidence():
    r = ClaimResult(claim_id="x", decision=Decision(status="REJECTED", approved_amount=0,
        reason_codes=[ReasonCode(code="WAITING_PERIOD", detail="d")], confidence=0.95))
    ok, notes = match_case(_case({"decision": "REJECTED", "rejection_reasons": ["WAITING_PERIOD"]}), r)
    assert ok
    ok2, _ = match_case(_case({"decision": "REJECTED", "rejection_reasons": ["PRE_AUTH_MISSING"]}), r)
    assert not ok2

def test_confidence_threshold_parsed():
    r = ClaimResult(claim_id="x", decision=Decision(status="APPROVED", approved_amount=1350, confidence=0.95))
    ok, _ = match_case(_case({"decision": "APPROVED", "approved_amount": 1350, "confidence_score": "above 0.85"}), r)
    assert ok
    r2 = ClaimResult(claim_id="x", decision=Decision(status="APPROVED", approved_amount=1350, confidence=0.80))
    ok2, _ = match_case(_case({"decision": "APPROVED", "confidence_score": "above 0.85"}), r2)
    assert not ok2
