"""Deterministic verdict folding. Reason ranking surfaces the most informative member-facing
reason first: PERMANENT denials (EXCLUDED, NOT_COVERED) lead, because telling a member their
claim is "eligible from <date>" (waiting period) or "resubmit with pre-auth" is misleading when
the treatment is in fact never covered. Then the fixable/temporary reasons (PRE_AUTH, WAITING),
then limit codes. Eval matching uses containment, so extra (true) reasons never hurt."""
from app.models.schemas import RuleVerdict, FinancialBreakdown, Decision, ReasonCode, DecisionStatus

_PRIORITY = ["EXCLUDED_CONDITION", "NOT_COVERED", "PRE_AUTH_MISSING", "WAITING_PERIOD",
             "SUB_LIMIT_EXCEEDED", "PER_CLAIM_EXCEEDED", "FLOATER_LIMIT_EXCEEDED",
             "ANNUAL_LIMIT_EXCEEDED", "FRAUD_SIGNALS"]
def _rank(code: str) -> int:
    return _PRIORITY.index(code) if code in _PRIORITY else len(_PRIORITY)

def _zeroed(financial: FinancialBreakdown, reason: str) -> FinancialBreakdown:
    """A copy of the breakdown made consistent with a zero-payout outcome.

    The financial calculator always computes the payable amount for the covered line
    items; on a REJECTED (or zero-approval MANUAL_REVIEW) outcome the top-level
    approved_amount is 0, so the nested breakdown must agree — otherwise the verifier
    correctly flags the decision as internally inconsistent (top-level 0 vs nested >0).
    """
    f = financial.model_copy(deep=True)
    f.approved_amount = 0.0
    f.line_items = [l.model_copy(update={"approved": False, "reason": l.reason or reason})
                    for l in f.line_items]
    # Replace (not append to) the calculator's narrative: its "N/N items approved" /
    # "Approved amount: ₹X" steps describe the payable computation and would otherwise
    # contradict the zeroed outcome (the verifier flags that contradiction).
    f.steps = [f"Approved amount: ₹0.00 — {reason}; no payout on this claim."]
    return f

def aggregate(verdicts: list[RuleVerdict], financial: FinancialBreakdown | None,
              auto_review_above: float) -> Decision:
    # A None financial means the calculator never produced a breakdown (it failed upstream).
    # Treat it as a zeroed breakdown so we degrade gracefully instead of dereferencing None.
    if financial is None:
        financial = FinancialBreakdown(gross=0.0, approved_amount=0.0,
                                       steps=["financial calculation unavailable"])
    fails = sorted([v for v in verdicts if v.status == "FAIL"], key=lambda v: _rank(v.reason_code or ""))
    flags = [v for v in verdicts if v.status == "FLAG"]
    skipped = [v for v in verdicts if v.status == "SKIPPED"]
    reasons = [ReasonCode(code=v.reason_code or v.rule.upper(), detail=v.detail) for v in fails + flags][:4]
    recs = [f"Rule '{v.rule}' was skipped due to a component failure — manual review recommended."
            for v in skipped]
    if fails:
        return Decision(status="REJECTED", approved_amount=0.0, reason_codes=reasons,
                        member_message=fails[0].detail, recommendations=recs,
                        financial=_zeroed(financial, "claim rejected"))
    if flags:
        return Decision(status="MANUAL_REVIEW", approved_amount=0.0, reason_codes=reasons,
                        member_message="Your claim needs a quick manual check by our team. "
                                       "No action is needed from you right now.",
                        recommendations=recs + [f.detail for f in flags],
                        financial=_zeroed(financial, "pending manual review"))
    if financial.approved_amount > auto_review_above:
        return Decision(status="MANUAL_REVIEW", approved_amount=0.0,
                        reason_codes=[ReasonCode(code="HIGH_VALUE", detail="Above auto-approval ceiling")],
                        member_message="High-value claim routed for manual approval.",
                        recommendations=recs, financial=_zeroed(financial, "pending manual review"))
    # No payable amount could be computed (no line items and no usable total/claimed amount):
    # do NOT auto-approve ₹0 — route to manual review so a real bill with missing line-item
    # extraction isn't silently approved for nothing.
    if not financial.line_items and financial.approved_amount <= 0:
        return Decision(status="MANUAL_REVIEW", approved_amount=0.0,
                        reason_codes=[ReasonCode(code="NO_PAYABLE_AMOUNT",
                            detail="No claimable amount could be determined from the documents.")],
                        member_message="We couldn't determine a claimable amount from your documents. "
                                       "Your claim has been routed for manual review.",
                        recommendations=recs, financial=_zeroed(financial, "no payable amount"))
    partial = any(not l.approved for l in financial.line_items)
    status: DecisionStatus = "PARTIAL" if partial else "APPROVED"
    msg_bits = [f"Approved ₹{financial.approved_amount:,.2f}."]
    if partial:
        for l in financial.line_items:
            if not l.approved:
                msg_bits.append(f"'{l.description}' (₹{l.amount:,.0f}) was not approved: {l.reason}")
    return Decision(status=status, approved_amount=financial.approved_amount,
                    reason_codes=[ReasonCode(code="LINE_ITEM_EXCLUDED", detail=b) for b in msg_bits[1:]][:4],
                    member_message=" ".join(msg_bits), recommendations=recs, financial=financial)
