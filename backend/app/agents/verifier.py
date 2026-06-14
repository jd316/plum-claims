"""LLM-as-judge over the deterministic decision. Advisory + safety net: a FAIL forces
MANUAL_REVIEW; it can never change amounts or flip a rejection to approval."""
from app.models.schemas import VerifierResult, Decision, RuleVerdict
from app.services.gemini import generate_structured_with_usage
from app.config import settings

PROMPT_TMPL = """You are an independent claims-decision reviewer. Below is a health-insurance
claim decision produced by a deterministic rules engine, with the rule verdicts it used.
Judge ONLY whether the decision is internally consistent with the verdicts and policy logic
(right status, right reasons, arithmetic plausible). You cannot recompute amounts.

CRITICAL RULE: If the decision status is APPROVED or PARTIAL but ANY rule verdict status is
FAIL, that is a direct contradiction — you MUST return verdict FAIL.

NOT a contradiction (these are the designed mechanism for PARTIAL approvals — return PASS):
- A 'coverage_exclusion' verdict with status PASS whose detail notes that a specific LINE ITEM is an
  excluded procedure (e.g. 'Teeth Whitening is an excluded procedure for this category'). A PASS here
  means the claim as a whole is covered; the excluded line item is simply dropped from the payout.
  This correctly yields a PARTIAL decision where that line item is unapproved. Judge that as CONSISTENT.
- A PARTIAL decision that approves the covered line items and denies an excluded line item, while every
  rule verdict is PASS, is internally consistent. Only the presence of an actual FAIL verdict (not PASS
  with an informative detail) contradicts an APPROVED/PARTIAL status.

Decision: {decision}
Rule verdicts: {verdicts}

Return verdict PASS if consistent, FAIL if you find a contradiction; confidence 0-1; one-sentence reason."""

def verify_with_usage(decision: Decision,
                      verdicts: list[RuleVerdict]) -> tuple[VerifierResult, dict]:
    """Sub-feature A: verifier judgement + per-call token usage for the trace."""
    prompt = PROMPT_TMPL.format(
        decision=decision.model_dump_json(exclude={"confidence_components"}),
        verdicts=[v.model_dump(include={"rule", "status", "reason_code", "detail"}) for v in verdicts])
    return generate_structured_with_usage([prompt], VerifierResult, model=settings.gemini_pro_model)


def verify(decision: Decision, verdicts: list[RuleVerdict]) -> VerifierResult:
    v, _ = verify_with_usage(decision, verdicts)
    return v
