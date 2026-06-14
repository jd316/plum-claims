"""Tests for the final-audit fixes (not marked live — no Gemini):

  * audit_log table is created by init_db on a fresh DB (DB-dependent).
  * GET /api/claims/{id}/audit returns the append-only trail (ops-only).
  * Idempotency-replay enforces ownership: 200 for the owner, 403 for a foreign
    member when auth is ON (and a permissive no-op when auth is OFF).
  * The 4 read-only LLM/estimate endpoints require auth when ON, open when OFF.
  * The insecure-default startup check warns (does not crash) in development.

DB-backed tests skip cleanly if Postgres is down.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

import tests.conftest  # noqa: F401 — inserts backend/ on sys.path
from app.config import settings
from app.main import app
from app.models.schemas import (
    ClaimSubmission, ClaimResult, Decision, DocumentInput, ReasonCode,
)
from app.services.auth import Principal


# --------------------------------------------------------------------------- #
# DB reachability guard.                                                       #
# --------------------------------------------------------------------------- #
def _db_reachable() -> bool:
    try:
        from app.services.persistence import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_DB = _db_reachable()
db_only = pytest.mark.skipif(not _DB, reason="Postgres unreachable")


def _make_submission(member_id: str) -> ClaimSubmission:
    return ClaimSubmission(
        member_id=member_id, policy_id="POL-TEST", claim_category="CONSULTATION",
        treatment_date=date(2026, 1, 15), claimed_amount=1500.0,
        documents=[DocumentInput(file_id="F001", file_name="r.png",
                                 stored_path="/tmp/r.png")])


def _make_result(claim_id: str) -> ClaimResult:
    return ClaimResult(
        claim_id=claim_id, blocked=False,
        decision=Decision(status="APPROVED", approved_amount=1200.0, confidence=0.95,
                          reason_codes=[ReasonCode(code="OK", detail="ok")],
                          member_message="Approved."))


# --------------------------------------------------------------------------- #
# Fix 1: audit_log table exists after init_db.                                #
# --------------------------------------------------------------------------- #
@db_only
def test_audit_log_table_created_by_init_db():
    from app.services.persistence import init_db, engine
    from sqlalchemy import inspect
    init_db()
    assert "audit_log" in inspect(engine).get_table_names()


# --------------------------------------------------------------------------- #
# Fix 7: audit read endpoint returns the trail (ops-only, tolerant).          #
# --------------------------------------------------------------------------- #
@db_only
def test_audit_endpoint_returns_recorded_trail():
    from app.services.persistence import init_db, save_claim
    from app.services.audit import record_decision
    init_db()
    claim_id = f"AUD-{uuid.uuid4().hex[:8]}"
    sub = _make_submission("M-AUD-1")
    result = _make_result(claim_id)
    save_claim(sub, result)
    record_decision(claim_id, result.decision, actor="system")
    with TestClient(app) as client:
        r = client.get(f"/api/claims/{claim_id}/audit")
    assert r.status_code == 200
    trail = r.json()
    assert isinstance(trail, list) and len(trail) >= 1
    assert trail[0]["claim_id"] == claim_id
    assert trail[0]["action"] == "DECISION"


def test_audit_endpoint_tolerant_of_unknown_claim():
    with TestClient(app) as client:
        r = client.get(f"/api/claims/UNKNOWN-{uuid.uuid4().hex[:6]}/audit")
    assert r.status_code == 200
    assert r.json() == []


# --------------------------------------------------------------------------- #
# Fix 2: idempotency-replay enforces ownership (IDOR guard).                   #
# --------------------------------------------------------------------------- #
@db_only
def test_idempotency_replay_ownership(monkeypatch):
    """A replay must 403 for a foreign member and 200 for the owner when auth is ON."""
    from app.services.persistence import init_db, save_claim
    from app.services.idempotency import get_store
    from app.services import auth as A
    init_db()

    owner_id = "EMP001"
    claim_id = f"IDEM-{uuid.uuid4().hex[:8]}"
    sub = _make_submission(owner_id)
    save_claim(sub, _make_result(claim_id))

    key = f"key-{uuid.uuid4().hex[:8]}"
    get_store().put(key, claim_id)

    monkeypatch.setattr(settings, "auth_enabled", True)

    # Foreign member replaying the key → 403 (must not get the owner's claim).
    foreign_tok = A.make_token(Principal(username="EMP002", role="member", member_id="EMP002"))
    with TestClient(app) as client:
        r = client.post("/api/claims",
                        data={"payload": "{}"},
                        files=[("files", ("x.png", b"x", "image/png"))],
                        headers={"Idempotency-Key": key,
                                 "Authorization": f"Bearer {foreign_tok}"})
    assert r.status_code == 403

    # The owner replaying the key → 200 with the stored result, tagged as a replay.
    owner_tok = A.make_token(Principal(username=owner_id, role="member", member_id=owner_id))
    with TestClient(app) as client:
        r = client.post("/api/claims",
                        data={"payload": "{}"},
                        files=[("files", ("x.png", b"x", "image/png"))],
                        headers={"Idempotency-Key": key,
                                 "Authorization": f"Bearer {owner_tok}"})
    assert r.status_code == 200
    body = r.json()
    assert body["claim_id"] == claim_id
    assert body.get("idempotent_replay") is True


@db_only
def test_idempotency_replay_open_when_auth_off(monkeypatch):
    """With auth OFF the replay is a permissive no-op (unchanged behaviour)."""
    from app.services.persistence import init_db, save_claim
    from app.services.idempotency import get_store
    init_db()
    monkeypatch.setattr(settings, "auth_enabled", False)
    claim_id = f"IDEM-{uuid.uuid4().hex[:8]}"
    save_claim(_make_submission("EMP001"), _make_result(claim_id))
    key = f"key-{uuid.uuid4().hex[:8]}"
    get_store().put(key, claim_id)
    with TestClient(app) as client:
        r = client.post("/api/claims", data={"payload": "{}"},
                        files=[("files", ("x.png", b"x", "image/png"))],
                        headers={"Idempotency-Key": key})
    assert r.status_code == 200
    assert r.json()["claim_id"] == claim_id


# --------------------------------------------------------------------------- #
# Fix 3: read-only LLM/estimate endpoints require auth when ON, open when OFF. #
# --------------------------------------------------------------------------- #
_READONLY_GET = ["/api/policy/document-requirements"]
_READONLY_POST = {
    "/api/policy/ask": {"question": "what is the consultation co-pay?"},
    "/api/claims/parse": {"text": "consultation for 1500 at apollo"},
    "/api/estimate": {"claim_category": "CONSULTATION", "claimed_amount": 1500},
}


def test_readonly_endpoints_require_auth_when_on(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    with TestClient(app) as client:
        for path in _READONLY_GET:
            assert client.get(path).status_code == 401, path
        for path, body in _READONLY_POST.items():
            assert client.post(path, json=body).status_code == 401, path
    # classify needs a multipart file; assert the auth gate fires without one.
    with TestClient(app) as client:
        r = client.post("/api/documents/classify",
                        files=[("file", ("x.png", b"x", "image/png"))])
        assert r.status_code == 401


def test_readonly_get_open_when_auth_off(monkeypatch):
    """With auth OFF the read-only endpoints don't require a token (estimate is
    pure-deterministic; assert it returns 200 with no auth header)."""
    monkeypatch.setattr(settings, "auth_enabled", False)
    with TestClient(app) as client:
        assert client.get("/api/policy/document-requirements").status_code == 200
        r = client.post("/api/estimate",
                        json={"claim_category": "CONSULTATION", "claimed_amount": 1500})
        assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Fix 5: insecure-default startup check warns (does not crash) in development. #
# --------------------------------------------------------------------------- #
def test_insecure_default_check_warns_not_crash_in_dev(monkeypatch):
    from app.main import _check_insecure_defaults
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "dev-insecure-change-me")
    # Must NOT raise in development (warning only).
    _check_insecure_defaults()


def test_insecure_default_check_refuses_boot_in_production(monkeypatch):
    from app.main import _check_insecure_defaults
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "dev-insecure-change-me")
    with pytest.raises(RuntimeError):
        _check_insecure_defaults()


def test_insecure_default_check_passes_with_strong_secret(monkeypatch):
    from app.main import _check_insecure_defaults
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "a-strong-production-secret-0123456789-abcdefghijkl")
    # Secure-by-default now requires at-rest PHI encryption ON in production, so a
    # fully-valid prod config supplies both the flag and a key.
    monkeypatch.setattr(settings, "phi_encryption_enabled", True)
    monkeypatch.setattr(settings, "phi_encryption_key", "a-strong-phi-encryption-key-value")
    monkeypatch.setattr(settings, "ops_default_password", "a-strong-ops-password")
    monkeypatch.setattr(settings, "member_default_password", "a-strong-member-password")
    _check_insecure_defaults()  # must not raise


def test_insecure_default_check_refuses_short_jwt_secret_in_prod(monkeypatch):
    """A custom-but-too-short JWT secret (below the HS256 32-byte floor) must block boot."""
    from app.main import _check_insecure_defaults
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "short-secret")  # 12 chars
    monkeypatch.setattr(settings, "phi_encryption_enabled", False)
    monkeypatch.setattr(settings, "ops_default_password", "a-strong-ops-password")
    monkeypatch.setattr(settings, "member_default_password", "a-strong-member-password")
    with pytest.raises(RuntimeError):
        _check_insecure_defaults()


def test_insecure_default_check_refuses_default_seed_passwords_in_prod(monkeypatch):
    """A deployed prod instance must never run with the repo-documented seed passwords —
    otherwise anyone reading the source could log in. JWT/PHI are strong here; only the
    default ops/member passwords remain, and that alone must block boot."""
    from app.main import _check_insecure_defaults
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "a-strong-production-secret-0123456789-abcdefghijkl")
    monkeypatch.setattr(settings, "phi_encryption_enabled", False)
    # ops_default_password / member_default_password left at their dev defaults.
    with pytest.raises(RuntimeError):
        _check_insecure_defaults()
