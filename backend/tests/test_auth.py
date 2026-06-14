"""Auth + RBAC tests (Round 4). Deterministic where possible.

* hashing / token / dependency-behaviour tests are pure (no DB, no Gemini).
* DB-backed tests (seed/authenticate) skip cleanly if Postgres is down.
* The login→me + ops-gating flow uses TestClient (no Gemini); it never triggers
  the decision pipeline, so it is NOT marked live.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import tests.conftest  # noqa: F401 — inserts backend/ on sys.path
from app.config import settings
from app.services import auth as A
from app.services.auth import Principal, SYSTEM_PRINCIPAL


# --------------------------------------------------------------------------- #
# Password hashing                                                            #
# --------------------------------------------------------------------------- #
def test_hash_verify_roundtrip():
    h = A.hash_password("s3cret-pw")
    assert h != "s3cret-pw"
    assert A.verify_password("s3cret-pw", h) is True
    assert A.verify_password("wrong-pw", h) is False


def test_verify_malformed_hash_is_false():
    assert A.verify_password("anything", "not-a-bcrypt-hash") is False


def test_long_password_truncated_deterministically():
    # >72 bytes must not raise and must round-trip.
    pw = "a" * 200
    h = A.hash_password(pw)
    assert A.verify_password(pw, h) is True


# --------------------------------------------------------------------------- #
# JWT issue / verify                                                          #
# --------------------------------------------------------------------------- #
def test_make_decode_token_roundtrip():
    p = Principal(username="EMP001", role="member", member_id="EMP001")
    claims = A.decode_token(A.make_token(p))
    assert claims["sub"] == "EMP001"
    assert claims["role"] == "member"
    assert claims["member_id"] == "EMP001"
    assert A.principal_from_claims(claims) == p


def test_expired_token_rejected():
    p = Principal(username="ops", role="ops")
    token = A.make_token(p, expires_minutes=-1)  # already expired
    with pytest.raises(A.TokenError):
        A.decode_token(token)


def test_tampered_token_rejected():
    token = A.make_token(Principal(username="ops", role="ops"))
    with pytest.raises(A.TokenError):
        A.decode_token(token + "x")


def test_wrong_secret_rejected(monkeypatch):
    token = A.make_token(Principal(username="ops", role="ops"))
    monkeypatch.setattr(settings, "jwt_secret", "a-different-secret")
    with pytest.raises(A.TokenError):
        A.decode_token(token)


# --------------------------------------------------------------------------- #
# Principal ownership logic (pure)                                            #
# --------------------------------------------------------------------------- #
def test_principal_ownership():
    ops = Principal(username="ops", role="ops")
    member = Principal(username="EMP001", role="member", member_id="EMP001")
    assert ops.can_access_member("EMP001") is True
    assert ops.can_access_member("EMP999") is True
    assert member.can_access_member("EMP001") is True
    assert member.can_access_member("EMP002") is False
    assert member.can_access_member(None) is False


# --------------------------------------------------------------------------- #
# RBAC dependencies — OFF path is a permissive no-op; ON path enforces.        #
# --------------------------------------------------------------------------- #
from app.deps_auth import (current_user, require_user, require_ops,  # noqa: E402
                           require_owner_or_ops)


def test_dependencies_noop_when_auth_off(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    # No token needed; all return the synthetic system/ops principal.
    assert current_user(authorization=None) is SYSTEM_PRINCIPAL
    assert require_user(user=current_user(authorization=None)) is SYSTEM_PRINCIPAL
    assert require_ops(user=require_user(user=SYSTEM_PRINCIPAL)) is SYSTEM_PRINCIPAL
    # Ownership helper is always permissive when off (even for a foreign member_id).
    assert require_owner_or_ops("EMP999", SYSTEM_PRINCIPAL) is SYSTEM_PRINCIPAL


def test_require_user_requires_token_when_on(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    with pytest.raises(HTTPException) as ei:
        require_user(user=current_user(authorization=None))
    assert ei.value.status_code == 401


def test_current_user_invalid_token_401_when_on(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    with pytest.raises(HTTPException) as ei:
        current_user(authorization="Bearer not.a.valid.token")
    assert ei.value.status_code == 401


def test_member_scoping_when_on(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    member = Principal(username="EMP001", role="member", member_id="EMP001")
    # Own member_id → allowed.
    assert require_owner_or_ops("EMP001", member) is member
    # Another member_id → 403.
    with pytest.raises(HTTPException) as ei:
        require_owner_or_ops("EMP002", member)
    assert ei.value.status_code == 403


def test_ops_accesses_all_and_eval_gate_when_on(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    ops = Principal(username="ops", role="ops")
    member = Principal(username="EMP001", role="member", member_id="EMP001")
    assert require_ops(user=ops) is ops
    assert require_owner_or_ops("EMP999", ops) is ops  # ops reads anyone
    with pytest.raises(HTTPException) as ei:
        require_ops(user=member)  # member hitting an ops-only route → 403
    assert ei.value.status_code == 403


# --------------------------------------------------------------------------- #
# DB-backed: seeding + authenticate (skip if Postgres is down).               #
# --------------------------------------------------------------------------- #
def _db_up() -> bool:
    try:
        from app.services.persistence import engine
        engine.connect().close()
        return True
    except Exception:
        return False


db_required = pytest.mark.skipif(not _db_up(), reason="Postgres not available")


@db_required
def test_seed_and_authenticate():
    from app.services.persistence import init_db
    init_db()
    A.seed_users()  # idempotent
    A.seed_users()  # second call must not error or duplicate

    # ops authenticates with the documented default dev password.
    ops = A.authenticate("ops", settings.ops_default_password)
    assert ops is not None and ops.role == "ops" and ops.member_id is None

    # a member account exists for EMP001 with role member + member_id set.
    m = A.authenticate("EMP001", settings.member_default_password)
    assert m is not None and m.role == "member" and m.member_id == "EMP001"

    # bad creds → None.
    assert A.authenticate("ops", "wrong") is None
    assert A.authenticate("nobody", "whatever") is None


# --------------------------------------------------------------------------- #
# login → me flow + eval ops-gating via TestClient (no Gemini).               #
# --------------------------------------------------------------------------- #
@db_required
def test_login_me_and_eval_gate_when_on(monkeypatch):
    from app.services.persistence import init_db
    init_db()
    A.seed_users()
    monkeypatch.setattr(settings, "auth_enabled", True)

    from app.main import app
    client = TestClient(app)

    # Member login returns a token + role/member_id.
    r = client.post("/api/auth/login",
                    json={"username": "EMP001", "password": settings.member_default_password})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "member" and body["member_id"] == "EMP001"
    token = body["access_token"]

    # /me with the bearer echoes the principal.
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["username"] == "EMP001"

    # /me without a token → 401 when auth is on.
    assert client.get("/api/auth/me").status_code == 401

    # Member hitting ops-only /api/eval/run → 403.
    assert client.post("/api/eval/run",
                       headers={"Authorization": f"Bearer {token}"}).status_code == 403

    # Bad login → 401.
    assert client.post("/api/auth/login",
                       json={"username": "EMP001", "password": "nope"}).status_code == 401


def test_auth_config_reflects_settings(monkeypatch):
    """The public /api/auth/config probe mirrors settings.auth_enabled (login wall) and
    settings.show_role_help (the optional Operator|Member login toggle); needs no token/DB."""
    from app.main import app
    client = TestClient(app)
    monkeypatch.setattr(settings, "show_role_help", False)
    monkeypatch.setattr(settings, "auth_enabled", False)
    assert client.get("/api/auth/config").json() == {"auth_enabled": False, "show_role_help": False}
    monkeypatch.setattr(settings, "auth_enabled", True)
    assert client.get("/api/auth/config").json() == {"auth_enabled": True, "show_role_help": False}
    monkeypatch.setattr(settings, "show_role_help", True)
    assert client.get("/api/auth/config").json() == {"auth_enabled": True, "show_role_help": True}


@db_required
def test_ops_login_can_reach_members_when_on(monkeypatch):
    from app.services.persistence import init_db
    init_db()
    A.seed_users()
    monkeypatch.setattr(settings, "auth_enabled", True)

    from app.main import app
    client = TestClient(app)
    r = client.post("/api/auth/login",
                    json={"username": "ops", "password": settings.ops_default_password})
    token = r.json()["access_token"]
    # ops can list members; an anonymous request is rejected.
    assert client.get("/api/members",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.get("/api/members").status_code == 401
