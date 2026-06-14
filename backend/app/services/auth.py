"""Self-issued JWT auth + user store for the claims product.

This module is the single home for password hashing, token issue/verify, the user
CRUD/store, and idempotent seeding. It is intentionally decoupled from FastAPI: the
RBAC request-dependencies live in `app/deps_auth.py` and call into here. Everything
here is dormant unless `settings.auth_enabled` is True — but hashing/seeding are
harmless to run regardless, so startup seeding is best-effort and unconditional.

Crypto choices: `bcrypt` (used directly — avoids the passlib/bcrypt 5.x version-probe
breakage) for passwords, and `PyJWT` (HS256) for tokens. No external IdP.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast

import bcrypt
import jwt

from app.config import settings

log = logging.getLogger("plum.auth")

# bcrypt hashes at most the first 72 bytes of a password; longer inputs raise on
# bcrypt 5.x. We truncate explicitly (a documented, standard practice) so arbitrary
# length passwords are accepted deterministically.
_BCRYPT_MAX_BYTES = 72


# --------------------------------------------------------------------------- #
# Principal — the resolved identity an endpoint sees. Decoupled from the ORM   #
# row so the no-op (auth-off) path can synthesise one without any DB.          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Principal:
    username: str
    role: str  # "member" | "ops"
    member_id: str | None = None

    @property
    def is_ops(self) -> bool:
        return self.role == "ops"

    def can_access_member(self, target_member_id: str | None) -> bool:
        """Ops can access any member's claims; a member only their own."""
        if self.is_ops:
            return True
        return target_member_id is not None and target_member_id == self.member_id


# The synthetic principal returned by the RBAC dependencies when auth is OFF.
# It is ops-role so every ownership check passes → endpoints behave as today.
SYSTEM_PRINCIPAL = Principal(username="system", role="ops", member_id=None)


# --------------------------------------------------------------------------- #
# Password hashing                                                            #
# --------------------------------------------------------------------------- #
def _to_bcrypt_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_to_bcrypt_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bcrypt_bytes(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed stored hash → treat as a non-match rather than raising.
        return False


# --------------------------------------------------------------------------- #
# JWT issue / verify                                                          #
# --------------------------------------------------------------------------- #
class TokenError(Exception):
    """Raised by decode_token on an expired/invalid/malformed token."""


def make_token(user: "Principal | object", expires_minutes: int | None = None) -> str:
    """Issue an HS256 JWT for a principal or a UserRow. Carries sub(username),
    role, member_id, and an exp. Accepts anything exposing those attributes."""
    minutes = settings.jwt_expire_minutes if expires_minutes is None else expires_minutes
    now = datetime.now(timezone.utc)
    payload = {
        "sub": getattr(user, "username"),
        "role": getattr(user, "role"),
        "member_id": getattr(user, "member_id", None),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Verify + decode a token to its claims dict. Raises TokenError on any
    invalid/expired/tampered token (never leaks the underlying jwt exception type)."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as e:
        raise TokenError(str(e)) from e


def principal_from_claims(claims: dict) -> Principal:
    return Principal(
        username=cast(str, claims.get("sub")),
        role=claims.get("role", "member"),
        member_id=claims.get("member_id"),
    )


# --------------------------------------------------------------------------- #
# User store (DB-backed). All functions open their own session and are safe to #
# call when auth is off; only seeding runs unconditionally at startup.         #
# --------------------------------------------------------------------------- #
def create_user(username: str, password: str, role: str,
                member_id: str | None = None) -> "object":
    """Insert a user, returning the row. Raises on duplicate username (unique)."""
    from app.services.persistence import Session, UserRow
    with Session() as s:
        row = UserRow(username=username, password_hash=hash_password(password),
                      role=role, member_id=member_id)
        s.add(row)
        s.commit()
        s.refresh(row)
        # Detach a plain Principal-friendly copy so callers don't touch a closed session.
        return Principal(username=row.username, role=row.role, member_id=row.member_id)


def get_user(username: str) -> "_UserSnapshot | None":
    """Return the UserRow for `username`, or None. Caller must not access lazy
    attributes after the session closes — we return the live row for password
    checks inside authenticate(); other callers should read eagerly."""
    from app.services.persistence import Session, UserRow
    with Session() as s:
        row = s.query(UserRow).filter(UserRow.username == username).one_or_none()
        if row is None:
            return None
        # Return a frozen snapshot (id + fields) so it's safe post-session.
        return _UserSnapshot(
            id=row.id, username=row.username, password_hash=row.password_hash,
            role=row.role, member_id=row.member_id)


@dataclass(frozen=True)
class _UserSnapshot:
    id: str
    username: str
    password_hash: str
    role: str
    member_id: str | None


def authenticate(username: str, password: str) -> Principal | None:
    """Return a Principal on a valid username+password, else None."""
    user = get_user(username)
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return Principal(username=user.username, role=user.role, member_id=user.member_id)


# --------------------------------------------------------------------------- #
# Seeding — idempotent. One ops account + one member account per policy member.#
# --------------------------------------------------------------------------- #
def seed_users() -> dict:
    """Best-effort, idempotent seed of the auth users:
      * one `ops` account (username `ops`, password settings.ops_default_password)
      * one `member` account per policy member (username = member_id, e.g. EMP001),
        role member, member_id set, password settings.member_default_password.

    Re-running never duplicates (skips usernames that already exist). Returns a
    small summary dict. A DB outage / missing table logs and yields {} rather than
    raising — seeding is harmless and never blocks startup."""
    from app.services.persistence import Session, UserRow
    from app.services.policy_engine import get_policy_engine

    created: list[str] = []
    try:
        with Session() as s:
            existing = {u for (u,) in s.query(UserRow.username).all()}

            def _ensure(username: str, password: str, role: str,
                        member_id: str | None) -> None:
                if username in existing:
                    return
                s.add(UserRow(username=username, password_hash=hash_password(password),
                              role=role, member_id=member_id))
                created.append(username)

            _ensure("ops", settings.ops_default_password, "ops", None)
            pe = get_policy_engine(settings.policy_path)
            for m in pe.members():
                mid = m["member_id"]
                _ensure(mid, settings.member_default_password, "member", mid)
            s.commit()
    except Exception as e:  # noqa: BLE001 — seeding must never break startup
        log.warning("seed_users skipped/failed (DB down or table missing?): %s", e)
        return {}
    if created:
        log.info("seed_users created %d account(s): %s", len(created), ", ".join(created))
    return {"created": created}
