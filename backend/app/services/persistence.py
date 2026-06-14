import logging, uuid
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import (create_engine, String, Float, DateTime, Integer, Boolean, JSON,
                        ForeignKey, Index, func, select)
from sqlalchemy.orm import declarative_base, sessionmaker, Mapped, mapped_column
from app.config import settings
from app.models.schemas import ClaimResult, ClaimSubmission
from app.services.timefmt import iso_utc

Base = declarative_base()
engine = create_engine(settings.database_url, pool_pre_ping=True,
                        pool_size=settings.db_pool_size,
                        max_overflow=settings.db_max_overflow,
                        pool_recycle=1800)
Session = sessionmaker(bind=engine)

class ClaimRow(Base):
    __tablename__ = "claims"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    member_id: Mapped[str | None] = mapped_column(String)
    category: Mapped[str | None] = mapped_column(String)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    submission: Mapped[Any] = mapped_column(JSON)
    result: Mapped[Any] = mapped_column(JSON)
    # Indices added by Alembic 0002 (declared here so create_all + ORM stay in sync).
    __table_args__ = (
        Index("ix_claims_member_id", "member_id"),
        Index("ix_claims_created_at", "created_at"),
        Index("ix_claims_status", "status"),
        Index("ix_claims_category", "category"),
    )

# --- Normalized read-models (additive) --------------------------------------
# These tables are a denormalized projection of the JSONB columns, populated on
# save alongside (never instead of) the JSONB. The JSONB remains the source of
# truth for every existing reader (doc viewer, replay, claims list, idempotency);
# these tables exist purely to make analytics/queryability cheap and indexable.

class DocumentRow(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    claim_id: Mapped[str] = mapped_column(String, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False)
    file_id: Mapped[str | None] = mapped_column(String)
    file_name: Mapped[str | None] = mapped_column(String, nullable=True)
    doc_type: Mapped[str | None] = mapped_column(String, nullable=True)
    stored_path: Mapped[str | None] = mapped_column(String, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (Index("ix_documents_claim_id", "claim_id"),)

class UserRow(Base):
    """Auth principal (additive table; see Alembic 0003_users). A `member` user has
    member_id set (scopes their claim access); an `ops` user has member_id NULL and
    can read all claims + run/inspect evals. Only meaningfully used when
    settings.auth_enabled is True, but the table + seeding are harmless when off."""
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # "member" | "ops"
    member_id: Mapped[str | None] = mapped_column(String, nullable=True)  # set for member users
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (Index("ix_users_username", "username", unique=True),)


class TraceEntryRow(Base):
    __tablename__ = "trace_entries"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    claim_id: Mapped[str] = mapped_column(String, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step: Mapped[str | None] = mapped_column(String, nullable=True)
    agent: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (Index("ix_trace_entries_claim_id", "claim_id"),)

log = logging.getLogger("plum.persistence")

# --- Transparent at-rest PHI encryption (gated by settings.phi_encryption_enabled) ---
# When enabled, the PHI-bearing JSONB payloads (submission/result) are stored as an
# encrypted envelope {"_enc": "<fernet-token>"} instead of plaintext JSON. Reads detect
# the envelope and decrypt transparently. A row that is NOT an envelope (legacy plaintext
# written while the flag was off) is returned as-is — so flipping the flag on a populated
# DB is safe (mixed plaintext/ciphertext rows both read correctly). When the flag is off,
# _maybe_encrypt is a pass-through, so storage is byte-identical to before.

def _maybe_encrypt(obj: Any) -> Any:
    """Return the value to persist for a PHI JSONB column. Plaintext object when the
    flag is off (unchanged behaviour); an {"_enc": token} envelope when on."""
    if not settings.phi_encryption_enabled or obj is None:
        return obj
    from app.services.crypto import encrypt_json
    return {"_enc": encrypt_json(obj)}


def _maybe_decrypt(stored: Any) -> Any:
    """Transparently decrypt a PHI JSONB column on read. An {"_enc": token} envelope is
    decrypted; anything else (legacy plaintext dict, or None) is returned unchanged. This
    is independent of the current flag value, so encrypted rows still read after the flag
    is turned back off, and plaintext rows still read after it is turned on."""
    if isinstance(stored, dict) and set(stored.keys()) == {"_enc"} and isinstance(stored["_enc"], str):
        from app.services.crypto import decrypt_json
        return decrypt_json(stored["_enc"])
    return stored

# Map file extensions → content types for the normalized documents projection.
_EXT_CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg",
                      ".jpeg": "image/jpeg", ".pdf": "application/pdf"}

def _content_type_for(path: str | None) -> str:
    import os
    ext = os.path.splitext(path or "")[1].lower()
    return _EXT_CONTENT_TYPES.get(ext, "application/octet-stream")


def init_db():
    # Tolerant: a down DB logs a warning rather than killing app startup. Listing endpoints
    # will surface a clean error later, but the app boots and the pipeline runs without the DB.
    #
    # create_all builds the base `claims` table (and, since they are declared on the
    # same Base, the normalized read-models + indices) when absent. The CANONICAL
    # production path is `alembic upgrade head` (see run_migrations + backend/alembic/);
    # create_all here keeps dev/test startup self-sufficient without an Alembic step.
    # Import the audit models module so AuditLogRow registers on `Base` BEFORE
    # create_all runs — otherwise the `audit_log` table is never created on a fresh
    # dev DB (audit.py is only imported lazily elsewhere, and alembic is best-effort).
    from app.services import audit as _audit_models  # noqa: F401
    if settings.app_env.lower() == "production":
        # Production: the schema is MIGRATION-MANAGED. Run `alembic upgrade head`
        # FAIL-FAST (a failed migration must block boot, not silently serve a stale
        # schema) and do NOT create_all — migrations are the single source of truth.
        run_migrations(strict=True)
        return
    # Dev/test: create_all keeps startup self-sufficient without an Alembic step (tests
    # spin up fresh DBs); migrations then apply additive changes best-effort.
    try:
        Base.metadata.create_all(engine)
    except Exception as e:
        log.warning("init_db failed (DB unavailable?); continuing without persistence: %s", e)
    run_migrations()


def run_migrations(strict: bool = False):
    """`alembic upgrade head`. In dev/test (strict=False) a failure logs and is
    swallowed so the app still boots; in production (strict=True) a failure RAISES so
    a broken/stale schema blocks boot. The canonical CLI path remains
    `alembic upgrade head` from backend/."""
    try:
        import pathlib
        from alembic.config import Config
        from alembic import command
        ini = pathlib.Path(__file__).resolve().parents[2] / "alembic.ini"
        if not ini.exists():
            if strict:
                raise RuntimeError(f"alembic.ini not found at {ini}")
            return
        cfg = Config(str(ini))
        # Don't let alembic's env.py run fileConfig() here — it would disable the
        # app's existing loggers (the CLI `alembic upgrade head` still configures
        # logging normally; only this in-process startup path opts out).
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")
    except Exception as e:
        if strict:
            raise
        log.warning("run_migrations (alembic upgrade head) skipped/failed: %s", e)


def _doc_types_by_file_id(result: ClaimResult) -> dict[str, str]:
    """Best-effort recover per-document doc_type for the normalized projection.
    Prefer the structured `extractions`; fall back to parsing the extraction trace
    summary ('<file_id> → <DOC_TYPE>; ...'), mirroring the doc-viewer's logic."""
    out: dict[str, str] = {}
    for ex in result.extractions or []:
        if ex.file_id:
            out[ex.file_id] = ex.doc_type
    if out:
        return out
    for entry in result.trace or []:
        if entry.agent != "extraction":
            continue
        summary = entry.summary or ""
        if "→" not in summary:
            continue
        left, _, right = summary.partition("→")
        fid = left.strip(); dt = right.split(";", 1)[0].strip()
        if fid and dt:
            out[fid] = dt
    return out


def _populate_normalized(s, sub: ClaimSubmission, result: ClaimResult) -> None:
    """Insert the documents + trace_entries projection rows for a claim. Called
    inside save_claim's session but wrapped by the caller so a failure here can
    never lose the main claim (JSONB stays the source of truth)."""
    doc_types = _doc_types_by_file_id(result)
    for d in sub.documents:
        s.add(DocumentRow(
            claim_id=result.claim_id, file_id=d.file_id, file_name=d.file_name,
            doc_type=doc_types.get(d.file_id, "UNKNOWN"), stored_path=d.stored_path,
            content_type=_content_type_for(d.stored_path or d.file_name)))
    for t in result.trace or []:
        s.add(TraceEntryRow(
            claim_id=result.claim_id, seq=t.seq, step=t.step, agent=t.agent,
            status=t.status, summary=t.summary, degraded=t.degraded,
            duration_ms=t.duration_ms))


def save_claim(sub: ClaimSubmission, result: ClaimResult) -> str:
    with Session() as s:
        d = result.decision
        s.add(ClaimRow(id=result.claim_id, member_id=sub.member_id, category=sub.claim_category,
                       blocked=result.blocked, status=d.status if d else None,
                       approved_amount=d.approved_amount if d else None,
                       confidence=d.confidence if d else None,
                       submission=_maybe_encrypt(sub.model_dump(mode="json")),
                       result=_maybe_encrypt(result.model_dump(mode="json"))))
        s.commit()
    # Normalized projection is strictly additive: a failure to write it must never
    # lose the (already-committed) main claim. Separate session, best-effort, logged.
    try:
        with Session() as s:
            _populate_normalized(s, sub, result)
            s.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("normalized projection write failed for %s (JSONB intact): %s",
                    result.claim_id, e)
    return result.claim_id

def update_claim_result(claim_id: str, result: ClaimResult) -> bool:
    """Overwrite the stored ClaimResult for an EXISTING claim in place (used by the
    ops correction flow to persist the re-decided outcome + corrected extractions +
    append-only correction_history). Keeps the denormalized decision columns
    (status/amount/confidence) in sync. The submission JSONB is untouched (the
    correction changes extracted facts, not the original submission). Returns True
    on success, False if the claim is unknown. PHI encryption is applied transparently
    via _maybe_encrypt, matching save_claim."""
    with Session() as s:
        row = s.get(ClaimRow, claim_id)
        if row is None:
            return False
        d = result.decision
        row.status = d.status if d else None
        row.approved_amount = d.approved_amount if d else None
        row.confidence = d.confidence if d else None
        row.blocked = result.blocked
        row.result = _maybe_encrypt(result.model_dump(mode="json"))
        s.commit()
    return True

def get_claim(claim_id: str) -> dict | None:
    with Session() as s:
        row = s.get(ClaimRow, claim_id)
        return _maybe_decrypt(row.result) if row else None

def get_submission(claim_id: str) -> dict | None:
    """Return the stored ClaimSubmission JSON for a claim (contains documents
    with per-doc stored_path), or None if the claim is unknown."""
    with Session() as s:
        row = s.get(ClaimRow, claim_id)
        return _maybe_decrypt(row.submission) if row else None

def list_claims() -> list[dict]:
    with Session() as s:
        return [{"claim_id": r.id, "created_at": iso_utc(r.created_at), "member_id": r.member_id,
                 "category": r.category, "blocked": r.blocked, "status": r.status,
                 "approved_amount": r.approved_amount, "confidence": r.confidence}
                for r in s.query(ClaimRow).order_by(ClaimRow.created_at.desc()).limit(100)]


# --- Analytics queries over the normalized read-models ----------------------
# These demonstrate the value of the normalized tables: cheap, indexable
# aggregates that would otherwise require scanning/JSON-extracting every row.

def claims_by_status_counts() -> dict[str, int]:
    """Count claims grouped by decision status (uses the indexed `claims.status`)."""
    with Session() as s:
        rows = s.execute(
            select(ClaimRow.status, func.count()).group_by(ClaimRow.status)).all()
        return {(status or "UNKNOWN"): count for status, count in rows}


def recent_documents(limit: int = 20) -> list[dict]:
    """Most recently stored documents across all claims (uses `documents`, an
    indexed normalized table — no JSON extraction over the claims rows)."""
    with Session() as s:
        rows = s.execute(
            select(DocumentRow).order_by(DocumentRow.created_at.desc()).limit(limit)).scalars().all()
        return [{"claim_id": r.claim_id, "file_id": r.file_id, "file_name": r.file_name,
                 "doc_type": r.doc_type, "content_type": r.content_type,
                 "created_at": iso_utc(r.created_at)}
                for r in rows]


# --- Ops dashboard read-models (additive, read-only) ------------------------
# All three helpers below are pure projections over the existing `claims` table.
# They never write and never touch the decision pipeline; they exist to power the
# ops worklist / analytics / fraud views. Each is tolerant of an empty DB.

# A claim is "decided" (vs. blocked / errored) when it has one of these statuses.
_DECIDED_STATUSES = {"APPROVED", "PARTIAL", "REJECTED", "MANUAL_REVIEW"}
# Statuses that count toward the approval-rate numerator.
_APPROVED_STATUSES = {"APPROVED", "PARTIAL"}


def _needs_review(status: str | None, blocked: bool | None) -> bool:
    """A row needs human attention when it landed in MANUAL_REVIEW or was blocked
    at intake (unreadable / wrong-document gate). Drives the worklist highlight."""
    return bool(blocked) or status == "MANUAL_REVIEW"


def _result_cost_latency(result) -> tuple[float, int]:
    """Pull (estimated_cost_inr, total_latency_ms) from a stored result JSON,
    decrypting transparently. Returns (0.0, 0) when absent."""
    r = _maybe_decrypt(result) or {}
    if not isinstance(r, dict):
        return 0.0, 0
    cost = r.get("estimated_cost_inr") or 0.0
    latency = r.get("total_latency_ms") or 0
    try:
        return float(cost), int(latency)
    except (TypeError, ValueError):
        return 0.0, 0


def analytics_summary() -> dict:
    """Single-pass analytics over the `claims` table for the ops dashboard.

    Uses SQL aggregates for the cheap, indexable numbers (counts by status, sums,
    averages) and one scan of the JSONB results for cost/latency (which live inside
    the result payload, not as columns). Tolerant of an empty DB → all-zero shape.
    """
    with Session() as s:
        status_rows = s.execute(
            select(ClaimRow.status, func.count()).group_by(ClaimRow.status)).all()
        by_status = {(status or "UNKNOWN"): count for status, count in status_rows}

        total_claims = sum(by_status.values())
        blocked_count = s.execute(
            select(func.count()).where(ClaimRow.blocked.is_(True))).scalar() or 0

        # Financials + confidence: AVG ignores NULLs, which is what we want
        # (blocked/errored rows have NULL approved_amount/confidence).
        total_approved = s.execute(
            select(func.coalesce(func.sum(ClaimRow.approved_amount), 0.0))).scalar() or 0.0
        avg_approved = s.execute(select(func.avg(ClaimRow.approved_amount))).scalar()
        avg_confidence = s.execute(select(func.avg(ClaimRow.confidence))).scalar()

        # By-category breakdown: count + sum approved per category.
        cat_rows = s.execute(
            select(ClaimRow.category, func.count(),
                   func.coalesce(func.sum(ClaimRow.approved_amount), 0.0))
            .group_by(ClaimRow.category)).all()
        by_category = [
            {"category": cat or "UNKNOWN", "count": count,
             "total_approved": float(total or 0.0)}
            for cat, count, total in cat_rows
        ]
        by_category.sort(key=lambda c: c["count"], reverse=True)

        # Cost/latency live inside the result JSONB → one scan of the result column.
        total_cost = 0.0
        latencies: list[int] = []
        for (result,) in s.execute(select(ClaimRow.result)).all():
            cost, latency = _result_cost_latency(result)
            total_cost += cost
            if latency:
                latencies.append(latency)

    decided = sum(by_status.get(k, 0) for k in _DECIDED_STATUSES)
    approved = sum(by_status.get(k, 0) for k in _APPROVED_STATUSES)
    manual_review = by_status.get("MANUAL_REVIEW", 0)

    def _rate(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    return {
        "total_claims": total_claims,
        "by_status": by_status,
        "decided_count": decided,
        "blocked_count": int(blocked_count),
        "flagged_fraud_count": manual_review,
        "approval_rate": _rate(approved, decided),
        "blocked_rate": _rate(int(blocked_count), total_claims),
        "manual_review_rate": _rate(manual_review, decided),
        "total_approved_amount": round(float(total_approved), 2),
        "avg_approved_amount": round(float(avg_approved), 2) if avg_approved is not None else 0.0,
        "avg_confidence": round(float(avg_confidence), 4) if avg_confidence is not None else 0.0,
        "estimated_total_cost_inr": round(total_cost, 4),
        "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
        "by_category": by_category,
    }


# Whitelist of sort keys → (column, default direction). Guards against SQL
# injection / arbitrary attribute access from the `sort` query param.
_WORKLIST_SORTS = {
    "created_at": ClaimRow.created_at,
    "amount": ClaimRow.approved_amount,
    "confidence": ClaimRow.confidence,
}


def worklist(status: str | None = None, category: str | None = None,
             q: str | None = None, sort: str = "created_at",
             limit: int = 200) -> list[dict]:
    """Filtered/sorted claim queue for the ops worklist. Optional filters: status,
    category, and `q` (case-insensitive substring match on member_id OR claim id).
    Sort by created_at (default, newest first), amount, or confidence (desc, NULLs
    last). Each row carries a `needs_review` flag so the UI can prioritize."""
    sort_col = _WORKLIST_SORTS.get(sort, ClaimRow.created_at)
    with Session() as s:
        stmt = select(ClaimRow)
        if status:
            stmt = stmt.where(ClaimRow.status == status)
        if category:
            stmt = stmt.where(ClaimRow.category == category)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(ClaimRow.member_id.ilike(like) | ClaimRow.id.ilike(like))
        # All worklist sorts are most-relevant-first (newest / largest / most
        # confident). NULLs sort last so undecided rows don't dominate the top.
        stmt = stmt.order_by(sort_col.desc().nullslast()).limit(limit)
        rows = s.execute(stmt).scalars().all()
        return [{"claim_id": r.id,
                 "created_at": iso_utc(r.created_at),
                 "member_id": r.member_id, "category": r.category,
                 "blocked": r.blocked, "status": r.status,
                 "approved_amount": r.approved_amount, "confidence": r.confidence,
                 "needs_review": _needs_review(r.status, r.blocked)}
                for r in rows]


def _fraud_signals_from_result(result) -> dict:
    """Extract the fraud-relevant signals from a stored ClaimResult JSON: the
    decision reason codes + recommendations, any extraction fraud_signals, and the
    fraud_anomaly rule's trace summary. Best-effort; tolerant of partial payloads."""
    r = _maybe_decrypt(result) or {}
    if not isinstance(r, dict):
        return {"reasons": [], "recommendations": [], "extraction_signals": [],
                "fraud_rule": None}
    decision = r.get("decision") or {}
    reasons = [{"code": rc.get("code"), "detail": rc.get("detail")}
               for rc in (decision.get("reason_codes") or []) if isinstance(rc, dict)]
    recommendations = list(decision.get("recommendations") or [])
    extraction_signals: list[str] = []
    for ex in r.get("extractions") or []:
        if isinstance(ex, dict):
            extraction_signals.extend(ex.get("fraud_signals") or [])
    fraud_rule = None
    for entry in r.get("trace") or []:
        if isinstance(entry, dict) and entry.get("agent") == "fraud_anomaly":
            fraud_rule = {"status": entry.get("status"),
                          "summary": entry.get("summary"),
                          "policy_refs": entry.get("policy_refs") or []}
            break
    return {"reasons": reasons, "recommendations": recommendations,
            "extraction_signals": extraction_signals, "fraud_rule": fraud_rule}


def fraud_queue(limit: int = 200) -> list[dict]:
    """Claims flagged for fraud review (status MANUAL_REVIEW), newest first, each
    annotated with its fraud signals pulled from the stored result. Read-only."""
    with Session() as s:
        rows = s.execute(
            select(ClaimRow).where(ClaimRow.status == "MANUAL_REVIEW")
            .order_by(ClaimRow.created_at.desc().nullslast()).limit(limit)).scalars().all()
        out = []
        for r in rows:
            signals = _fraud_signals_from_result(r.result)
            out.append({
                "claim_id": r.id,
                "created_at": iso_utc(r.created_at),
                "member_id": r.member_id, "category": r.category,
                "status": r.status, "approved_amount": r.approved_amount,
                "confidence": r.confidence, **signals,
            })
        return out
