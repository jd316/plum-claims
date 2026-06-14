"""Immutable audit log + retention for decided claims.

Append-only `audit_log` table records one row per decision (no update/delete in the
normal flow). The row stores only the NON-PHI decision summary (status, approved amount,
reason codes) plus claim_id + actor — never patient/document content — so the audit
trail is safe to retain after the claim's PHI is anonymized by a retention sweep.

  * record_decision(claim_id, decision, actor) — append one row (best-effort; the caller
    wraps it so an audit failure never blocks the claim response).
  * audit_trail(claim_id) — the append-only history for a claim, oldest first.
  * retention_sweep(days) — anonymize/delete claims (and their PHI) older than a window,
    while KEEPING the non-PHI audit summary. Provided as a function + CLI; never auto-run.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import String, Float, DateTime, JSON, Index, select
from sqlalchemy.orm import Mapped, mapped_column

from app.services.persistence import Base, Session, ClaimRow
from app.services.timefmt import iso_utc

log = logging.getLogger("plum.audit")


class AuditLogRow(Base):
    """Append-only audit record. Intentionally carries NO PHI: claim_id + the decision
    summary only. Survives retention anonymization of the claim it references."""
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    claim_id: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False, default="system")
    action: Mapped[str] = mapped_column(String, nullable=False, default="DECISION")
    decision_status: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason_codes: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (Index("ix_audit_log_claim_id", "claim_id"),)


def record_decision(claim_id: str, decision, actor: str = "system") -> str | None:
    """Append an audit row for a decided claim. Best-effort: returns the row id, or
    None if persistence is unavailable (caller treats it as non-blocking). Append-only —
    this function never updates or deletes existing rows."""
    try:
        status = getattr(decision, "status", None) if decision else None
        amount = getattr(decision, "approved_amount", None) if decision else None
        codes = [rc.code for rc in getattr(decision, "reason_codes", [])] if decision else []
        row_id = uuid.uuid4().hex
        with Session() as s:
            s.add(AuditLogRow(id=row_id, claim_id=claim_id, actor=actor, action="DECISION",
                              decision_status=status, approved_amount=amount, reason_codes=codes))
            s.commit()
        return row_id
    except Exception as e:  # noqa: BLE001 — auditing must never block the response
        log.warning("record_decision failed for %s (non-blocking): %s", claim_id, e)
        return None


def record_correction(claim_id: str, before, after, changed_fields: list[str],
                      actor: str = "ops") -> str | None:
    """Append an audit row for an ops inline field correction. Captures the actor,
    which extracted fields changed, and the original→new decision status/amount —
    NON-PHI only (field NAMES, not values). Best-effort + append-only, exactly like
    record_decision: returns the row id, or None if persistence is unavailable
    (caller treats it as non-blocking)."""
    try:
        reason_codes = {
            "changed_fields": list(changed_fields),
            "before": {"status": getattr(before, "status", None) if before else None,
                       "approved_amount": getattr(before, "approved_amount", None) if before else None},
            "after": {"status": getattr(after, "status", None) if after else None,
                      "approved_amount": getattr(after, "approved_amount", None) if after else None},
        }
        row_id = uuid.uuid4().hex
        with Session() as s:
            s.add(AuditLogRow(
                id=row_id, claim_id=claim_id, actor=actor, action="CORRECTION",
                decision_status=getattr(after, "status", None) if after else None,
                approved_amount=getattr(after, "approved_amount", None) if after else None,
                reason_codes=reason_codes))
            s.commit()
        return row_id
    except Exception as e:  # noqa: BLE001 — auditing must never block the response
        log.warning("record_correction failed for %s (non-blocking): %s", claim_id, e)
        return None


def record_operator_decision(claim_id: str, before, after, note: str,
                             actor: str = "ops") -> str | None:
    """Append an audit row for an operator's FINAL decision (resolve a MANUAL_REVIEW or
    override the AI). Captures the actor, the human note (the decision rationale), and the
    AI→operator status/amount. Best-effort + append-only, like record_decision/correction."""
    try:
        reason_codes = {
            "note": note,
            "before": {"status": getattr(before, "status", None) if before else None,
                       "approved_amount": getattr(before, "approved_amount", None) if before else None},
            "after": {"status": getattr(after, "status", None) if after else None,
                      "approved_amount": getattr(after, "approved_amount", None) if after else None},
        }
        row_id = uuid.uuid4().hex
        with Session() as s:
            s.add(AuditLogRow(
                id=row_id, claim_id=claim_id, actor=actor, action="OPERATOR_DECISION",
                decision_status=getattr(after, "status", None) if after else None,
                approved_amount=getattr(after, "approved_amount", None) if after else None,
                reason_codes=reason_codes))
            s.commit()
        return row_id
    except Exception as e:  # noqa: BLE001 — auditing must never block the response
        log.warning("record_operator_decision failed for %s (non-blocking): %s", claim_id, e)
        return None


def record_outcome_label(claim_id: str, confidence: float, correct: bool,
                         decision_status: str | None = None, actor: str = "ops") -> str | None:
    """Append an operator's outcome label: was the AUTOMATED decision correct? This is the
    RIGHT label domain for confidence calibration (operator agreement on the final decision,
    not extraction-field accuracy). Stores the decision's confidence + the boolean label so
    `outcome_labels()` can feed recalibration. Best-effort + append-only."""
    try:
        row_id = uuid.uuid4().hex
        with Session() as s:
            s.add(AuditLogRow(
                id=row_id, claim_id=claim_id, actor=actor, action="OUTCOME_LABEL",
                decision_status=decision_status,
                reason_codes={"confidence": float(confidence), "correct": bool(correct)}))
            s.commit()
        return row_id
    except Exception as e:  # noqa: BLE001 — auditing must never block the response
        log.warning("record_outcome_label failed for %s (non-blocking): %s", claim_id, e)
        return None


def outcome_labels() -> list[dict]:
    """All operator outcome labels as {claim_id, confidence, correct, created_at}, oldest
    first. The training set for confidence recalibration / conformal risk control. Returns
    [] if persistence is unavailable."""
    try:
        with Session() as s:
            rows = s.execute(
                select(AuditLogRow).where(AuditLogRow.action == "OUTCOME_LABEL")
                .order_by(AuditLogRow.created_at.asc())).scalars().all()
            out = []
            for r in rows:
                rc = r.reason_codes or {}
                if "confidence" in rc and "correct" in rc:
                    out.append({"claim_id": r.claim_id, "confidence": float(rc["confidence"]),
                                "correct": bool(rc["correct"]),
                                "created_at": iso_utc(r.created_at)})
            return out
    except Exception as e:  # noqa: BLE001
        log.warning("outcome_labels query failed (non-blocking): %s", e)
        return []


def audit_trail(claim_id: str) -> list[dict]:
    """Return the append-only audit history for a claim, oldest first."""
    with Session() as s:
        rows = s.execute(
            select(AuditLogRow).where(AuditLogRow.claim_id == claim_id)
            .order_by(AuditLogRow.created_at.asc())).scalars().all()
        return [{"id": r.id, "claim_id": r.claim_id, "actor": r.actor, "action": r.action,
                 "decision_status": r.decision_status, "approved_amount": r.approved_amount,
                 "reason_codes": r.reason_codes or [],
                 "created_at": iso_utc(r.created_at)}
                for r in rows]


def retention_sweep(days: int, *, delete_rows: bool = False) -> dict:
    """Apply data retention to claims older than `days`.

    The PHI lives in the claims table's `submission`/`result` JSONB (+ the documents /
    trace_entries projection). This sweep removes that PHI for aged claims while KEEPING
    the non-PHI audit summary in audit_log:

      * delete_rows=False (default): ANONYMIZE in place — null out the PHI-bearing JSONB
        columns + member_id, leaving a tombstone claims row (status/amount kept for stats).
      * delete_rows=True: DELETE the claims rows outright (documents/trace_entries cascade).

    Returns a summary {cutoff, anonymized|deleted, audit_rows_kept}. The audit_log is
    never touched here — it is the durable, non-PHI record of what was decided.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    affected = 0
    with Session() as s:
        aged = s.execute(select(ClaimRow).where(ClaimRow.created_at < cutoff)).scalars().all()
        for row in aged:
            if delete_rows:
                s.delete(row)
            else:
                # Anonymize: drop PHI-bearing payloads + member linkage; keep the
                # decision summary columns for non-PHI analytics.
                row.submission = None
                row.result = None
                row.member_id = None
            affected += 1
        s.commit()
    audit_kept = 0
    try:
        with Session() as s:
            from sqlalchemy import func
            audit_kept = s.execute(select(func.count()).select_from(AuditLogRow)).scalar() or 0
    except Exception:  # noqa: BLE001
        pass
    return {"cutoff": cutoff.isoformat(), "days": days,
            ("deleted" if delete_rows else "anonymized"): affected,
            "audit_rows_kept": audit_kept}


def _cli() -> None:
    """CLI entrypoint: python -m app.services.audit --retention-days N [--delete].
    NOT run automatically anywhere — an operator invokes it explicitly."""
    import argparse
    parser = argparse.ArgumentParser(description="Claims audit / retention utility")
    parser.add_argument("--retention-days", type=int, required=True,
                        help="Anonymize (or --delete) claims older than this many days.")
    parser.add_argument("--delete", action="store_true",
                        help="Hard-delete aged claim rows instead of anonymizing in place.")
    args = parser.parse_args()
    summary = retention_sweep(args.retention_days, delete_rows=args.delete)
    print(summary)


if __name__ == "__main__":
    _cli()
