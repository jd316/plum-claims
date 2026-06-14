"""Member-facing additive features: deterministic payout estimate + read-only
per-claim chat assistant. The estimate tests are pure (no Gemini); the chat test
is live-marked and seeds a persisted APPROVED claim before asking 1-2 questions.

These features are additive — they never touch the decision pipeline or the 12
cases. The estimate mirrors financial.calculate exactly (network discount first,
then co-pay), so the asserted numbers match the real arithmetic."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import (ClaimResult, Decision, FinancialBreakdown,
                                LineItemDecision, ReasonCode)
from app.services import persistence

client = TestClient(app)


# --- Deterministic payout estimate (no Gemini) ------------------------------

def test_estimate_consultation_non_network_copay_only():
    """CONSULTATION ₹1500, no hospital → 10% copay, no discount → ₹1350."""
    r = client.post("/api/estimate", json={
        "claim_category": "CONSULTATION", "claimed_amount": 1500})
    assert r.status_code == 200
    body = r.json()
    assert body["estimated_payout"] == 1350
    assert body["network_discount_amount"] == 0
    assert body["copay_amount"] == 150
    assert body["is_network"] is False
    assert "estimate only" in body["note"].lower()
    assert body["breakdown_steps"]


def test_estimate_consultation_network_discount_then_copay():
    """CONSULTATION ₹4500 at Apollo Hospitals → 20% discount (₹900) then 10%
    copay on ₹3600 (₹360) → ₹3240. Network discount applied FIRST."""
    r = client.post("/api/estimate", json={
        "claim_category": "CONSULTATION", "claimed_amount": 4500,
        "hospital_name": "Apollo Hospitals"})
    assert r.status_code == 200
    body = r.json()
    assert body["estimated_payout"] == 3240
    assert body["network_discount_amount"] == 900
    assert body["copay_amount"] == 360
    assert body["is_network"] is True


def test_estimate_unknown_category_is_422():
    r = client.post("/api/estimate", json={
        "claim_category": "NONSENSE", "claimed_amount": 1000})
    assert r.status_code == 422


def test_estimate_non_positive_amount_is_422():
    r = client.post("/api/estimate", json={
        "claim_category": "CONSULTATION", "claimed_amount": 0})
    assert r.status_code == 422


# --- Read-only per-claim chat assistant (live Gemini) -----------------------

def _seed_approved_claim() -> str:
    """Persist a simple APPROVED claim and return its id. Best-effort: skips the
    test only if the DB is genuinely unavailable. A unique id per run keeps the
    seed idempotent — no primary-key conflict on re-runs against a populated DB."""
    import uuid
    from app.models.schemas import ClaimSubmission, DocumentInput
    claim_id = f"CLM-test-chat-{uuid.uuid4().hex[:10]}"
    fin = FinancialBreakdown(
        gross=1500.0, network_discount_pct=0.0, network_discount_amount=0.0,
        post_discount=1500.0, copay_pct=10.0, copay_amount=150.0,
        line_items=[LineItemDecision(description="Consultation Fee", amount=1500.0, approved=True)],
        approved_amount=1350.0,
        steps=["Covered line items total ₹1,500.00 (1/1 items approved)",
               "Co-pay 10% applied on post-discount amount: −₹150.00",
               "Approved amount: ₹1,350.00"])
    decision = Decision(
        status="APPROVED", approved_amount=1350.0,
        reason_codes=[ReasonCode(code="COPAY_APPLIED", detail="A 10% co-pay was applied.")],
        member_message="Your consultation claim was approved for ₹1,350 after a 10% co-pay.",
        confidence=0.95, financial=fin)
    result = ClaimResult(claim_id=claim_id, blocked=False, decision=decision)
    sub = ClaimSubmission(
        member_id="MEM001", policy_id="PLUM_GHI_2024", claim_category="CONSULTATION",
        treatment_date="2024-06-01", claimed_amount=1500.0,
        documents=[DocumentInput(file_id="F001", file_name="bill.pdf", stored_path="/tmp/x.pdf")])
    persistence.save_claim(sub, result)
    return claim_id


def _db_reachable() -> bool:
    # Mirrors the guard in test_persistence.py: the /ask endpoint must look the
    # claim up in Postgres before it can return 404, so this test is DB-dependent
    # and is skipped (rather than failing) on a bare checkout with no Postgres.
    try:
        from sqlalchemy import text
        with persistence.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_reachable(), reason="Postgres unreachable — /ask is DB-backed")
def test_ask_unknown_claim_is_404():
    r = client.post("/api/claims/CLM-does-not-exist/ask", json={"question": "why?"})
    assert r.status_code == 404


@pytest.mark.live
def test_chat_grounded_on_approved_claim():
    try:
        claim_id = _seed_approved_claim()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB unavailable for seeding: {e}")

    # 1) A question answerable from the claim facts → non-empty answer that
    #    references the approval / amount.
    r = client.post(f"/api/claims/{claim_id}/ask",
                    json={"question": "Why was this approved?"})
    assert r.status_code == 200
    answer = r.json()["answer"]
    assert isinstance(answer, str) and answer.strip()
    low = answer.lower()
    assert ("approv" in low or "1350" in answer or "1,350" in answer or "co-pay" in low
            or "copay" in low), f"answer did not reference the approval/amount: {answer!r}"

    # 2) Something unknowable from the claim facts → it declines rather than
    #    inventing a policy answer.
    r2 = client.post(f"/api/claims/{claim_id}/ask",
                     json={"question": "What is the capital of France according to my policy?"})
    assert r2.status_code == 200
    a2 = r2.json()["answer"]
    assert isinstance(a2, str) and a2.strip()
    assert "paris" not in a2.lower(), f"assistant invented an answer: {a2!r}"
