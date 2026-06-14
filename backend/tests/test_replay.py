"""Sub-feature B: deterministic decision-replay tests (NOT live — no Gemini).

Builds a ClaimResult with stored extractions/semantic/member for a simple covered
CONSULTATION claim, runs the replay logic directly, and asserts the replayed verdict
reproduces the original — proving 'same facts → same decision'.
"""
from __future__ import annotations

from datetime import date

from app.models.schemas import (ClaimSubmission, ExtractionResult, SemanticMapping,
                                DocumentInput, DocumentQuality, StrField, NumField, LineItem)
from app.services.replay import replay_decision, replay_from_stored, replayable


def _engine_member() -> dict:
    """Resolve a real member from the policy so RuleContext has what it needs."""
    from app.services.replay import _engine
    return _engine().members()[0]


def _covered_consultation():
    member = _engine_member()
    name = member["name"]
    submission = ClaimSubmission(
        member_id=member["member_id"], policy_id="POL-1",
        claim_category="CONSULTATION", treatment_date=date(2026, 1, 15),
        claimed_amount=1500.0, hospital_name=None,
        documents=[DocumentInput(file_id="F001", file_name="bill.png", stored_path="/tmp/x.png")],
    )
    extractions = [
        ExtractionResult(
            file_id="F001", doc_type="HOSPITAL_BILL",
            quality=DocumentQuality(readable=True, overall_confidence=0.95),
            patient_name=StrField(value=name, confidence=0.95),
            line_items=[LineItem(description="Consultation fee", amount=1500.0)],
            total_amount=NumField(value=1500.0, confidence=0.95),
        ),
        ExtractionResult(
            file_id="F002", doc_type="PRESCRIPTION",
            quality=DocumentQuality(readable=True, overall_confidence=0.95),
            patient_name=StrField(value=name, confidence=0.95),
            doctor_name=StrField(value="Dr. A", confidence=0.9),
            doctor_registration=StrField(value="REG-123", confidence=0.9),
            diagnosis=StrField(value="Fever", confidence=0.9),
        ),
    ]
    semantic = SemanticMapping(category_match=True, mapped_category="CONSULTATION",
                               exclusion_candidates=[], waiting_condition=None, confidence=0.9)
    return submission, extractions, semantic, member


def test_replay_reproduces_covered_consultation():
    submission, extractions, semantic, member = _covered_consultation()

    # Original decision = the deterministic replay of these exact facts (this IS the
    # function the live pipeline's nodes call), so it stands in for the stored verdict.
    original = replay_decision(submission, extractions, semantic, member)
    assert original["replayed_status"] in ("APPROVED", "PARTIAL")
    assert original["replayed_amount"] > 0

    # Build a stored-result bundle and replay through the full from_stored path.
    bundled = {
        "claim_id": "CLM-REPLAY-TEST",
        "blocked": False,
        "decision": {"status": original["replayed_status"],
                     "approved_amount": original["replayed_amount"]},
        "extractions": [e.model_dump(mode="json") for e in extractions],
        "semantic": semantic.model_dump(mode="json"),
        "member": member,
        "submission": submission.model_dump(mode="json"),
    }
    assert replayable(bundled) is True

    out = replay_from_stored(bundled)
    assert out["replayable"] is True
    assert out["replayed_status"] == out["original_status"]
    assert out["matches"] is True
    assert out["replayed_amount"] == out["original_amount"]
    # Trace summary must include the rule + aggregator steps.
    rules = {t["rule"] for t in out["replayed_trace_summary"]}
    assert "aggregator" in rules and "financial" in rules


def test_replay_is_deterministic_across_runs():
    submission, extractions, semantic, member = _covered_consultation()
    a = replay_decision(submission, extractions, semantic, member)
    b = replay_decision(submission, extractions, semantic, member)
    assert a["replayed_status"] == b["replayed_status"]
    assert a["replayed_amount"] == b["replayed_amount"]


def test_older_record_without_facts_is_not_replayable():
    # No extractions / member → not replayable, clean 200-style payload.
    bundled = {"claim_id": "OLD", "blocked": False,
               "decision": {"status": "APPROVED", "approved_amount": 100.0},
               "submission": {}}
    assert replayable(bundled) is False
    out = replay_from_stored(bundled)
    assert out["replayable"] is False
    assert "reason" in out
