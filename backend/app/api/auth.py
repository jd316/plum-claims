from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import settings
from app.services import auth as auth_service
from app.services.auth import Principal
from app.deps_auth import current_user
from app.api.common import _client_ip

router = APIRouter()

# ---------------------------------------------------------------------------
# Auth endpoints (exist regardless of settings.auth_enabled). Login issues a
# self-signed JWT; /me echoes the principal carried by the bearer token. When
# auth is OFF these still work, and /me returns the synthetic "system" ops user.
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    member_id: str | None = None


@router.post("/api/auth/login", response_model=LoginResponse)
def auth_login(req: LoginRequest, request: Request):
    # Brute-force throttle (only when auth is on): cap attempts per username + client IP.
    # The limiter is resolved from app.main so an existing test that monkeypatches
    # app.main._login_limiter (to isolate its own budget) still takes effect; app.main
    # re-exports the shared instance from app.api.common.
    if settings.auth_enabled:
        from app import main as _main
        client_ip = _client_ip(request)
        if not _main._login_limiter.allow(f"{req.username}|{client_ip}"):
            raise HTTPException(429, detail="Too many login attempts — please wait and retry.")
    principal = auth_service.authenticate(req.username, req.password)
    if principal is None:
        raise HTTPException(401, detail="Invalid username or password")
    return LoginResponse(access_token=auth_service.make_token(principal),
                         role=principal.role, member_id=principal.member_id)

@router.get("/api/auth/config")
def auth_config() -> dict:
    """Public, no-auth config probe so the frontend knows whether to show the
    login wall + role gating. When auth_enabled is False (default), the UI renders
    every page openly exactly as before; when True it requires login."""
    return {"auth_enabled": settings.auth_enabled,
            "show_role_help": settings.show_role_help}

@router.get("/api/auth/me")
def auth_me(user: Principal | None = Depends(current_user)):
    if user is None:  # auth on + no/invalid token
        raise HTTPException(401, detail="Authentication required")
    return {"username": user.username, "role": user.role, "member_id": user.member_id}


@router.post("/api/auth/logout")
def auth_logout(authorization: str | None = Header(default=None)):
    """Revoke the presented bearer token (by jti) until its natural expiry, so a stolen
    or logged-out token stops working immediately. No-op when auth is off or no valid
    token is presented (always 200 — logout must never error)."""
    if not settings.auth_enabled:
        return {"status": "ok"}
    import time as _time
    from app.deps_auth import _bearer_token
    from app.services.token_revocation import get_revocation_store
    token = _bearer_token(authorization)
    if not token:
        return {"status": "ok"}
    try:
        claims = auth_service.decode_token(token)
    except auth_service.TokenError:
        return {"status": "ok"}  # already invalid/expired
    ttl = int(claims.get("exp", 0)) - int(_time.time())
    get_revocation_store().revoke(claims.get("jti"), ttl)
    return {"status": "ok", "revoked": True}
