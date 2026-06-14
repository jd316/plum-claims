from app.config import settings
from app.models.schemas import RuleVerdict
from app.rules.base import RuleContext

def check(ctx: RuleContext) -> RuleVerdict:
    cat = ctx.pe.category_rules(ctx.submission.claim_category)
    refs = [f"opd_categories.{ctx.submission.claim_category.lower()}"]
    if not cat.get("covered", True):
        return RuleVerdict(rule="coverage_exclusion", status="FAIL", reason_code="NOT_COVERED",
                           detail=f"{ctx.submission.claim_category} is not covered.", policy_refs=refs)
    candidates = list(ctx.semantic.exclusion_candidates)
    if not candidates:
        text = " ".join(filter(None, [e.diagnosis.value for e in ctx.extractions] +
                                     [e.treatment.value for e in ctx.extractions])).lower()
        candidates = [x for x in ctx.pe.exclusion_conditions()
                      if any(w in text for w in (x.lower().split(" and ")[0],)) and x.lower() != ""]
    confirmed = [c for c in candidates if ctx.pe.is_excluded_condition(c)]
    if confirmed:
        refs.append("exclusions.conditions")
        return RuleVerdict(rule="coverage_exclusion", status="FAIL", reason_code="EXCLUDED_CONDITION",
            detail=f"The treatment falls under policy exclusion(s): {', '.join(confirmed)}. "
                   f"These are not covered under PLUM_GHI_2024.",
            policy_refs=refs, certainty=max(0.85, ctx.semantic.confidence))
    disallowed: list[str] = []
    details: list[str] = []
    excluded_procs = [p.lower() for p in cat.get("excluded_procedures", []) + cat.get("excluded_items", [])]
    for item in ctx.line_items:
        if any(p in item.description.lower() or item.description.lower() in p for p in excluded_procs):
            disallowed.append(item.description)
            details.append(f"'{item.description}' is an excluded procedure for this category.")
    # Generic-mandatory (gated OFF; settings.generic_mandatory_enabled). For PHARMACY, a branded
    # line for which a generic substitute exists is disallowed (policy mandates generics). Needs the
    # formulary-derived has_generic_alternative flag; both default-absent → no effect on the 12 cases.
    if settings.generic_mandatory_enabled and ctx.submission.claim_category == "PHARMACY" \
            and cat.get("generic_mandatory"):
        for item in ctx.line_items:
            if item.is_branded and item.has_generic_alternative and item.description not in disallowed:
                disallowed.append(item.description)
                details.append(f"'{item.description}' is a branded drug with a generic substitute; "
                               f"the policy mandates generics, so it is not covered.")
                refs.append(f"opd_categories.{ctx.submission.claim_category.lower()}.generic_mandatory")
    return RuleVerdict(rule="coverage_exclusion", status="PASS",
        detail="; ".join(details) or "Treatment and all line items are covered.",
        policy_refs=refs, disallowed_items=disallowed)
