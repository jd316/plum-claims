from datetime import date, timedelta
from app.config import settings
from app.models.schemas import RuleVerdict
from app.rules.base import RuleContext

def check(ctx: RuleContext) -> RuleVerdict:
    join = date.fromisoformat(ctx.member["join_date"])
    tdate = ctx.submission.treatment_date
    days_in = (tdate - join).days
    refs = ["waiting_periods.initial_waiting_period_days"]
    # Pre-existing-condition waiting period (gated OFF; settings.pre_existing_condition_check_enabled).
    # Enforced against a per-member enrolment marker `pre_existing_condition_eligible_from`; treatment
    # before that date is inside the PED waiting period. Default OFF and no test member carries the
    # marker, so the 12 cases are unaffected.
    if settings.pre_existing_condition_check_enabled:
        ped_from = ctx.member.get("pre_existing_condition_eligible_from")
        if ped_from:
            eligible = date.fromisoformat(ped_from)
            if tdate < eligible:
                ped_days = ctx.pe.pre_existing_conditions_days()
                return RuleVerdict(rule="waiting_period", status="FAIL", reason_code="WAITING_PERIOD",
                    detail=f"This member has a {ped_days}-day pre-existing-condition waiting period; "
                           f"pre-existing-condition claims are eligible from {eligible}. Treatment on "
                           f"{tdate} falls before that date.",
                    policy_refs=refs + ["waiting_periods.pre_existing_conditions_days"])
    initial = ctx.pe.initial_waiting_days()
    if days_in < initial:
        eligible = join + timedelta(days=initial)
        return RuleVerdict(rule="waiting_period", status="FAIL", reason_code="WAITING_PERIOD",
            detail=f"Treatment on {tdate} falls inside the initial {initial}-day waiting period "
                   f"(member joined {join}). Claims are eligible from {eligible}.",
            policy_refs=refs)
    cond = ctx.semantic.waiting_condition
    if cond:
        wdays = ctx.pe.waiting_days(cond)
        refs.append(f"waiting_periods.specific_conditions.{cond}")
        if wdays and days_in < wdays:
            eligible = join + timedelta(days=wdays)
            return RuleVerdict(rule="waiting_period", status="FAIL", reason_code="WAITING_PERIOD",
                detail=f"'{cond}' has a {wdays}-day waiting period. Member joined {join}; treatment "
                       f"on {tdate} is day {days_in}. Eligible for {cond}-related claims from {eligible}.",
                policy_refs=refs, certainty=min(1.0, ctx.semantic.confidence + 0.2))
    return RuleVerdict(rule="waiting_period", status="PASS",
                       detail=f"Outside all applicable waiting periods (day {days_in} since joining).",
                       policy_refs=refs)
