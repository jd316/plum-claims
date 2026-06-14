"""Sub-feature B: deterministic decision replay.

Proves the product thesis — "LLM proposes, deterministic code decides; same facts
→ same decision". Given a stored claim's LLM-proposed facts (extractions + semantic
mapping + resolved member), we re-run ONLY the deterministic layer (the 5 rule
agents + financial.calculate + aggregator.aggregate) with NO Gemini call, and check
the verdict + amount reproduce the originally stored decision.

The functions imported here are the SAME ones the live pipeline uses (app/graph/nodes
calls into these), so a match is a real reproduction, not a re-implementation.
"""
from __future__ import annotations

from app.config import settings
from app.models.schemas import (ClaimSubmission, ExtractionResult, SemanticMapping,
                                RuleVerdict)
from app.services.policy_engine import PolicyEngine
from app.rules import waiting_period, coverage_exclusion, pre_auth, limits, fraud
from app.rules.base import RuleContext
from app.rules.financial import calculate
from app.rules.aggregator import aggregate
from app.models.schemas import LineItem

# Same rule set + order the live pipeline runs (app/graph/nodes.RULES).
_RULES = {"waiting_period": waiting_period, "coverage_exclusion": coverage_exclusion,
          "pre_auth": pre_auth, "limits": limits, "fraud_anomaly": fraud}

_pe: PolicyEngine | None = None
def _engine() -> PolicyEngine:
    global _pe
    if _pe is None:
        _pe = PolicyEngine(settings.policy_path)
    return _pe


def replayable(stored: dict) -> bool:
    """A stored claim is replayable only if it carries the facts the rules need:
    a member, at least one extraction, and a non-blocked decision."""
    return bool(stored and not stored.get("blocked") and stored.get("decision")
                and stored.get("member") and stored.get("extractions"))


def _financial_items(extractions: list[ExtractionResult],
                     submission: ClaimSubmission) -> list[LineItem]:
    """Mirror app/graph/nodes.financial_calc's line-item selection / fallback EXACTLY
    so the replay reproduces the same payable amount."""
    items = [i for e in extractions if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL")
             for i in e.line_items]
    if not items:
        total = next((e.total_amount.value for e in extractions
                      if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL")
                      and e.total_amount.value), None)
        fallback = total or (submission.claimed_amount
                             if submission.claimed_amount and submission.claimed_amount > 0 else None)
        if fallback:
            items = [LineItem(description="Claimed amount", amount=float(fallback))]
    return items


def replay_decision(submission: ClaimSubmission, extractions: list[ExtractionResult],
                    semantic: SemanticMapping | None, member: dict) -> dict:
    """Re-run the deterministic decision from stored facts. NO Gemini. Returns
    {replayed_status, replayed_amount, replayed_trace_summary}."""
    pe = _engine()
    ctx = RuleContext(submission, member, extractions,
                      semantic or SemanticMapping(confidence=0.3), pe)

    verdicts: list[RuleVerdict] = []
    trace_summary: list[dict] = []
    for name, mod in _RULES.items():
        v = mod.check(ctx)
        verdicts.append(v)
        trace_summary.append({"rule": name, "status": v.status, "detail": v.detail[:160]})

    disallowed = [d for v in verdicts for d in v.disallowed_items]
    items = _financial_items(extractions, submission)
    hospital = submission.hospital_name or next(
        (e.hospital_name.value for e in extractions if e.hospital_name.value), None)
    fb = calculate(pe, submission.claim_category, pe.is_network(hospital), items, disallowed)

    decision = aggregate(verdicts, fb, pe.fraud_thresholds()["auto_manual_review_above"])
    trace_summary.append({"rule": "financial", "status": "PASS", "detail": " | ".join(fb.steps)[:200]})
    trace_summary.append({"rule": "aggregator", "status": decision.status,
                          "detail": f"approved ₹{decision.approved_amount:,.2f}"})
    return {"replayed_status": decision.status,
            "replayed_amount": decision.approved_amount,
            "replayed_trace_summary": trace_summary}


def replay_from_stored(stored: dict) -> dict:
    """Replay from a stored ClaimResult dict + its stored submission dict bundled in.
    `stored` must contain key 'submission' (the ClaimSubmission JSON) plus the
    ClaimResult fields. Returns the full comparison payload."""
    if not replayable(stored):
        return {"replayable": False,
                "reason": "This claim predates fact persistence (or was blocked) — "
                          "no stored extracted facts to replay deterministically."}

    submission = ClaimSubmission(**stored["submission"])
    extractions = [ExtractionResult(**e) for e in stored.get("extractions", [])]
    semantic = SemanticMapping(**stored["semantic"]) if stored.get("semantic") else None
    member = stored.get("member") or {}
    decision = stored["decision"]

    out = replay_decision(submission, extractions, semantic, member)
    original_status = decision.get("status")
    original_amount = decision.get("approved_amount")
    matches = (out["replayed_status"] == original_status
               and abs((out["replayed_amount"] or 0) - (original_amount or 0)) < 0.01)
    return {"replayable": True,
            "original_status": original_status,
            "replayed_status": out["replayed_status"],
            "original_amount": original_amount,
            "replayed_amount": out["replayed_amount"],
            "matches": matches,
            "replayed_trace_summary": out["replayed_trace_summary"]}
