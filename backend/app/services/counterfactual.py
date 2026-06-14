"""Counterfactual explanations + a what-if simulator over the DETERMINISTIC layer.

Two explainability features built ENTIRELY on the deterministic decision (the 5 rule
agents + financial.calculate + aggregator.aggregate) — NO Gemini — so they are exact
and instant. Both are PURE / read-only: every helper operates on a COPY of the stored
facts and never mutates persisted data or touches the live pipeline.

  * `counterfactuals(facts)` — for a non-approved / partial claim, finds the MINIMAL
    change that would flip the decision, by actually PERTURBING the facts and
    RE-DECIDING (no hand-waving). Each item is honest about whether a flip is
    achievable (a hard exclusion has `achievable: false`).

  * `what_if(facts, overrides)` — applies operator overrides (claimed amount, network
    hospital, treatment date, category, per-line allow toggles, or a candidate policy)
    to a COPY of the facts, re-decides, and returns before/after + which rules changed.

`reconstruct_facts(stored)` rebuilds the structured facts object (submission +
extractions + semantic + member) from a stored ClaimResult so `decide_from_facts`
can run on it — the same shape the eval runner / replay use.
"""
from __future__ import annotations

import copy
from dataclasses import replace as _dc_replace
from datetime import date

from app.config import settings
from app.models.schemas import (ClaimSubmission, ExtractionResult, SemanticMapping,
                                LineItem, Decision, RuleVerdict)
from app.services.policy_engine import PolicyEngine, get_policy_engine
from app.rules import waiting_period, coverage_exclusion, pre_auth, limits, fraud
from app.rules.base import RuleContext
from app.rules.financial import calculate
from app.evalrunner.synthetic import SyntheticCase

# Same rule set + order the live pipeline runs (app/graph/nodes.RULES).
_RULES = [waiting_period, coverage_exclusion, pre_auth, limits, fraud]


# --------------------------------------------------------------------------- #
# Facts: a SyntheticCase carries exactly (submission + extractions + semantic) #
# that decide_from_facts needs; the member is resolved from the engine inside  #
# decide_from_facts via pe.member(submission.member_id). To re-decide on a      #
# stored claim whose member may NOT be on the engine roster, we resolve the     #
# member ourselves and run the rules directly (mirroring decide_from_facts).    #
# --------------------------------------------------------------------------- #

def _engine() -> PolicyEngine:
    return get_policy_engine(settings.policy_path)


def reconstruct_facts(stored: dict) -> SyntheticCase:
    """Rebuild a structured facts object (a SyntheticCase) from a stored ClaimResult
    dict bundled with its submission. `stored` must carry the ClaimResult fields plus a
    'submission' key (the ClaimSubmission JSON), exactly like replay's bundled dict.

    The stored `member` (resolved at decision time) is stashed on the case so we can
    re-decide even when that member isn't on the engine's live roster."""
    submission = ClaimSubmission(**stored["submission"])
    extractions = [ExtractionResult(**e) for e in stored.get("extractions", [])]
    semantic = (SemanticMapping(**stored["semantic"]) if stored.get("semantic")
                else SemanticMapping(confidence=0.3))
    member = stored.get("member") or {}
    case = SyntheticCase(
        case_id=stored.get("claim_id", "stored"), template="stored",
        submission=submission, extractions=extractions, semantic=semantic,
        expected={}, note="reconstructed from stored ClaimResult",
    )
    # Stash the resolved member so _decide can use it without the engine roster.
    case.member = member  # type: ignore[attr-defined]
    return case


def _member_for(case: SyntheticCase, pe: PolicyEngine) -> dict:
    """Resolve the member: prefer a stashed member (reconstructed facts), else the
    engine roster (synthetic cases). Lets us re-decide off-roster stored claims."""
    stashed = getattr(case, "member", None)
    if stashed:
        return stashed
    return pe.member(case.submission.member_id)


def _decide(case: SyntheticCase, pe: PolicyEngine) -> tuple[Decision, list[RuleVerdict]]:
    """Run the 5 rule checks + financial + aggregator EXACTLY as decide_from_facts /
    the pipeline's decide stage does, returning BOTH the Decision and the per-rule
    verdicts (the verdicts let what_if report which rules changed). No Gemini."""
    s = case.submission
    ctx = RuleContext(s, _member_for(case, pe), case.extractions,
                      case.semantic or SemanticMapping(confidence=0.3), pe)
    verdicts = [rule.check(ctx) for rule in _RULES]

    disallowed = [d for v in verdicts for d in v.disallowed_items]
    items = [i for e in case.extractions
             if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL") for i in e.line_items]
    if not items:
        total = next((e.total_amount.value for e in case.extractions
                      if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL")
                      and e.total_amount.value), None)
        fallback = total or (s.claimed_amount if s.claimed_amount and s.claimed_amount > 0 else None)
        if fallback:
            items = [LineItem(description="Claimed amount", amount=float(fallback))]
    hospital = s.hospital_name or next((e.hospital_name.value for e in case.extractions
                                        if e.hospital_name.value), None)
    financial = calculate(pe, s.claim_category, pe.is_network(hospital), items, disallowed)
    from app.rules.aggregator import aggregate
    decision = aggregate(verdicts, financial, pe.fraud_thresholds()["auto_manual_review_above"])
    return decision, verdicts


def _clone(case: SyntheticCase) -> SyntheticCase:
    """A deep copy of a facts object so any perturbation is local and can never
    mutate the caller's (stored) data. The stashed member is copied too."""
    semantic = (case.semantic or SemanticMapping(confidence=0.3)).model_copy(deep=True)
    new = _dc_replace(
        case,
        submission=case.submission.model_copy(deep=True),
        extractions=[e.model_copy(deep=True) for e in case.extractions],
        semantic=semantic,
    )
    stashed = getattr(case, "member", None)
    if stashed is not None:
        new.member = copy.deepcopy(stashed)  # type: ignore[attr-defined]
    return new


# --------------------------------------------------------------------------- #
# Counterfactuals                                                              #
# --------------------------------------------------------------------------- #

def _verdict(verdicts: list[RuleVerdict], rule: str) -> RuleVerdict | None:
    return next((v for v in verdicts if v.rule == rule), None)


def _set_claimed(case: SyntheticCase, amount: float) -> SyntheticCase:
    """Return a clone with the claimed amount AND the single-line bill set to `amount`,
    so limits (reads claimed_amount) and financial (reads line items) both see it.
    Only rewrites a single covered line (the common case the counterfactuals target);
    multi-line bills are left to what_if's per-line controls."""
    c = _clone(case)
    c.submission.claimed_amount = round(amount, 2)
    for e in c.extractions:
        if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL") and len(e.line_items) == 1:
            e.line_items[0].amount = round(amount, 2)
            e.total_amount.value = round(amount, 2)
    return c


def counterfactuals(case: SyntheticCase, pe: PolicyEngine | None = None) -> list[dict]:
    """For the relevant rejection / partial reasons on this claim, compute concrete
    flips by REAL re-decides. Each item:
        {reason, change, resulting_decision, resulting_amount, achievable: bool}.
    An APPROVED / PARTIAL claim yields []. Hard exclusions yield achievable: false."""
    pe = pe or _engine()
    decision, verdicts = _decide(case, pe)
    if decision.status in ("APPROVED", "PARTIAL"):
        return []

    out: list[dict] = []
    codes = {c.code for c in decision.reason_codes}

    # --- per-claim / sub-limit: the max amount that would pass + its payout ------
    lim = _verdict(verdicts, "limits")
    if lim and lim.status == "FAIL" and lim.reason_code in ("PER_CLAIM_EXCEEDED", "SUB_LIMIT_EXCEEDED"):
        cat = case.submission.claim_category
        limit = (pe.per_claim_limit() if cat == "CONSULTATION"
                 else pe.category_rules(cat)["sub_limit"])
        probe = _set_claimed(case, float(limit))
        d2, _ = _decide(probe, pe)
        achievable = d2.status in ("APPROVED", "PARTIAL")
        out.append({
            "reason": lim.reason_code,
            "change": (f"Reduce the claimed amount to ₹{limit:,.0f} "
                       f"(the {'per-claim' if cat == 'CONSULTATION' else cat + ' sub'}-limit)"),
            "resulting_decision": d2.status,
            "resulting_amount": d2.approved_amount,
            "achievable": achievable,
        })

    # --- waiting period: the eligible-from date (parsed from the verdict detail) -
    wp = _verdict(verdicts, "waiting_period")
    if wp and wp.status == "FAIL" and wp.reason_code == "WAITING_PERIOD":
        eligible = _eligible_date(wp.detail)
        change = (f"Submit on or after {eligible} (the eligible-from date)"
                  if eligible else "Submit once the waiting period has elapsed")
        achievable = False
        d_status = decision.status
        d_amount = decision.approved_amount
        if eligible:
            probe = _clone(case)
            probe.submission.treatment_date = eligible
            d2, _ = _decide(probe, pe)
            achievable = d2.status in ("APPROVED", "PARTIAL")
            d_status, d_amount = d2.status, d2.approved_amount
        out.append({
            "reason": "WAITING_PERIOD", "change": change,
            "resulting_decision": d_status, "resulting_amount": d_amount,
            "achievable": achievable,
        })

    # --- pre-auth missing: obtain pre-auth + the would-be payout if approved -----
    pa = _verdict(verdicts, "pre_auth")
    if pa and pa.status == "FAIL" and pa.reason_code == "PRE_AUTH_MISSING":
        # The would-be payout: re-decide with pre_auth neutralised (drop the rule),
        # so the member sees what they'd receive once pre-auth is obtained.
        d_payout = _decide_without(case, pe, drop="pre_auth")
        achievable = d_payout.status in ("APPROVED", "PARTIAL", "MANUAL_REVIEW")
        out.append({
            "reason": "PRE_AUTH_MISSING",
            "change": "Obtain pre-authorization from the insurer and resubmit this claim",
            "resulting_decision": d_payout.status,
            "resulting_amount": d_payout.approved_amount,
            "achievable": achievable,
        })

    # --- excluded / not covered: HARD exclusion — honest, no fake approval -------
    ce = _verdict(verdicts, "coverage_exclusion")
    if ce and ce.status == "FAIL" and ce.reason_code in ("EXCLUDED_CONDITION", "NOT_COVERED"):
        out.append({
            "reason": ce.reason_code,
            "change": ("This is a hard policy exclusion — no change to the claim can "
                       "make it eligible under the current policy."),
            "resulting_decision": "REJECTED", "resulting_amount": 0.0,
            "achievable": False,
        })

    # --- high-value / fraud manual review: amount under the ceiling auto-approves -
    if "HIGH_VALUE" in codes:
        ceiling = pe.fraud_thresholds()["auto_manual_review_above"]
        out.append({
            "reason": "HIGH_VALUE",
            "change": (f"A claim under ₹{ceiling:,.0f} auto-approves (this one is "
                       f"above the high-value ceiling and needs manual sign-off)"),
            "resulting_decision": "APPROVED", "resulting_amount": None,
            "achievable": True,
        })
    fr = _verdict(verdicts, "fraud_anomaly")
    if fr and fr.status == "FLAG" and "HIGH_VALUE" not in codes:
        out.append({
            "reason": fr.reason_code or "FRAUD_SIGNALS",
            "change": ("Flagged for a manual fraud check — resolving the flagged "
                       "signal(s) clears the review (no amount change auto-approves it)."),
            "resulting_decision": "MANUAL_REVIEW", "resulting_amount": 0.0,
            "achievable": False,
        })

    return out


def _decide_without(case: SyntheticCase, pe: PolicyEngine, drop: str) -> Decision:
    """Re-decide with ONE rule dropped (used to show the would-be payout once a
    blocking-but-correctable rule, e.g. pre_auth, is satisfied)."""
    s = case.submission
    ctx = RuleContext(s, _member_for(case, pe), case.extractions,
                      case.semantic or SemanticMapping(confidence=0.3), pe)
    verdicts = [rule.check(ctx) for rule in _RULES if rule.__name__.split(".")[-1] != drop]
    disallowed = [d for v in verdicts for d in v.disallowed_items]
    items = [i for e in case.extractions
             if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL") for i in e.line_items]
    if not items and s.claimed_amount and s.claimed_amount > 0:
        items = [LineItem(description="Claimed amount", amount=float(s.claimed_amount))]
    hospital = s.hospital_name or next((e.hospital_name.value for e in case.extractions
                                        if e.hospital_name.value), None)
    financial = calculate(pe, s.claim_category, pe.is_network(hospital), items, disallowed)
    from app.rules.aggregator import aggregate
    return aggregate(verdicts, financial, pe.fraud_thresholds()["auto_manual_review_above"])


def _eligible_date(detail: str) -> date | None:
    """Pull the eligible-from ISO date out of a waiting_period verdict detail. The rule
    phrasings end '... eligible from YYYY-MM-DD.' / '... claims from YYYY-MM-DD.'."""
    import re
    m = re.findall(r"(\d{4}-\d{2}-\d{2})", detail or "")
    # The LAST date in the detail is always the eligible-from date in both phrasings.
    if not m:
        return None
    try:
        return date.fromisoformat(m[-1])
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# What-if simulator                                                           #
# --------------------------------------------------------------------------- #

def _apply_overrides(case: SyntheticCase, overrides: dict, pe: PolicyEngine) -> SyntheticCase:
    """Apply ops overrides to a CLONE of the facts. Supported keys:
        claimed_amount, hospital_name, is_network (bool → network/non-network hospital),
        treatment_date (ISO str | date), category (== claim_category),
        line_allow ({index: bool} toggles per covered line),
    Returns the perturbed clone; never mutates the input."""
    c = _clone(case)
    s = c.submission

    if "claimed_amount" in overrides and overrides["claimed_amount"] is not None:
        amt = float(overrides["claimed_amount"])
        s.claimed_amount = round(amt, 2)
        for e in c.extractions:
            if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL") and len(e.line_items) == 1:
                e.line_items[0].amount = round(amt, 2)
                e.total_amount.value = round(amt, 2)

    if "category" in overrides and overrides["category"]:
        s.claim_category = overrides["category"]

    if "treatment_date" in overrides and overrides["treatment_date"]:
        td = overrides["treatment_date"]
        s.treatment_date = td if isinstance(td, date) else date.fromisoformat(str(td))

    # Network: an explicit hospital_name wins; else is_network toggles a known
    # network / non-network hospital so pe.is_network() flips deterministically.
    if "hospital_name" in overrides and overrides["hospital_name"] is not None:
        s.hospital_name = overrides["hospital_name"] or None
        for e in c.extractions:
            e.hospital_name.value = s.hospital_name
    elif "is_network" in overrides and overrides["is_network"] is not None:
        from app.evalrunner.synthetic import NETWORK_HOSPITAL, NON_NETWORK_HOSPITAL
        s.hospital_name = NETWORK_HOSPITAL if overrides["is_network"] else NON_NETWORK_HOSPITAL
        for e in c.extractions:
            e.hospital_name.value = s.hospital_name

    # Per-line allow toggles: {line_index: bool}. A False marks the line excluded by
    # renaming its description into the category's excluded set is NOT robust, so we
    # instead drop disallowed lines from the bill entirely — the simplest honest
    # representation of "ops decided this line isn't payable".
    if "line_allow" in overrides and isinstance(overrides["line_allow"], dict):
        toggles = {int(k): bool(v) for k, v in overrides["line_allow"].items()}
        for e in c.extractions:
            if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL"):
                kept = [li for idx, li in enumerate(e.line_items) if toggles.get(idx, True)]
                e.line_items = kept
                e.total_amount.value = round(sum(li.amount for li in kept), 2)
        s.claimed_amount = round(sum(li.amount for e in c.extractions
                                     if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL")
                                     for li in e.line_items), 2) or s.claimed_amount

    return c


def _summary(decision: Decision) -> dict:
    return {
        "status": decision.status,
        "approved_amount": decision.approved_amount,
        "reason_codes": [{"code": c.code, "detail": c.detail} for c in decision.reason_codes],
    }


def _changed_rules(before: list[RuleVerdict], after: list[RuleVerdict]) -> list[dict]:
    """Which rules changed verdict (status or reason_code) between before / after."""
    by_after = {v.rule: v for v in after}
    out: list[dict] = []
    for b in before:
        a = by_after.get(b.rule)
        if a and (a.status != b.status or a.reason_code != b.reason_code):
            out.append({"rule": b.rule,
                        "before": {"status": b.status, "reason_code": b.reason_code},
                        "after": {"status": a.status, "reason_code": a.reason_code}})
    return out


def what_if(case: SyntheticCase, overrides: dict,
            pe: PolicyEngine | None = None,
            candidate_policy: dict | None = None) -> dict:
    """Apply `overrides` to a COPY of the facts, re-decide, and return before/after
    decisions + amounts + which rules changed verdict. Pure / deterministic; never
    mutates stored data. An optional `candidate_policy` previews the decision under a
    different policy (via a throwaway engine) — the policy_store preview path."""
    pe = pe or _engine()
    before, before_v = _decide(case, pe)

    after_case = _apply_overrides(case, overrides or {}, pe)
    if candidate_policy is not None:
        from app.services.policy_store import _candidate_engine
        after_pe = _candidate_engine(candidate_policy)
    else:
        after_pe = pe
    after, after_v = _decide(after_case, after_pe)

    before_s, after_s = _summary(before), _summary(after)
    return {
        "before": before_s,
        "after": after_s,
        "diff": {
            "status_changed": before_s["status"] != after_s["status"],
            "amount_delta": round(after.approved_amount - before.approved_amount, 2),
            "changed_rules": _changed_rules(before_v, after_v),
        },
    }
