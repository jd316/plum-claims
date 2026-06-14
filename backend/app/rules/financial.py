"""Pure financial arithmetic. ORDER IS CRITICAL: network discount FIRST, then co-pay.
The LLM never touches these numbers.

Arithmetic is done in decimal.Decimal (quantized to 2 dp, ROUND_HALF_UP, via
app.services.money) so there is no float rounding drift across the
gross → discount → co-pay → cap chain; values are converted to float only on the
FinancialBreakdown at the edge."""
from app.config import settings
from app.models.schemas import FinancialBreakdown, LineItem, LineItemDecision
from app.services.money import D, money, to_float
from app.services.policy_engine import PolicyEngine

def calculate(pe: PolicyEngine, category: str, is_network: bool,
              items: list[LineItem], disallowed: list[str]) -> FinancialBreakdown:
    cat = pe.category_rules(category)
    steps: list[str] = []
    decisions: list[LineItemDecision] = []
    gross = D(0)
    dis = {d.lower() for d in disallowed}
    for it in items:
        ok = it.description.lower() not in dis
        decisions.append(LineItemDecision(description=it.description, amount=it.amount, approved=ok,
            reason=None if ok else "Excluded procedure under policy (cosmetic/not covered)"))
        if ok: gross += D(it.amount)
    gross = money(gross)  # exact 2-dp covered total before any arithmetic
    steps.append(f"Covered line items total ₹{gross:,.2f} "
                 f"({sum(1 for d in decisions if d.approved)}/{len(decisions)} items approved)")
    disc_pct = D(cat.get("network_discount_percent", 0)) if is_network else D(0)
    disc_amt = money(gross * disc_pct / 100)
    post = money(gross - disc_amt)  # post-discount base for co-pay
    if disc_pct: steps.append(f"Network discount {disc_pct:.0f}% applied first: −₹{disc_amt:,.2f} → ₹{post:,.2f}")
    base_copay_pct = D(cat.get("copay_percent", 0))
    branded_copay_pct = D(cat.get("branded_drug_copay_percent", 0))
    # PHARMACY branded co-pay: branded drug lines carry branded_drug_copay_percent (30%);
    # generic / unknown lines keep the category copay (0%). We apply each line's copay to
    # its OWN post-discount portion (discount→copay order preserved), then report the
    # blended rate. For non-pharmacy, or pharmacy bills with no branded flags, this
    # collapses to the flat category copay — identical to the prior behaviour.
    branded_present = (category == "PHARMACY" and branded_copay_pct
                       and any(i.is_branded for i in items if i.description.lower() not in dis))
    if branded_present:
        copay_amt = D(0)
        for it in items:
            if it.description.lower() in dis:
                continue
            line_post = money(D(it.amount) * (1 - disc_pct / 100))
            rate = branded_copay_pct if it.is_branded else base_copay_pct
            copay_amt += money(line_post * rate / 100)
        copay_amt = money(copay_amt)
        copay_pct = money(copay_amt / post * 100) if post else D(0)
        steps.append(f"Pharmacy co-pay: {branded_copay_pct:.0f}% on branded lines, "
                     f"{base_copay_pct:.0f}% on generic → −₹{copay_amt:,.2f} "
                     f"(blended {copay_pct:.2f}% on post-discount amount)")
    else:
        copay_pct = base_copay_pct
        copay_amt = money(post * copay_pct / 100)
        if copay_pct: steps.append(f"Co-pay {copay_pct:.0f}% applied on post-discount amount: −₹{copay_amt:,.2f}")
    approved = money(post - copay_amt)
    sub_limit = cat.get("sub_limit")
    # CONSULTATION is capped by sub_limit ONLY under the literal "whole_claim" reading
    # (settings.sub_limit_scope); the default "per_line_item" leaves per_claim_limit as
    # its binding cap (enforced by the limits rule). Other categories always cap here.
    cap_applies = sub_limit is not None and (
        category != "CONSULTATION" or settings.sub_limit_scope == "whole_claim")
    if cap_applies and approved > D(sub_limit):
        steps.append(f"Capped at {category} sub-limit ₹{D(sub_limit):,.0f}")
        approved = money(sub_limit)
    steps.append(f"Approved amount: ₹{approved:,.2f}")
    return FinancialBreakdown(gross=to_float(gross), network_discount_pct=to_float(disc_pct),
                              network_discount_amount=to_float(disc_amt), post_discount=to_float(post),
                              copay_pct=to_float(copay_pct), copay_amount=to_float(copay_amt),
                              line_items=decisions, approved_amount=to_float(approved), steps=steps)
