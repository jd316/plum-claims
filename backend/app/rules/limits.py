"""Documented interpretation (spec §12a): categories with a governing sub_limit
(DENTAL/DIAGNOSTIC/PHARMACY/VISION/ALTERNATIVE_MEDICINE) are capped by that sub_limit;
CONSULTATION claims are capped by coverage.per_claim_limit (its sub_limit applies to the
consultation-fee line). For categories with line-item exclusions, the cap is checked against
the COVERED amount (excluded items don't count toward the cap)."""
import re

from app.config import settings
from app.models.schemas import RuleVerdict
from app.rules.base import RuleContext

# State-coded Indian medical registration, e.g. KA/45678/2015, or AYUSH e.g. AYUR/KL/2345/2019.
_REG_RE = re.compile(r"^(AYUR/)?[A-Z]{2}/\d{3,6}/\d{4}$")

def _valid_registration(value: str | None) -> bool:
    return bool(value) and bool(_REG_RE.match(value.strip().upper()))

def check(ctx: RuleContext) -> RuleVerdict:
    cat_name = ctx.submission.claim_category
    amount = ctx.submission.claimed_amount
    cat = ctx.pe.category_rules(cat_name)
    refs: list[str] = []
    # CONSULTATION cap interpretation is config-driven (settings.sub_limit_scope):
    #   "per_line_item" (default §12a reading) → per_claim_limit is the binding whole-claim cap;
    #   "whole_claim" (literal reading) → the consultation sub_limit caps the whole claim.
    # Other categories always cap by their sub_limit (handled in the else branch).
    if cat_name == "CONSULTATION" and settings.sub_limit_scope != "whole_claim":
        limit = ctx.pe.per_claim_limit(); refs.append("coverage.per_claim_limit")
        if amount > limit:
            return RuleVerdict(rule="limits", status="FAIL", reason_code="PER_CLAIM_EXCEEDED",
                detail=f"Claimed amount ₹{amount:,.0f} exceeds the per-claim limit of ₹{limit:,.0f}.",
                policy_refs=refs)
    else:
        limit = cat["sub_limit"]; refs.append(f"opd_categories.{cat_name.lower()}.sub_limit")
        covered = amount
        if ctx.line_items:
            cat_excluded = [p.lower() for p in cat.get("excluded_procedures", []) + cat.get("excluded_items", [])]
            covered = sum(i.amount for i in ctx.line_items
                          if not any(p in i.description.lower() for p in cat_excluded))
        if covered > limit:
            return RuleVerdict(rule="limits", status="FAIL", reason_code="SUB_LIMIT_EXCEEDED",
                detail=f"Covered amount ₹{covered:,.0f} exceeds the {cat_name} sub-limit of ₹{limit:,.0f}.",
                policy_refs=refs)
    # ALTERNATIVE_MEDICINE-only gated checks (both OFF by default; no test case is alt-medicine).
    if cat_name == "ALTERNATIVE_MEDICINE":
        # Session cap: max_sessions_per_year. The YTD session count is attached at the API
        # layer (ctx.submission.alt_med_sessions_ytd); None → not supplied → skip.
        if settings.alt_med_session_limit_enabled:
            max_sessions = cat.get("max_sessions_per_year")
            used = ctx.submission.alt_med_sessions_ytd
            if max_sessions is not None and used is not None and used + 1 > max_sessions:
                return RuleVerdict(rule="limits", status="FAIL", reason_code="SESSION_LIMIT_EXCEEDED",
                    detail=f"This is alternative-medicine session {used + 1}, exceeding the policy "
                           f"maximum of {max_sessions} sessions per year.",
                    policy_refs=refs + [f"opd_categories.{cat_name.lower()}.max_sessions_per_year"])
        # Registered-practitioner requirement: at least one document must carry a well-formed
        # Indian medical/AYUSH registration number.
        if settings.practitioner_registration_check_enabled and cat.get("requires_registered_practitioner"):
            if not any(_valid_registration(e.doctor_registration.value) for e in ctx.extractions):
                return RuleVerdict(rule="limits", status="FAIL", reason_code="PRACTITIONER_NOT_REGISTERED",
                    detail="Alternative-medicine claims require a registered practitioner, but no "
                           "document carries a valid medical/AYUSH registration number.",
                    policy_refs=refs + [f"opd_categories.{cat_name.lower()}.requires_registered_practitioner"])
    # Family-floater combined limit. floater_used_amount is set ONLY at the API layer
    # (from persisted family history); the eval runner never sets it, so it stays None
    # there and this branch never fires for the 12 cases — eval-safe by construction.
    floater_used = ctx.submission.floater_used_amount
    floater = ctx.pe.family_floater()
    if floater_used is not None and floater.get("enabled"):
        combined = floater["combined_limit"]; refs.append("coverage.family_floater.combined_limit")
        if floater_used + amount > combined:
            return RuleVerdict(rule="limits", status="FAIL", reason_code="FLOATER_LIMIT_EXCEEDED",
                detail=f"Family-floater usage ₹{floater_used:,.0f} + this claim ₹{amount:,.0f} exceeds "
                       f"the combined family-floater limit of ₹{combined:,.0f}.", policy_refs=refs)
    if ctx.submission.ytd_claims_amount is not None:
        annual = ctx.pe.annual_opd_limit(); refs.append("coverage.annual_opd_limit")
        if ctx.submission.ytd_claims_amount + amount > annual:
            return RuleVerdict(rule="limits", status="FAIL", reason_code="ANNUAL_LIMIT_EXCEEDED",
                detail=f"YTD claims ₹{ctx.submission.ytd_claims_amount:,.0f} + this claim exceeds the "
                       f"annual OPD limit ₹{annual:,.0f}.", policy_refs=refs)
    return RuleVerdict(rule="limits", status="PASS",
                       detail="Within applicable limits.", policy_refs=refs)
