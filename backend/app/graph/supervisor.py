"""Adaptive agentic supervisor for the adjudication stage.

Instead of always fanning out to ALL five rule agents, the supervisor inspects the
structured facts (submission + member + policy) and selects only the rules that are
APPLICABLE to this claim. A rule is SKIPPED ONLY when it is PROVABLY guaranteed to
return PASS for the given input — so the aggregated decision is byte-identical to
running all five rules (an ABSENT rule verdict contributes no FAIL/FLAG, exactly like
a PASS — see app.rules.aggregator).

PROVABLY-SAFE SKIP CONDITIONS (the only skips allowed; each proven against the rule code):

  * pre_auth (app/rules/pre_auth.py):
        The only non-PASS branch is reached when
            high_value = cat["high_value_tests_requiring_pre_auth"]  (truthy)
            AND threshold = cat["pre_auth_threshold"] is not None
        and then a high-value test name appears AND claimed_amount > threshold.
        In the policy ONLY the DIAGNOSTIC category carries those two keys; every other
        category has high_value == [] (falsy) and threshold == None, so the guard
        `if high_value and threshold is not None:` is False and the rule ALWAYS returns
        PASS. => SKIP pre_auth for any non-DIAGNOSTIC claim is provably safe.
        (We additionally verify against the live policy that the category truly lacks
        the pre-auth config, so a future policy that adds pre-auth to another category
        is handled correctly — we only skip when the config is genuinely absent.)

  * waiting_period (app/rules/waiting_period.py):
        FAIL requires either
            days_in < initial_waiting_period_days        (=30), or
            days_in < waiting_days(condition)            (max over specific_conditions = 730).
        The maximum waiting that can possibly apply is
            policy_max = max(initial, max(specific_conditions.values())).
        If days_since_join > policy_max, then for every possible wdays <= policy_max we
        have days_in >= wdays (so `days_in < wdays` is False) AND days_in >= initial,
        so NEITHER FAIL branch can fire and the rule ALWAYS returns PASS.
        => SKIP waiting_period when days_since_join > policy_max is provably safe.
        (Boundary note: at days_in == policy_max, `days_in < policy_max` is already
        False, so `>=` would also be safe; we use the strict `>` for a clean margin.)

  * coverage_exclusion, limits, fraud_anomaly:
        These can produce FAIL/FLAG across many categories/inputs in ways that are NOT
        cheaply provable from the submission alone (line-item exclusions, sub-limits,
        floater/annual usage, history-based fraud signals, bill/claim mismatches).
        => ALWAYS RUN. Never skipped.

If ANY required fact is missing or a skip cannot be proven, the supervisor errs on the
side of RUNNING the rule (fail-safe): a skip is emitted only when the guarantee holds.
"""
from __future__ import annotations

from datetime import date

from app.models.schemas import ClaimSubmission
from app.services.policy_engine import PolicyEngine

# The full rule set, in the canonical order the pipeline runs them.
ALL_RULES = ["waiting_period", "coverage_exclusion", "pre_auth", "limits", "fraud_anomaly"]

# Rules that are NEVER skipped (non-PASS not cheaply provable — see module docstring).
_ALWAYS_RUN = {"coverage_exclusion", "limits", "fraud_anomaly"}


def _policy_max_waiting(pe: PolicyEngine) -> int:
    """The largest waiting period that can possibly apply under the policy:
    max(initial_waiting, max(specific_conditions)). No waiting period can exceed this,
    so a member enrolled longer than this is provably outside every waiting window."""
    specific = pe.waiting_conditions().values()
    return max(pe.initial_waiting_days(), max(specific) if specific else 0)


def _pre_auth_applicable(submission: ClaimSubmission, pe: PolicyEngine) -> bool:
    """True iff pre_auth COULD return non-PASS for this category — i.e. the category
    actually carries `high_value_tests_requiring_pre_auth` AND a `pre_auth_threshold`.
    Mirrors the exact guard in app/rules/pre_auth.py. When False, pre_auth is provably
    a PASS for any input in this category and may be skipped."""
    cat = pe.category_rules(submission.claim_category)
    high_value = cat.get("high_value_tests_requiring_pre_auth") or []
    threshold = cat.get("pre_auth_threshold")
    return bool(high_value) and threshold is not None


def select_rules(submission: ClaimSubmission, member: dict,
                 pe: PolicyEngine) -> tuple[list[str], list[dict]]:
    """Decide which rule agents to invoke for this claim.

    Returns (invoked, skipped) where:
      * invoked  is the list of rule names to actually run, in canonical order, and
      * skipped  is a list of {"rule", "reason"} dicts — each a rule proven to PASS,
                 with a human-readable justification for the trace.

    Only the provably-safe skip conditions documented at module level are applied; if a
    skip cannot be proven for a rule, that rule is invoked (fail-safe)."""
    skipped: list[dict] = []
    skipped_names: set[str] = set()

    # --- pre_auth: skip for categories with no pre-auth-gated tests ----------------
    if not _pre_auth_applicable(submission, pe):
        skipped.append({
            "rule": "pre_auth",
            "reason": (f"pre_auth not applicable to {submission.claim_category} "
                       f"(no pre-auth-gated high-value tests configured for this category) "
                       f"— provably PASS, skipped."),
        })
        skipped_names.add("pre_auth")

    # --- waiting_period: skip when enrolled beyond the policy's maximum waiting -----
    try:
        join = date.fromisoformat(member["join_date"])
        days_since_join = (submission.treatment_date - join).days
        policy_max = _policy_max_waiting(pe)
        if days_since_join > policy_max:
            skipped.append({
                "rule": "waiting_period",
                "reason": (f"waiting_period cleared — enrolled {days_since_join:,} days "
                           f"> {policy_max}-day policy maximum, so no waiting window can "
                           f"apply — provably PASS, skipped."),
            })
            skipped_names.add("waiting_period")
    except (KeyError, ValueError, TypeError):
        # Missing/garbled join_date: cannot prove the skip, so RUN waiting_period.
        pass

    invoked = [r for r in ALL_RULES if r not in skipped_names]
    return invoked, skipped
