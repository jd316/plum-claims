"""FastAPI RBAC dependencies for the claims API.

Behaviour is gated entirely by `settings.auth_enabled`:

  * OFF (default): every dependency is a permissive no-op that returns the
    synthetic `SYSTEM_PRINCIPAL` (ops role). No bearer token is read, no DB users
    table is needed, nothing raises → all existing endpoints, tests, UI, and the
    12/12 eval behave EXACTLY as before. The OFF path must remain a true no-op.

  * ON: a valid bearer token is required; `require_ops` enforces ops-role; the
    ownership helper scopes members to their own member_id (403 otherwise) while
    ops can access everything.

These are thin request-dependencies; the crypto/store lives in app/services/auth.py.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException

from app.config import settings
from app.services.auth import (
    Principal,
    SYSTEM_PRINCIPAL,
    TokenError,
    decode_token,
    principal_from_claims,
)


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def current_user(authorization: str | None = Header(default=None)) -> Principal | None:
    """Optional principal. OFF → SYSTEM_PRINCIPAL. ON → decode the bearer if
    present (invalid token → 401); no token → None (anonymous)."""
    if not settings.auth_enabled:
        return SYSTEM_PRINCIPAL
    token = _bearer_token(authorization)
    if token is None:
        return None
    try:
        return principal_from_claims(decode_token(token))
    except TokenError:
        raise HTTPException(401, detail="Invalid or expired token")


def require_user(user: Principal | None = Depends(current_user)) -> Principal:
    """A valid authenticated principal is required. OFF → SYSTEM_PRINCIPAL."""
    if not settings.auth_enabled:
        return SYSTEM_PRINCIPAL
    if user is None:
        raise HTTPException(401, detail="Authentication required")
    return user


def require_ops(user: Principal = Depends(require_user)) -> Principal:
    """Ops-only. OFF → SYSTEM_PRINCIPAL (which is ops). ON → 403 for non-ops."""
    if not settings.auth_enabled:
        return SYSTEM_PRINCIPAL
    if not user.is_ops:
        raise HTTPException(403, detail="Ops role required")
    return user


def require_owner_or_ops(claim_member_id: str | None,
                         user: Principal) -> Principal:
    """Authorize access to a resource scoped to `claim_member_id`. Ops always pass;
    a member passes only for their own member_id. OFF → always allowed. This is a
    PLAIN helper (not a Depends) because the member_id is only known after a lookup
    inside the endpoint."""
    if not settings.auth_enabled:
        return SYSTEM_PRINCIPAL
    if not user.can_access_member(claim_member_id):
        raise HTTPException(403, detail="Not authorized for this claim")
    return user
