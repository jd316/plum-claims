"""API-layer history accumulation for annual-OPD and family-floater enforcement.

DESIGN — why this lives here and NOT in the pipeline rules:
    The pipeline rules (`rules/limits.py`) consume `submission.ytd_claims_amount` and
    `submission.floater_used_amount` purely as *values*. The eval runner
    (`evalrunner/runner.py`) calls `run_claim` directly with each case's OWN
    `ytd_claims_amount` (usually None) and NEVER touches persisted history, so it
    never accumulates. Accumulation is therefore done ONLY at the API layer
    (`main.py`), which sets these fields on the submission BEFORE running the
    pipeline. This keeps the rules pure and the 12-case eval unchanged.

Both helpers sum over persisted APPROVED / PARTIAL claims (the ones that actually
consumed coverage) within the policy year. They degrade to 0.0 if Postgres is down,
so a missing DB never blocks a submission — it just means no history is accumulated.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from app.config import settings
from app.services.policy_engine import get_policy_engine

log = logging.getLogger("plum.accumulation")

# Statuses that consumed coverage and therefore count toward YTD / floater usage.
_CONSUMING_STATUSES = ("APPROVED", "PARTIAL")


def _policy_year_bounds(pe) -> tuple[date | None, date | None]:
    """(start, end) of the policy year from policy_holder, or (None, None) if absent."""
    ph = pe.policy_holder()
    start = _parse_date(ph.get("policy_start_date"))
    end = _parse_date(ph.get("policy_end_date"))
    return start, end


def _parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _claim_in_policy_year(sub_json: dict, created_at, start: date | None,
                          end: date | None) -> bool:
    """A claim counts if its treatment_date (preferred) or created_at falls within
    the policy-year window. No bounds → always counts."""
    if start is None and end is None:
        return True
    ref = _parse_date((sub_json or {}).get("treatment_date")) or _parse_date(created_at)
    if ref is None:
        return True  # cannot date it → don't silently drop it
    if start and ref < start:
        return False
    if end and ref > end:
        return False
    return True


def _covered_member_ids(member_id: str, pe) -> set[str]:
    """The member plus their covered family (dependents / shared primary), filtered
    to the relationships the family_floater covers. SELF is always included."""
    floater = pe.family_floater()
    covered_rels = {r.upper() for r in floater.get("covered_relationships", [])}
    ids: set[str] = {member_id}
    try:
        members = pe.members()
    except Exception:  # noqa: BLE001
        return ids
    by_id = {m["member_id"]: m for m in members}
    me = by_id.get(member_id)
    if me is None:
        return ids

    def _rel_covered(m: dict) -> bool:
        # Normalise CHILD/CHILDREN so either policy spelling matches.
        rel = (m.get("relationship") or "").upper()
        if not covered_rels:
            return True
        return rel in covered_rels or (rel == "CHILD" and "CHILDREN" in covered_rels)

    # Find the primary of this family: either `me` is primary, or it points to one.
    primary_id = me.get("primary_member_id") or member_id
    primary = by_id.get(primary_id, me)

    # Include the primary themselves if SELF is covered.
    if (primary.get("relationship") or "SELF").upper() in covered_rels or not covered_rels:
        ids.add(primary["member_id"])

    # Include every dependent of the primary whose relationship is covered.
    for dep_id in primary.get("dependents", []) or []:
        dep = by_id.get(dep_id)
        if dep is not None and _rel_covered(dep):
            ids.add(dep_id)
    # Also include any member that names the primary as their primary_member_id.
    for m in members:
        if m.get("primary_member_id") == primary_id and _rel_covered(m):
            ids.add(m["member_id"])
    return ids


def _sum_approved(member_ids: set[str], pe) -> float:
    """Sum approved_amount over persisted consuming claims for `member_ids` within
    the policy year. Returns 0.0 (and logs) if the DB is unreachable."""
    start, end = _policy_year_bounds(pe)
    try:
        from app.services.persistence import ClaimRow, Session
        with Session() as s:
            rows = (s.query(ClaimRow)
                    .filter(ClaimRow.member_id.in_(list(member_ids)))
                    .filter(ClaimRow.status.in_(_CONSUMING_STATUSES))
                    .all())
            total = 0.0
            for r in rows:
                if r.approved_amount is None:
                    continue
                if not _claim_in_policy_year(r.submission, r.created_at, start, end):
                    continue
                total += float(r.approved_amount)
            return round(total, 2)
    except Exception as e:  # noqa: BLE001 — a down DB must not block submission
        log.warning("accumulation query failed (DB down?); treating history as empty: %s", e)
        return 0.0


def member_ytd(member_id: str, pe=None) -> float:
    """Sum of approved_amount over persisted APPROVED/PARTIAL claims for THIS member
    within the policy year. Used to fill `ytd_claims_amount` when the caller omits it."""
    pe = pe or get_policy_engine(settings.policy_path)
    return _sum_approved({member_id}, pe)


def family_floater_used(member_id: str, pe=None) -> float:
    """Sum of approved_amount across the member + their covered family within the
    policy year. Used to fill `floater_used_amount` for the floater-limit rule."""
    pe = pe or get_policy_engine(settings.policy_path)
    return _sum_approved(_covered_member_ids(member_id, pe), pe)


def member_alt_med_sessions_ytd(member_id: str, pe=None) -> int:
    """COUNT of persisted consuming ALTERNATIVE_MEDICINE claims for this member within the
    policy year — one claim = one session. Fills `alt_med_sessions_ytd` for the gated
    session-cap rule (settings.alt_med_session_limit_enabled). Same API-layer-only design as
    member_ytd: the eval runner never calls this, so the 12 cases are unaffected. Returns 0
    if the DB is unreachable."""
    pe = pe or get_policy_engine(settings.policy_path)
    start, end = _policy_year_bounds(pe)
    try:
        import json as _json
        from app.services.persistence import ClaimRow, Session
        with Session() as s:
            rows = (s.query(ClaimRow)
                    .filter(ClaimRow.member_id == member_id)
                    .filter(ClaimRow.status.in_(_CONSUMING_STATUSES))
                    .all())
            count = 0
            for r in rows:
                sub = r.submission
                if isinstance(sub, str):
                    try: sub = _json.loads(sub)
                    except (ValueError, TypeError): sub = {}
                if (sub or {}).get("claim_category") != "ALTERNATIVE_MEDICINE":
                    continue
                if not _claim_in_policy_year(sub, r.created_at, start, end):
                    continue
                count += 1
            return count
    except Exception as e:  # noqa: BLE001 — a down DB must not block submission
        log.warning("alt-med session count failed (DB down?); treating as 0: %s", e)
        return 0
