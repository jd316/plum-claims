"""Deterministic tests for the production data layer (Part A + B).

Covers:
  * Alembic: `alembic upgrade head` creates documents + trace_entries + the indices;
    downgrade of 0002 removes them. (Needs Postgres; skips cleanly if unreachable.)
  * Normalized population: save_claim writes the projection rows, claims_by_status_counts
    / recent_documents reflect them, and the existing JSONB readers are unchanged.
  * object_store local mode: put/open/get_path round-trip; default backend is local;
    an unconfigured minio backend falls back to local without crashing.

None of these hit Gemini, so they are NOT marked live.
"""
from __future__ import annotations

import os
import pathlib
import uuid
from datetime import date

import pytest

from app.models.schemas import (
    ClaimResult,
    ClaimSubmission,
    Decision,
    DocumentInput,
    ExtractionResult,
    ReasonCode,
    TraceEntry,
)

_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# DB reachability guard
# ---------------------------------------------------------------------------

def _db_reachable() -> bool:
    try:
        from sqlalchemy import text
        from app.services.persistence import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres unreachable")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _submission(member_id: str = "M-DL-001") -> ClaimSubmission:
    return ClaimSubmission(
        member_id=member_id, policy_id="POL-DL-001", claim_category="CONSULTATION",
        treatment_date=date(2026, 1, 10), claimed_amount=900.0,
        documents=[DocumentInput(file_id="F001", file_name="bill.png",
                                 stored_path="/tmp/dl_bill.png")],
    )


def _result(claim_id: str, status: str = "APPROVED") -> ClaimResult:
    return ClaimResult(
        claim_id=claim_id, blocked=False,
        decision=Decision(status=status, approved_amount=800.0, confidence=0.9,
                          reason_codes=[ReasonCode(code="OK", detail="ok")],
                          member_message="ok"),
        extractions=[ExtractionResult(file_id="F001", doc_type="HOSPITAL_BILL")],
        trace=[TraceEntry(seq=1, step="extract", agent="extraction", status="PASS",
                          summary="F001 → HOSPITAL_BILL", duration_ms=12),
               TraceEntry(seq=2, step="decide", agent="decision", status="PASS",
                          summary="approved", duration_ms=5)],
    )


# ---------------------------------------------------------------------------
# Alembic
# ---------------------------------------------------------------------------

@requires_db
def test_alembic_upgrade_head_creates_tables_and_indices():
    from alembic.config import Config
    from alembic import command
    from sqlalchemy import inspect
    from app.services.persistence import engine

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    command.upgrade(cfg, "head")

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert {"claims", "documents", "trace_entries"} <= tables

    claims_ix = {ix["name"] for ix in insp.get_indexes("claims")}
    assert {"ix_claims_member_id", "ix_claims_created_at",
            "ix_claims_status", "ix_claims_category"} <= claims_ix
    assert "ix_documents_claim_id" in {ix["name"] for ix in insp.get_indexes("documents")}
    assert "ix_trace_entries_claim_id" in {ix["name"] for ix in insp.get_indexes("trace_entries")}


@requires_db
def test_alembic_downgrade_0002_removes_normalized_then_restores():
    from alembic.config import Config
    from alembic import command
    from sqlalchemy import inspect
    from app.services.persistence import engine

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0001")

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert "claims" in tables  # baseline survives — JSONB readers unaffected
    assert "documents" not in tables
    assert "trace_entries" not in tables
    assert {ix["name"] for ix in insp.get_indexes("claims")} == set()

    # Restore so the rest of the suite (and the live app) finds the full schema.
    command.upgrade(cfg, "head")
    insp = inspect(engine)
    assert {"documents", "trace_entries"} <= set(insp.get_table_names())


# ---------------------------------------------------------------------------
# Normalized population + analytics queries
# ---------------------------------------------------------------------------

@requires_db
def test_save_claim_populates_normalized_rows():
    from sqlalchemy import select
    from app.services.persistence import (Session, DocumentRow, TraceEntryRow,
                                          init_db, save_claim)
    init_db()

    claim_id = f"dl-{uuid.uuid4().hex}"
    save_claim(_submission(), _result(claim_id))

    with Session() as s:
        docs = s.execute(select(DocumentRow).where(
            DocumentRow.claim_id == claim_id)).scalars().all()
        traces = s.execute(select(TraceEntryRow).where(
            TraceEntryRow.claim_id == claim_id)).scalars().all()

    assert len(docs) == 1
    assert docs[0].file_id == "F001"
    assert docs[0].doc_type == "HOSPITAL_BILL"  # recovered from extractions
    assert docs[0].content_type == "image/png"
    assert len(traces) == 2
    assert {t.agent for t in traces} == {"extraction", "decision"}


@requires_db
def test_claims_by_status_counts_reflects_saved_claim():
    from app.services.persistence import init_db, save_claim, claims_by_status_counts
    init_db()

    before = claims_by_status_counts().get("MANUAL_REVIEW", 0)
    save_claim(_submission(), _result(f"dl-{uuid.uuid4().hex}", status="MANUAL_REVIEW"))
    after = claims_by_status_counts().get("MANUAL_REVIEW", 0)
    assert after == before + 1


@requires_db
def test_recent_documents_lists_projection():
    from app.services.persistence import init_db, save_claim, recent_documents
    init_db()

    claim_id = f"dl-{uuid.uuid4().hex}"
    save_claim(_submission(), _result(claim_id))
    recent = recent_documents(limit=50)
    assert any(d["claim_id"] == claim_id and d["doc_type"] == "HOSPITAL_BILL"
               for d in recent)


@requires_db
def test_existing_jsonb_readers_unchanged():
    """get_claim / get_submission / list_claims keep returning the JSONB shape."""
    from app.services.persistence import (init_db, save_claim, get_claim,
                                          get_submission, list_claims)
    init_db()

    claim_id = f"dl-{uuid.uuid4().hex}"
    save_claim(_submission(member_id="M-JSONB"), _result(claim_id))

    rec = get_claim(claim_id)
    assert rec is not None and rec["claim_id"] == claim_id
    assert rec["decision"]["status"] == "APPROVED"

    sub = get_submission(claim_id)
    assert sub is not None and sub["documents"][0]["file_id"] == "F001"

    ids = [c["claim_id"] for c in list_claims()]
    assert claim_id in ids


# ---------------------------------------------------------------------------
# object_store — local mode + minio fallback
# ---------------------------------------------------------------------------

def test_object_store_default_is_local():
    from app.config import settings
    from app.services.object_store import get_object_store, reset_object_store_cache
    reset_object_store_cache()
    try:
        assert settings.object_store == "local"
        assert get_object_store().backend == "local"
    finally:
        reset_object_store_cache()


def test_object_store_local_round_trip(tmp_path):
    from app.services.object_store import (LocalObjectStore, storage_key)

    src = tmp_path / "src.bin"
    payload = b"plum-object-store-roundtrip"
    src.write_bytes(payload)

    store = LocalObjectStore()
    key = storage_key("uploads", f"CLM-{uuid.uuid4().hex[:6]}", "F001.bin")
    dst = store.put(key, str(src))

    assert os.path.isfile(dst)
    assert store.open(key) == payload
    # local get_path is a real filesystem path (not a URL)
    assert store.get_path(key) == dst
    assert not store.get_path(key).startswith("http")

    os.remove(dst)


def test_object_store_put_is_noop_when_src_equals_dst(tmp_path, monkeypatch):
    """When the upload was already streamed to the key's local path (the common
    _ingest_claim case), put() returns it without copying onto itself."""
    from app.config import settings
    from app.services.object_store import LocalObjectStore, storage_key

    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    store = LocalObjectStore()
    key = storage_key("uploads", "CLM-noop", "F001.bin")
    dst = store.local_path(key)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(b"already-here")

    returned = store.put(key, dst)  # src == dst → no self-copy, no crash
    assert returned == dst
    assert store.open(key) == b"already-here"


def test_unconfigured_minio_falls_back_to_local(monkeypatch):
    """Selecting minio with an unreachable endpoint must degrade to local, not crash."""
    from app.config import settings
    from app.services.object_store import get_object_store, reset_object_store_cache

    monkeypatch.setattr(settings, "object_store", "minio")
    # Point at a definitely-dead endpoint so bucket_exists fails fast during init.
    monkeypatch.setattr(settings, "minio_endpoint", "127.0.0.1:1")
    reset_object_store_cache()
    try:
        store = get_object_store()
        assert store.backend == "local"  # fell back, no exception
    finally:
        reset_object_store_cache()
