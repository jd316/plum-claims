"""Ops inline field correction tests — DETERMINISTIC, no Gemini.

The pure correction logic (apply_correction) is exercised directly with no DB.
The persistence + endpoint tests use real Postgres and SKIP if it is unreachable
(mirroring test_persistence.py's guard). No doubles, no Gemini anywhere: every
re-decide runs the real deterministic decision layer via decide_from_facts.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.config import settings
from app.models.schemas import (ClaimSubmission, ClaimResult, ExtractionResult,
                                SemanticMapping, DocumentInput, StrField, NumField, LineItem)
from app.services.policy_engine import get_policy_engine
from app.services.counterfactual import _decide
from app.evalrunner.synthetic import SyntheticCase
from app.services.correction import apply_correction, CorrectionError


# --------------------------------------------------------------------------- #
# Builders — a stored ClaimResult + submission bundle, as persistence returns. #
# --------------------------------------------------------------------------- #

FILE_ID = "F001"


def _member() -> dict:
    return get_policy_engine(settings.policy_path).members()[0]


def _facts(category: str, total: float, hospital: str | None = "Apollo Hospital",
           patient: str | None = None, diagnosis: str | None = None) -> SyntheticCase:
    m = _member()
    sub = ClaimSubmission(
        member_id=m["member_id"], policy_id="POL-1", claim_category=category,
        treatment_date=date(2026, 1, 15), claimed_amount=total, hospital_name=hospital,
        documents=[DocumentInput(file_id=FILE_ID, file_name="bill.png", stored_path="/tmp/x.png")],
    )
    bill = ExtractionResult(
        file_id=FILE_ID, doc_type="HOSPITAL_BILL",
        patient_name=StrField(value=patient or m["name"], confidence=0.55),
        diagnosis=StrField(value=diagnosis or "Consult", confidence=0.6),
        hospital_name=StrField(value=hospital, confidence=0.95) if hospital else StrField(),
        line_items=[LineItem(description=f"{category.title()} service", amount=total)],
        total_amount=NumField(value=total, confidence=0.5),
    )
    case = SyntheticCase(case_id="t", template="test", submission=sub, extractions=[bill],
                         semantic=SemanticMapping(category_match=True, confidence=0.95),
                         expected={})
    case.member = m  # type: ignore[attr-defined]
    return case


def _stored(facts: SyntheticCase) -> tuple[dict, dict]:
    """Build the (stored_result_dict, submission_dict) a corrected claim starts from —
    exactly the shape persistence.get_claim / get_submission return. The decision is the
    REAL deterministic decision on these facts (no Gemini)."""
    pe = get_policy_engine(settings.policy_path)
    decision, _ = _decide(facts, pe)
    result = ClaimResult(
        claim_id=uuid.uuid4().hex, decision=decision,
        extractions=facts.extractions, semantic=facts.semantic,
        member=getattr(facts, "member", None),
    )
    return result.model_dump(mode="json"), facts.submission.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# Pure logic — no DB.                                                          #
# --------------------------------------------------------------------------- #

def test_correcting_total_flips_rejected_to_within_limit():
    """A bill misread as ₹9,000 (REJECTED, over the ₹5,000 per-claim limit) corrected
    to ₹1,000 re-decides to an APPROVED within-limit payout."""
    stored, submission = _stored(_facts("CONSULTATION", 9000.0))
    assert stored["decision"]["status"] == "REJECTED"

    new_result, summary = apply_correction(
        stored, submission,
        [{"file_id": FILE_ID, "field": "total_amount", "value": 1000.0}], actor="op1")

    assert summary["before"]["status"] == "REJECTED"
    assert summary["after"]["status"] in ("APPROVED", "PARTIAL")
    assert new_result.decision.status == summary["after"]["status"]
    # CONSULTATION network: 20% discount then 10% copay → 1000*0.8*0.9 = 720.
    assert new_result.decision.approved_amount == pytest.approx(720.0, abs=1.0)
    assert any(r["rule"] == "limits" for r in summary["changed_rules"])


def test_correcting_total_can_flip_approved_to_rejected():
    """Inverse direction: a within-limit total corrected UP past the per-claim limit
    flips an APPROVED claim to REJECTED."""
    stored, submission = _stored(_facts("CONSULTATION", 1000.0))
    assert stored["decision"]["status"] in ("APPROVED", "PARTIAL")
    new_result, summary = apply_correction(
        stored, submission,
        [{"file_id": FILE_ID, "field": "total_amount", "value": 9000.0}])
    assert summary["after"]["status"] == "REJECTED"


def test_correcting_patient_name_and_diagnosis_preserves_original_in_history():
    """Correcting non-financial string fields updates the stored extraction, bumps
    confidence to 1.0, and PRESERVES the original decision in correction_history."""
    stored, submission = _stored(_facts("CONSULTATION", 1000.0,
                                        patient="Rajsh Kumr", diagnosis="Fevr"))
    original_decision = dict(stored["decision"])

    new_result, summary = apply_correction(
        stored, submission,
        [{"file_id": FILE_ID, "field": "patient_name", "value": "Rajesh Kumar"},
         {"file_id": FILE_ID, "field": "diagnosis", "value": "Fever"}], actor="op2")

    ex = new_result.extractions[0]
    assert ex.patient_name.value == "Rajesh Kumar" and ex.patient_name.confidence == 1.0
    assert ex.diagnosis.value == "Fever" and ex.diagnosis.confidence == 1.0
    # Original decision preserved verbatim in append-only history.
    assert len(new_result.correction_history) == 1
    hist = new_result.correction_history[0]
    assert hist["before"]["status"] == original_decision["status"]
    assert hist["corrected_by"] == "op2"
    assert {c["field"] for c in hist["changed_fields"]} == {"patient_name", "diagnosis"}


def test_correcting_line_items_replaces_list_and_syncs_total():
    """Replacing the line-items table re-sums the bill total and re-decides on it."""
    stored, submission = _stored(_facts("CONSULTATION", 9000.0))
    new_result, summary = apply_correction(
        stored, submission,
        [{"file_id": FILE_ID, "field": "line_items",
          "value": [{"description": "Consult", "amount": 800.0},
                     {"description": "Meds", "amount": 200.0}]}])
    ex = new_result.extractions[0]
    assert len(ex.line_items) == 2
    assert ex.total_amount.value == pytest.approx(1000.0)
    assert summary["after"]["status"] in ("APPROVED", "PARTIAL")


def test_history_is_append_only_across_two_corrections():
    """A second correction never drops the first: history grows, oldest first."""
    stored, submission = _stored(_facts("CONSULTATION", 9000.0))
    r1, _ = apply_correction(stored, submission,
                             [{"file_id": FILE_ID, "field": "total_amount", "value": 1000.0}])
    r2, _ = apply_correction(r1.model_dump(mode="json"), submission,
                             [{"file_id": FILE_ID, "field": "total_amount", "value": 2000.0}])
    assert len(r2.correction_history) == 2


def test_unknown_file_id_and_field_raise():
    stored, submission = _stored(_facts("CONSULTATION", 1000.0))
    with pytest.raises(CorrectionError):
        apply_correction(stored, submission,
                         [{"file_id": "NOPE", "field": "total_amount", "value": 1.0}])
    with pytest.raises(CorrectionError):
        apply_correction(stored, submission,
                         [{"file_id": FILE_ID, "field": "not_a_field", "value": 1.0}])
    with pytest.raises(CorrectionError):
        apply_correction(stored, submission, [])


# --------------------------------------------------------------------------- #
# DB + endpoint — skip if Postgres unreachable.                               #
# --------------------------------------------------------------------------- #

def _db_reachable() -> bool:
    try:
        from app.services.persistence import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


db_required = pytest.mark.skipif(not _db_reachable(), reason="Postgres unreachable")


def _persist(stored: dict, submission: dict) -> str:
    """Insert a claim straight via save_claim so the correction endpoint can load it."""
    from app.services.persistence import init_db, save_claim
    init_db()
    sub = ClaimSubmission(**submission)
    result = ClaimResult(**stored)
    save_claim(sub, result)
    return result.claim_id


@db_required
def test_persisted_correction_updates_state_and_audit():
    """End-to-end (no HTTP): persist a REJECTED-over-limit claim, correct the total
    under the limit, and verify the STORED claim now reflects the corrected decision,
    the original is in correction_history, and an audit row was recorded."""
    from app.services.persistence import get_claim, get_submission, update_claim_result
    from app.services.audit import audit_trail

    stored, submission = _stored(_facts("CONSULTATION", 9000.0))
    claim_id = _persist(stored, submission)

    loaded = get_claim(claim_id)
    loaded_sub = get_submission(claim_id)
    new_result, summary = apply_correction(
        loaded, loaded_sub,
        [{"file_id": FILE_ID, "field": "total_amount", "value": 1000.0}], actor="auditor")
    assert update_claim_result(claim_id, new_result) is True

    from app.services.audit import record_correction
    from types import SimpleNamespace
    record_correction(claim_id,
                      SimpleNamespace(status=summary["before"]["status"],
                                      approved_amount=summary["before"]["amount"]),
                      SimpleNamespace(status=summary["after"]["status"],
                                      approved_amount=summary["after"]["amount"]),
                      [c["field"] for c in summary["changed_fields"]], actor="auditor")

    re_loaded = get_claim(claim_id)
    assert re_loaded["decision"]["status"] in ("APPROVED", "PARTIAL")
    assert re_loaded["corrected_by"] == "auditor"
    assert len(re_loaded["correction_history"]) == 1
    assert re_loaded["correction_history"][0]["before"]["status"] == "REJECTED"

    trail = audit_trail(claim_id)
    corr_rows = [r for r in trail if r["action"] == "CORRECTION"]
    assert corr_rows and corr_rows[-1]["actor"] == "auditor"
    assert "total_amount" in corr_rows[-1]["reason_codes"]["changed_fields"]


@db_required
def test_correct_endpoint_returns_before_after():
    """The TestClient endpoint (auth off → ops) returns before/after for a persisted
    claim and 404 for an unknown id. No Gemini."""
    from fastapi.testclient import TestClient
    from app.main import app

    stored, submission = _stored(_facts("CONSULTATION", 9000.0))
    claim_id = _persist(stored, submission)

    client = TestClient(app)
    resp = client.post(f"/api/claims/{claim_id}/correct", json={
        "corrections": [{"file_id": FILE_ID, "field": "total_amount", "value": 1000.0}]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["before"]["status"] == "REJECTED"
    assert body["after"]["status"] in ("APPROVED", "PARTIAL")
    assert body["after"]["amount"] == pytest.approx(720.0, abs=1.0)

    missing = client.post("/api/claims/does-not-exist-xyz/correct", json={
        "corrections": [{"file_id": FILE_ID, "field": "total_amount", "value": 1.0}]})
    assert missing.status_code == 404


@db_required
def test_member_cannot_correct_when_auth_enabled(monkeypatch):
    """With auth ON, a member token is rejected by require_ops (403); only ops correct."""
    from fastapi.testclient import TestClient
    from app.config import settings as cfg
    from app.services import auth as auth_mod
    from app.main import app

    stored, submission = _stored(_facts("CONSULTATION", 9000.0))
    claim_id = _persist(stored, submission)

    monkeypatch.setattr(cfg, "auth_enabled", True)
    member = auth_mod.Principal(username="mem", role="member", member_id="EMP001")
    token = auth_mod.make_token(member)

    client = TestClient(app)
    resp = client.post(f"/api/claims/{claim_id}/correct",
                       headers={"Authorization": f"Bearer {token}"},
                       json={"corrections": [{"file_id": FILE_ID, "field": "total_amount", "value": 1.0}]})
    assert resp.status_code == 403
