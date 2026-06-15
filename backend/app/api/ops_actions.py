import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.auth import Principal
from app.deps_auth import require_ops
from app.services import persistence

log = logging.getLogger("plum.claims")

router = APIRouter()


# ---------------------------------------------------------------------------
# Ops inline field correction. An operator corrects a low-confidence EXTRACTED
# field; the DETERMINISTIC decision is re-run on the corrected facts (no Gemini)
# and the corrected outcome is PERSISTED with an append-only audit trail (the
# ORIGINAL decision is preserved in correction_history). Ops-only.
# ---------------------------------------------------------------------------

class FieldCorrection(BaseModel):
    file_id: str
    field: str  # "total_amount" | "patient_name" | "diagnosis" | "line_items" | ...
    value: object | None = None


class CorrectRequest(BaseModel):
    corrections: list[FieldCorrection]
    actor: str | None = None


@router.post("/api/claims/{claim_id}/correct")
def claim_correct(claim_id: str, body: CorrectRequest,
                  user: Principal = Depends(require_ops)):
    """Ops inline field correction: apply operator corrections to the stored
    extracted facts, RE-RUN the deterministic decision (no Gemini), and PERSIST the
    corrected outcome. The corrected decision + extractions become the new state; the
    ORIGINAL decision is appended to correction_history (never lost) and an audit row
    records the actor + changed fields + before→after. Ops-only (open when auth off).
    Returns {before, after, changed_fields, changed_rules}. 404 unknown claim;
    422 for an unknown document/field or a malformed value."""
    from app.services.correction import apply_correction, CorrectionError
    result = persistence.get_claim(claim_id)
    submission = persistence.get_submission(claim_id)
    if result is None or submission is None:
        raise HTTPException(404, "claim not found")
    actor = body.actor or user.username
    corrections = [c.model_dump() for c in body.corrections]
    try:
        new_result, summary = apply_correction(result, submission, corrections, actor=actor)
    except CorrectionError as e:
        raise HTTPException(422, detail=str(e))

    # Persist the corrected state in place (the submission is unchanged). Best-effort
    # like the decide path: a persistence failure is surfaced (the correction is not
    # silently dropped) but the recomputed before/after is still returned.
    persisted = False
    try:
        persisted = persistence.update_claim_result(claim_id, new_result)
    except Exception as e:  # noqa: BLE001
        log.warning("update_claim_result failed for %s (correction not persisted): %s",
                    claim_id, e)
    # Audit row: actor + which fields changed + original→new decision (non-PHI). The
    # SimpleNamespace adapters expose .status/.approved_amount for record_correction.
    try:
        from types import SimpleNamespace
        from app.services.audit import record_correction
        before_ns = SimpleNamespace(status=summary["before"]["status"],
                                    approved_amount=summary["before"]["amount"])
        after_ns = SimpleNamespace(status=summary["after"]["status"],
                                   approved_amount=summary["after"]["amount"])
        record_correction(claim_id, before_ns, after_ns,
                          [c["field"] for c in summary["changed_fields"]], actor=actor)
    except Exception as e:  # noqa: BLE001
        log.warning("record_correction failed for %s (non-blocking): %s", claim_id, e)
    return {**summary, "persisted": persisted}


# ---------------------------------------------------------------------------
# Operator FINAL decision (human-in-the-loop). The AI auto-adjudicates; for a
# MANUAL_REVIEW (e.g. a fraud flag) — or to override the AI — a human operator
# makes the final call. Sets the decision, PERSISTS it, and records an append-only
# audit row (actor + note + AI→operator status/amount). Ops-only.
# ---------------------------------------------------------------------------

class OperatorDecisionRequest(BaseModel):
    status: Literal["APPROVED", "PARTIAL", "REJECTED"]
    approved_amount: float | None = None
    note: str


@router.post("/api/claims/{claim_id}/decision")
def claim_operator_decision(claim_id: str, body: OperatorDecisionRequest,
                            user: Principal = Depends(require_ops)):
    """An operator's final human decision on a claim — resolve a MANUAL_REVIEW or override
    the AI outcome. Sets the decision, persists it, and writes an append-only audit row.
    The note is REQUIRED (the decision rationale). Ops-only (open when auth off).
    404 unknown claim; 409 a blocked claim (no decision to resolve); 422 empty note."""
    from app.models.schemas import ClaimResult, ReasonCode
    stored = persistence.get_claim(claim_id)
    if stored is None:
        raise HTTPException(404, "claim not found")
    # The note is operator free-text persisted in the (PHI-minimized, retention-surviving)
    # audit log — bound its length so it can't become a large PHI sink.
    note = (body.note or "").strip()[:2000]
    if not note:
        raise HTTPException(422, "a decision note is required")
    result = ClaimResult.model_validate(stored)
    if result.blocked or result.decision is None:
        raise HTTPException(409, "this claim is blocked on a document problem and has no "
                                 "decision to resolve — the member must re-submit valid documents")
    prior = result.decision
    new_amount = 0.0 if body.status == "REJECTED" else (
        body.approved_amount if body.approved_amount is not None else prior.approved_amount)
    member_msg = {
        "APPROVED": f"Approved by our team after review. ₹{new_amount:,.2f}.",
        "PARTIAL": f"Partially approved by our team after review. ₹{new_amount:,.2f}.",
        "REJECTED": "Reviewed by our team — this claim was not approved.",
    }[body.status]
    actor = user.username
    # Reconcile the financial breakdown so it doesn't contradict the human decision
    # (e.g. a MANUAL_REVIEW breakdown is zeroed "pending review" — make it match the
    # operator's approved amount, with a step that names the override).
    new_financial = prior.financial
    if new_financial is not None:
        new_financial = new_financial.model_copy(update={
            "approved_amount": new_amount,
            "steps": [f"Operator decision ({actor}): {body.status} — ₹{new_amount:,.2f}."],
        })
    new_decision = prior.model_copy(update={
        "status": body.status,
        "approved_amount": new_amount,
        "reason_codes": [ReasonCode(code="OPERATOR_DECISION", detail=note)],
        "member_message": member_msg,
        "confidence": 1.0,        # a human made the call
        "recommendations": [],
        "financial": new_financial,
    })
    now = datetime.now(timezone.utc).isoformat()
    before = {"status": prior.status, "amount": prior.approved_amount}
    after = {"status": body.status, "amount": new_amount}
    new_result = result.model_copy(update={
        "decision": new_decision, "decided_by": actor, "decided_at": now,
        "correction_history": result.correction_history + [
            {"action": "operator_decision", "by": actor, "at": now, "note": note,
             "before": before, "after": after}],
    })
    persisted = False
    try:
        persisted = persistence.update_claim_result(claim_id, new_result)
    except Exception as e:  # noqa: BLE001 — a completed decision is not silently dropped
        log.warning("update_claim_result failed for %s (operator decision not persisted): %s",
                    claim_id, e)
    try:
        from types import SimpleNamespace
        from app.services.audit import record_operator_decision
        record_operator_decision(
            claim_id,
            SimpleNamespace(status=prior.status, approved_amount=prior.approved_amount),
            SimpleNamespace(status=body.status, approved_amount=new_amount),
            note, actor=actor)
    except Exception as e:  # noqa: BLE001 — auditing must never block the response
        log.warning("record_operator_decision failed for %s (non-blocking): %s", claim_id, e)
    return {"claim_id": claim_id, "before": before, "after": after,
            "decided_by": actor, "decided_at": now, "persisted": persisted,
            "decision": new_decision.model_dump(mode="json")}


class MarkOutcomeRequest(BaseModel):
    # Was the AUTOMATED decision correct (operator's ground-truth label)?
    correct: bool


@router.post("/api/claims/{claim_id}/mark-outcome")
def claim_mark_outcome(claim_id: str, body: MarkOutcomeRequest,
                       user: Principal = Depends(require_ops)):
    """Ops-only: label whether the system's automated decision on this claim was correct.
    This is the RIGHT training signal for confidence calibration / conformal risk control —
    operator agreement on the final decision, not extraction-field accuracy. We store the
    decision's own confidence alongside the boolean so the recalibration job can fit on
    (confidence, correct) pairs. 404 unknown claim; 409 a blocked claim (no decision)."""
    from app.models.schemas import ClaimResult
    stored = persistence.get_claim(claim_id)
    if stored is None:
        raise HTTPException(404, "claim not found")
    result = ClaimResult.model_validate(stored)
    if result.blocked or result.decision is None:
        raise HTTPException(409, "this claim has no automated decision to label")
    from app.services.audit import record_outcome_label
    row_id = record_outcome_label(claim_id, confidence=result.decision.confidence,
                                  correct=body.correct, decision_status=result.decision.status,
                                  actor=user.username)
    return {"claim_id": claim_id, "labeled": True, "correct": body.correct,
            "confidence": result.decision.confidence, "audit_row": row_id}


# ---------------------------------------------------------------------------
# Ops dashboard — additive, read-only analytics over the persisted claims.
# These never touch the decision pipeline or the 12 eval cases; they are pure
# projections of the `claims` table. Ops-only when auth is enabled (require_ops);
# with auth off (default) they are open like the rest of the API.
# ---------------------------------------------------------------------------

@router.get("/api/ops/analytics")
def ops_analytics(user: Principal = Depends(require_ops)):
    """Summary metrics for the ops dashboard: counts by status, approval / blocked /
    manual-review rates, total + average approved amount, average confidence, fraud
    (MANUAL_REVIEW) count, est. total cost + average latency, and a by-category
    breakdown. Tolerant of an empty/unavailable DB → all-zero shape (never 500s)."""
    try:
        return persistence.analytics_summary()
    except Exception as e:  # noqa: BLE001 — dashboard must degrade, not 500
        log.warning("ops_analytics failed; returning empty shape: %s", e)
        return {"total_claims": 0, "by_status": {}, "decided_count": 0,
                "blocked_count": 0, "flagged_fraud_count": 0, "approval_rate": 0.0,
                "blocked_rate": 0.0, "manual_review_rate": 0.0,
                "total_approved_amount": 0.0, "avg_approved_amount": 0.0,
                "avg_confidence": 0.0, "estimated_total_cost_inr": 0.0,
                "avg_latency_ms": 0, "by_category": []}


@router.get("/api/ops/worklist")
def ops_worklist(status: str | None = None, category: str | None = None,
                 q: str | None = None, sort: str = "created_at",
                 user: Principal = Depends(require_ops)):
    """Filtered/sorted claim queue. Filters: status, category, q (member id / claim
    id substring). Sort: created_at | amount | confidence (all desc). Each row carries
    a `needs_review` flag (MANUAL_REVIEW or blocked) so the queue can prioritize."""
    return persistence.worklist(status=status, category=category, q=q, sort=sort)


@router.get("/api/ops/fraud")
def ops_fraud(user: Principal = Depends(require_ops)):
    """Claims flagged for fraud review (MANUAL_REVIEW), each annotated with its fraud
    signals (reason codes, recommendations, extraction fraud_signals, fraud rule)."""
    return persistence.fraud_queue()


@router.get("/api/ops/improvement-proposals")
def ops_improvement_proposals(user: Principal = Depends(require_ops)):
    """System self-assessment (ADVISORY ONLY): reads the system's own eval outputs
    (decision eval, extraction robustness, calibration ECE, confidence config) and
    returns {findings, proposals}. Nothing here changes a prompt, threshold, weight,
    or decision — `auto_applicable` on each proposal is informational. Read-only; the
    decision pipeline and the 12 cases are untouched. Degrades to an error shape rather
    than 500."""
    from app.services import self_improve
    try:
        findings = self_improve.analyze()
        proposals = self_improve.propose(findings)  # deterministic, no Gemini
        return {"findings": findings,
                "proposals": [p.to_dict() for p in proposals]}
    except Exception as e:  # noqa: BLE001 — self-assessment must degrade, not 500
        log.warning("improvement-proposals failed: %s", e)
        return {"findings": {}, "proposals": [], "error": str(e)}
