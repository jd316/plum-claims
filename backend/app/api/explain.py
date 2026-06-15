import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.services.auth import Principal
from app.deps_auth import require_user, require_ops, require_owner_or_ops
from app.services import persistence
from app.api.common import _reconstructed_facts_or_404

log = logging.getLogger("plum.claims")

router = APIRouter()


@router.post("/api/claims/{claim_id}/replay")
def claim_replay(claim_id: str, user: Principal = Depends(require_user)):
    """Sub-feature B: re-run the DETERMINISTIC decision from the stored extracted
    facts (no Gemini) and report whether it reproduces the original verdict —
    proving 'same facts → same decision'. Older records without stored facts
    return {replayable: false} with a 200 (not an error: replay is best-effort)."""
    from app.services.replay import replay_from_stored
    result = persistence.get_claim(claim_id)
    submission = persistence.get_submission(claim_id)
    if result is None or submission is None:
        raise HTTPException(404, "claim not found")
    require_owner_or_ops(submission.get("member_id"), user)
    # Bundle the stored submission into the result dict the way replay expects.
    bundled = {**result, "submission": submission}
    return replay_from_stored(bundled)


# ---------------------------------------------------------------------------
# Explainability: counterfactuals + a what-if simulator. Both run ENTIRELY on
# the DETERMINISTIC layer (no Gemini) over the stored facts, so they are exact
# and instant, and never touch the live pipeline or the 12 cases. Read-only.
# ---------------------------------------------------------------------------

@router.get("/api/claims/{claim_id}/counterfactuals")
def claim_counterfactuals(claim_id: str, user: Principal = Depends(require_user)):
    """Counterfactual explanations: for a non-approved / partial claim, the MINIMAL
    changes that would flip the decision, computed by REAL deterministic re-decides
    (no Gemini). APPROVED/PARTIAL claims return []. Hard exclusions are honest
    (achievable: false)."""
    from app.services.counterfactual import counterfactuals
    from app.services.policy_engine import get_policy_engine
    facts, member_id = _reconstructed_facts_or_404(claim_id)
    require_owner_or_ops(member_id, user)
    s = facts.submission
    pe = get_policy_engine(settings.policy_path)
    # The base facts let the what-if UI seed its controls so "before" == the stored
    # decision (the operator changes only what they intend to).
    base = {"claimed_amount": s.claimed_amount,
            "treatment_date": s.treatment_date.isoformat(),
            "is_network": pe.is_network(s.hospital_name),
            "category": s.claim_category}
    return {"claim_id": claim_id, "base": base,
            "counterfactuals": counterfactuals(facts)}


class WhatIfRequest(BaseModel):
    claimed_amount: float | None = None
    hospital_name: str | None = None
    is_network: bool | None = None
    treatment_date: str | None = None
    category: str | None = None
    line_allow: dict[str, bool] | None = None
    candidate_policy: dict | None = None


@router.post("/api/claims/{claim_id}/what-if")
def claim_what_if(claim_id: str, body: WhatIfRequest,
                  user: Principal = Depends(require_user)):
    """What-if simulator (ops): apply overrides (claimed amount, network toggle,
    treatment date, category, per-line toggles, or a candidate policy) to a COPY of
    the stored facts, re-decide deterministically, and return before/after + which
    rules changed. Pure / read-only; never mutates stored data; no Gemini."""
    from app.services.counterfactual import what_if
    facts, member_id = _reconstructed_facts_or_404(claim_id)
    require_owner_or_ops(member_id, user)
    overrides = body.model_dump(exclude_none=True)
    candidate = overrides.pop("candidate_policy", None)
    return what_if(facts, overrides, candidate_policy=candidate)


@router.get("/api/claims/{claim_id}/audit")
def claim_audit(claim_id: str, user: Principal = Depends(require_ops)):
    """Ops-only: the append-only audit trail (decision + correction history) for a
    claim, oldest first. Tolerant of a missing audit_log table/row → returns []."""
    try:
        from app.services.audit import audit_trail
        return audit_trail(claim_id)
    except Exception as e:  # noqa: BLE001 — degrade to empty, never 500
        log.warning("claim_audit failed for %s; returning []: %s", claim_id, e)
        return []
