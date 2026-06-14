"""Operator final-decision (human-in-the-loop) tests — no Gemini.

The endpoint sets the final decision, persists it, and audits it. Endpoint +
persistence tests use real Postgres and SKIP if unreachable (like test_correction).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import tests.conftest  # noqa: F401 — inserts backend/ on sys.path
from app.config import settings
from tests.test_correction import _facts, _stored, _persist, db_required, FILE_ID  # noqa: F401


def _persist_manual_review() -> str:
    """Persist a claim whose AI decision is MANUAL_REVIEW (needs a human)."""
    stored, submission = _stored(_facts("CONSULTATION", 1500.0))
    stored["decision"]["status"] = "MANUAL_REVIEW"
    stored["decision"]["approved_amount"] = 0.0
    stored["blocked"] = False
    return _persist(stored, submission)


@db_required
def test_operator_resolves_manual_review_to_approved():
    from app.main import app
    from app.services.persistence import get_claim
    from app.services.audit import audit_trail

    cid = _persist_manual_review()
    client = TestClient(app)
    r = client.post(f"/api/claims/{cid}/decision", json={
        "status": "APPROVED", "approved_amount": 1350.0,
        "note": "Verified documents by phone; genuine claim."})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["before"]["status"] == "MANUAL_REVIEW"
    assert body["after"]["status"] == "APPROVED"
    assert body["after"]["amount"] == pytest.approx(1350.0)

    stored = get_claim(cid)
    assert stored["decision"]["status"] == "APPROVED"
    assert stored["decision"]["approved_amount"] == pytest.approx(1350.0)
    assert stored["decided_by"] is not None
    assert stored["correction_history"][-1]["action"] == "operator_decision"
    assert stored["decision"]["reason_codes"][0]["code"] == "OPERATOR_DECISION"

    rows = [t for t in audit_trail(cid) if t["action"] == "OPERATOR_DECISION"]
    assert rows and rows[-1]["reason_codes"]["note"].startswith("Verified")
    assert rows[-1]["reason_codes"]["before"]["status"] == "MANUAL_REVIEW"


@db_required
def test_operator_reject_zeroes_amount():
    from app.main import app
    from app.services.persistence import get_claim

    cid = _persist_manual_review()
    client = TestClient(app)
    r = client.post(f"/api/claims/{cid}/decision", json={
        "status": "REJECTED", "approved_amount": 9999.0,
        "note": "Duplicate of an earlier claim."})
    assert r.status_code == 200, r.text
    assert r.json()["after"]["amount"] == 0.0
    assert get_claim(cid)["decision"]["approved_amount"] == 0.0


@db_required
def test_operator_decision_requires_note():
    from app.main import app
    cid = _persist_manual_review()
    resp = TestClient(app).post(f"/api/claims/{cid}/decision",
                                json={"status": "APPROVED", "note": "   "})
    assert resp.status_code == 422


@db_required
def test_operator_decision_404_unknown_claim():
    from app.main import app
    resp = TestClient(app).post("/api/claims/does-not-exist-xyz/decision",
                                json={"status": "APPROVED", "note": "x"})
    assert resp.status_code == 404


@db_required
def test_operator_decision_409_on_blocked_claim():
    """A claim blocked on a document problem has no decision to resolve → 409."""
    from app.main import app
    from app.models.schemas import ClaimResult
    from app.services.persistence import init_db, save_claim
    from app.models.schemas import ClaimSubmission, DocumentProblem
    init_db()
    stored, submission = _stored(_facts("CONSULTATION", 1500.0))
    result = ClaimResult(**stored)
    result.blocked = True
    result.decision = None
    result.problems = [DocumentProblem(kind="MISSING_DOCUMENT", message="need a bill")]
    save_claim(ClaimSubmission(**submission), result)
    resp = TestClient(app).post(f"/api/claims/{result.claim_id}/decision",
                                json={"status": "APPROVED", "note": "x"})
    assert resp.status_code == 409


@db_required
def test_member_cannot_operator_decide_when_auth_on(monkeypatch):
    from app.main import app
    from app.services import auth as auth_mod

    cid = _persist_manual_review()
    monkeypatch.setattr(settings, "auth_enabled", True)
    token = auth_mod.make_token(auth_mod.Principal(username="mem", role="member", member_id="EMP001"))
    resp = TestClient(app).post(f"/api/claims/{cid}/decision",
                                headers={"Authorization": f"Bearer {token}"},
                                json={"status": "APPROVED", "note": "x"})
    assert resp.status_code == 403
