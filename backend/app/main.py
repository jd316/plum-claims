import logging, os, uuid
from contextlib import asynccontextmanager
from app.config import settings

# --- Sub-feature C: optional LangSmith tracing (env-gated, no-op without key) ---
# Export the LangChain env vars BEFORE app.graph.build is imported below, so
# LangGraph picks them up and auto-traces the execution tree to LangSmith. When
# the flag/key are unset this does nothing and the app runs exactly as before.
def _maybe_enable_langsmith() -> None:
    if settings.langsmith_tracing and settings.langsmith_api_key:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_API_KEY", settings.langsmith_api_key)
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.langsmith_project)
        logging.getLogger("plum.claims").info(
            "LangSmith tracing enabled (project=%s)", settings.langsmith_project)
_maybe_enable_langsmith()

# --- PHI/privacy: PII masking in logs (ALWAYS ON, independent of any flag) -------
# Install a logging.Filter that redacts member names, long digit runs and emails from
# log records on the plum.* loggers + root handlers, so logs never leak PII regardless
# of the at-rest encryption flag. Best-effort: a setup failure must not block startup.
def _install_pii_log_masking() -> None:
    try:
        from app.config import settings as _s
        from app.services.log_filter import install_pii_masking, configure_json_logging
        # JSON output FIRST (sets the root handler's formatter), then PII masking so the
        # filter is attached to that handler and redacts fields before they're serialized.
        if _s.json_logs:
            configure_json_logging()
        install_pii_masking("plum.claims", "plum.persistence", "plum.audit",
                            "plum.crypto", "plum.auth", "plum.worker")
    except Exception as e:  # noqa: BLE001 — logging hardening must never crash boot
        logging.getLogger("plum.claims").warning("PII log masking install failed: %s", e)
_install_pii_log_masking()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from app.services import auth as auth_service
from app.services import persistence
from app.services import policy_store
from app.services.idempotency import get_store as get_idempotency_store

log = logging.getLogger("plum.claims")

# Re-export the shared client-IP helper from app.api.common so that an existing test
# which imports `app.main._client_ip` still resolves after the extraction. The login
# rate limiter lives in app.api.common too; the auth router resolves it via app.main
# so tests that monkeypatch app.main._login_limiter continue to take effect.
from app.api.common import _client_ip, _login_limiter  # noqa: E402,F401


def _check_insecure_defaults() -> None:
    """Refuse to boot in production with insecure security defaults; warn loudly
    otherwise. Tests run with app_env=development → warning only, never a crash."""
    is_prod = settings.is_production
    problems: list[str] = []
    if settings.auth_enabled and settings.jwt_secret == "dev-insecure-change-me":
        problems.append("AUTH_ENABLED=true but JWT_SECRET is the insecure dev default "
                        "('dev-insecure-change-me') — set a strong JWT_SECRET.")
    elif settings.auth_enabled and len(settings.jwt_secret) < 48:
        # HS256 needs >= 32 bytes (RFC 7518 §3.2); we require the 48 that DEPLOY.md's
        # `openssl rand -base64 48` produces, so a short custom secret can't slip through.
        problems.append(f"AUTH_ENABLED=true but JWT_SECRET is only {len(settings.jwt_secret)} "
                        "chars — production requires >= 48 (RFC 7518 §3.2 floor is 32). "
                        "Use `openssl rand -base64 48`.")
    if settings.phi_encryption_enabled and not settings.phi_encryption_key:
        problems.append("PHI_ENCRYPTION_ENABLED=true but PHI_ENCRYPTION_KEY is empty — "
                        "set a PHI_ENCRYPTION_KEY (else a weak dev key is derived).")
    # The seed passwords are documented dev defaults in the repo — a deployed prod instance
    # must never run with them, or anyone reading the source could log in. Refuse to boot.
    if settings.auth_enabled and settings.ops_default_password == "ops-dev-password":
        problems.append("AUTH_ENABLED=true but OPS_DEFAULT_PASSWORD is the documented dev "
                        "default ('ops-dev-password') — set a strong OPS_DEFAULT_PASSWORD.")
    if settings.auth_enabled and settings.member_default_password == "member-dev-password":
        problems.append("AUTH_ENABLED=true but MEMBER_DEFAULT_PASSWORD is the documented dev "
                        "default ('member-dev-password') — set a strong MEMBER_DEFAULT_PASSWORD.")
    # Secure-by-default in production: serving/storing PHI requires auth + at-rest
    # encryption ON, and non-default object-store credentials. These are prod-only
    # (guarded by is_prod) so development / tests / the eval are unaffected.
    if is_prod and not settings.auth_enabled:
        problems.append("APP_ENV=production but AUTH_ENABLED is false — refusing to serve PHI "
                        "without authentication.")
    if is_prod and not settings.phi_encryption_enabled:
        problems.append("APP_ENV=production but PHI_ENCRYPTION_ENABLED is false — refusing to "
                        "store PHI unencrypted at rest.")
    if is_prod and settings.object_store == "minio" and "minioadmin" in (
            settings.minio_access_key, settings.minio_secret_key):
        problems.append("APP_ENV=production with object_store=minio but MinIO credentials are the "
                        "default 'minioadmin' — set strong MINIO_ACCESS_KEY/MINIO_SECRET_KEY.")
    if not problems:
        return
    if is_prod:
        raise RuntimeError("Refusing to boot in production with insecure defaults: "
                           + " ".join(problems))
    for p in problems:
        log.warning("SECURITY WARNING: %s", p)


@asynccontextmanager
async def lifespan(app):
    # Fail-fast (production) / warn (dev) on insecure security defaults before anything else.
    _check_insecure_defaults()
    # Tolerant startup: a down DB must not prevent the app (and /api/health) from booting.
    # The pipeline works without the DB; listing endpoints fail later with a clean error.
    try:
        persistence.init_db()
    except Exception as e:
        # Production: a failed migration / DB init must FAIL-FAST (don't serve a stale
        # or broken schema). Dev/test stay tolerant so the app boots without a DB.
        if settings.is_production:
            raise
        log.warning("DB init failed at startup; continuing without persistence: %s", e)
    # Best-effort idempotent user seeding (ops + one per policy member). Harmless when
    # auth is off; a down DB / missing table logs and is swallowed inside seed_users().
    try:
        auth_service.seed_users()
    except Exception as e:  # noqa: BLE001 — seeding must never block startup
        log.warning("seed_users at startup skipped/failed: %s", e)
    # Best-effort idempotent policy-studio seeding: v1 == current policy_terms.json,
    # active. Idempotent (no-op once seeded). A down DB / missing table is swallowed.
    # NOTHING else is activated, so active == original file → the 12/12 eval is unchanged.
    try:
        policy_store.seed_initial_version()
    except Exception as e:  # noqa: BLE001 — seeding must never block startup
        log.warning("seed_initial_version at startup skipped/failed: %s", e)
    yield

app = FastAPI(title="Plum Claims Processing", lifespan=lifespan)
_cors_origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()] or ["*"]
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_methods=["*"], allow_headers=["*"])


from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# Labelled by ROUTE TEMPLATE (e.g. /api/claims/{claim_id}), never the raw path, so
# per-claim ids don't explode label cardinality.
_REQ_COUNT = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
_REQ_LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency (s)",
                         ["method", "route"])


@app.middleware("http")
async def _request_context(request: Request, call_next):
    """Per-request: assign/propagate a correlation id (returned as X-Request-ID), emit
    one structured access line, and record Prometheus request count + latency so the
    API is traceable AND monitorable end-to-end."""
    import time as _time
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    start = _time.monotonic()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        dur_s = _time.monotonic() - start
        route = request.scope.get("route")
        route_label = getattr(route, "path", None) or "unmatched"
        try:
            _REQ_COUNT.labels(request.method, route_label, str(status)).inc()
            _REQ_LATENCY.labels(request.method, route_label).observe(dur_s)
        except Exception:  # noqa: BLE001 — metrics must never break a request
            pass
        log.info("request id=%s method=%s path=%s status=%s dur_ms=%s",
                 rid, request.method, request.url.path, status, round(dur_s * 1000, 1))


@app.get("/metrics")
def metrics() -> Response:
    """Prometheus scrape endpoint (request count/latency by route+status). No PHI —
    labels are route templates and methods only."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Last-resort handler: log the real error server-side, return a clean envelope
    to the client (never a stack trace). HTTPException/validation errors are handled
    by FastAPI's own handlers and never reach here."""
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/api/health")
def health() -> dict:
    """Liveness — the process is up. Always 200 (used by container healthchecks)."""
    return {"status": "ok"}


@app.get("/api/ready")
def ready():
    """Readiness — dependencies reachable. 200 only if Postgres answers; Redis is
    reported but not required (the app degrades to sync/in-memory without it). Use
    this (not /api/health) to gate traffic in an orchestrator."""
    checks: dict = {}
    try:
        from sqlalchemy import text as _text
        with persistence.engine.connect() as c:
            c.execute(_text("select 1"))
        checks["db"] = "ok"
    except Exception as e:  # noqa: BLE001 — readiness must report, not raise
        checks["db"] = f"error: {str(e)[:80]}"
    checks["redis"] = "ok" if get_idempotency_store().ping() else "unavailable"
    ok = checks.get("db") == "ok"
    return JSONResponse(status_code=200 if ok else 503,
                        content={"status": "ready" if ok else "not ready", "checks": checks})

# ---------------------------------------------------------------------------
# Router registration. Each module under app.api defines a bare APIRouter with
# routes moved verbatim from this file. They are included here in the SAME
# relative order the routes were originally defined, so path match-order (and
# the OpenAPI surface) is preserved exactly.
# ---------------------------------------------------------------------------
from app.api import auth as _auth_router
from app.api import eval as _eval_router
from app.api import policy as _policy_router
from app.api import intake as _intake_router
from app.api import claims_read as _claims_read_router
from app.api import explain as _explain_router
from app.api import ops_actions as _ops_actions_router
from app.api import assistant as _assistant_router

app.include_router(_auth_router.router)
app.include_router(_eval_router.router)
app.include_router(_policy_router.router)
app.include_router(_intake_router.router)
app.include_router(_claims_read_router.router)
app.include_router(_explain_router.router)
app.include_router(_ops_actions_router.router)
app.include_router(_assistant_router.router)
