from app.config import settings
from app.models.schemas import RuleVerdict
from app.rules.base import RuleContext
from app.services import fraud_signals

def check(ctx: RuleContext) -> RuleVerdict:
    th = ctx.pe.fraud_thresholds()
    refs = ["fraud_thresholds"]
    signals: list[str] = []
    tdate = ctx.submission.treatment_date
    # Category-mismatch routing (gated OFF; settings.category_match_enforcement_enabled).
    # When the semantic mapper is confident the documented treatment does not match the
    # filed claim_category, this is a mis-filing → route to MANUAL_REVIEW (a FLAG), never an
    # auto-reject. Default OFF and category_match defaults True, so the 12 cases are unaffected.
    if (settings.category_match_enforcement_enabled and ctx.semantic is not None
            and not ctx.semantic.category_match
            and ctx.semantic.confidence >= settings.category_match_min_confidence):
        mapped = ctx.semantic.mapped_category or "a different category"
        signals.append(f"filed as {ctx.submission.claim_category} but the documents look like "
                       f"{mapped} (category mismatch, confidence {ctx.semantic.confidence:.2f})")
    same_day = [h for h in ctx.submission.claims_history if h.date == tdate]
    if len(same_day) + 1 > th["same_day_claims_limit"]:
        signals.append(f"{len(same_day) + 1} claims on the same day ({tdate}) "
                       f"exceeds the limit of {th['same_day_claims_limit']}")
    # Monthly claims limit: count prior claims in the same calendar month as the treatment
    # date; +1 for the current claim. Exceeding the limit contributes a fraud signal (FLAG).
    monthly_limit = th.get("monthly_claims_limit")
    if monthly_limit is not None:
        same_month = [h for h in ctx.submission.claims_history
                      if h.date.year == tdate.year and h.date.month == tdate.month]
        if len(same_month) + 1 > monthly_limit:
            signals.append(f"{len(same_month) + 1} claims in {tdate:%Y-%m} exceeds the "
                           f"monthly limit of {monthly_limit}")
    if ctx.submission.claimed_amount > th["high_value_claim_threshold"]:
        signals.append(f"claim amount ₹{ctx.submission.claimed_amount:,.0f} exceeds the high-value "
                       f"threshold ₹{th['high_value_claim_threshold']:,.0f}")
    for e in ctx.extractions:
        if e.line_items and e.total_amount.value is not None:
            s = sum(i.amount for i in e.line_items)
            if abs(s - e.total_amount.value) > 1:
                signals.append(fraud_signals.tag(f"line items on {e.file_id} sum to ₹{s:,.0f} but "
                                                 f"total reads ₹{e.total_amount.value:,.0f}"))
        # Vision-reported anomalies, classified into a standardized code (e.g. DOCUMENT_ALTERATION)
        # so operators can filter by issue type — see app.services.fraud_signals.
        signals.extend(fraud_signals.tag(f"vision flag on {e.file_id}: {fs}") for fs in e.fraud_signals)
    # Reconcile the submitted claimed_amount against the extracted bill total. A member could
    # claim one amount while the supporting bill totals another; a material gap is a fraud signal.
    bill_total = next((e.total_amount.value for e in ctx.extractions
                       if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL")
                       and e.total_amount.value is not None), None)
    if bill_total is not None and abs(bill_total - ctx.submission.claimed_amount) > 1:
        signals.append(fraud_signals.tag(f"claimed amount ₹{ctx.submission.claimed_amount:,.0f} does "
                                         f"not match the bill total ₹{bill_total:,.0f}"))
    if signals:
        return RuleVerdict(rule="fraud_anomaly", status="FLAG", reason_code="FRAUD_SIGNALS",
            detail="Unusual pattern detected — routed to manual review. Signals: " + "; ".join(signals),
            policy_refs=refs, certainty=0.9)
    return RuleVerdict(rule="fraud_anomaly", status="PASS", detail="No fraud signals.", policy_refs=refs)
