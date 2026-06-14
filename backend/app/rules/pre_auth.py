from app.models.schemas import RuleVerdict
from app.rules.base import RuleContext

def check(ctx: RuleContext) -> RuleVerdict:
    cat = ctx.pe.category_rules(ctx.submission.claim_category)
    refs = ["pre_authorization.required_for"]
    high_value = [t.lower() for t in cat.get("high_value_tests_requiring_pre_auth", [])]
    threshold = cat.get("pre_auth_threshold")
    if high_value and threshold is not None:
        joined = " ".join(i.description.lower() for i in ctx.line_items) + " " + \
                 " ".join((e.treatment.value or "") for e in ctx.extractions).lower()
        hit = next((t for t in high_value if t.lower() in joined), None)
        if hit and ctx.submission.claimed_amount > threshold:
            return RuleVerdict(rule="pre_auth", status="FAIL", reason_code="PRE_AUTH_MISSING",
                detail=(f"{hit.upper()} above ₹{threshold:,.0f} requires pre-authorization, and none was "
                        f"submitted with this claim. To proceed: obtain pre-authorization from the insurer "
                        f"(valid {ctx.pe.pre_authorization()['validity_days']} days) and resubmit the claim "
                        f"with the pre-auth reference number."),
                policy_refs=refs + ["opd_categories.diagnostic.pre_auth_threshold"])
    return RuleVerdict(rule="pre_auth", status="PASS", detail="No pre-authorization requirement triggered.",
                       policy_refs=refs)
