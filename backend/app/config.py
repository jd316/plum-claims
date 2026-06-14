import pathlib

from pydantic_settings import BaseSettings, SettingsConfigDict

# Candidate roots to resolve relative asset paths against, regardless of the
# process CWD: the CWD itself, the backend dir, and the repo root (its parent).
_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
_SEARCH_ROOTS = (pathlib.Path.cwd(), _BACKEND_DIR, _BACKEND_DIR.parent)


def _resolve_asset(path: str) -> str:
    p = pathlib.Path(path)
    if p.is_absolute():
        return str(p)
    for root in _SEARCH_ROOTS:
        candidate = root / p
        if candidate.exists():
            return str(candidate)
    return path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    # Deployment environment marker (env APP_ENV). "production" makes insecure
    # defaults (a dev JWT secret with auth on, an empty PHI key with encryption on)
    # a hard boot refusal; any other value (default "development") only warns.
    app_env: str = "development"
    gemini_api_key: str = ""
    # Default to the current-latest aliases (gemini-flash-latest → 3.5-flash,
    # gemini-pro-latest → 3.1-pro-preview). NOTE: aliases roll to newer models over
    # time, so pin an explicit version if you need a reproducible eval. The Pro alias
    # is PAID (drives the per-claim verifier); override GEMINI_PRO_MODEL to a Flash
    # model to stay fully on the free tier.
    gemini_model: str = "gemini-flash-latest"
    gemini_pro_model: str = "gemini-pro-latest"
    # Hard wall-clock timeout for every Gemini HTTP call (ms). Bounds threadpool
    # exhaustion if the upstream hangs; the resilience layer (breaker + retries +
    # flash→pro fallback) handles the resulting error. Generous default so normal
    # vision calls (typically < 30 s) never trip it.
    gemini_timeout_ms: int = 90000
    database_url: str = "postgresql+psycopg://plum:plum@localhost:5432/claims"
    # DB connection pool sizing (per process). Defaults are conservative for a single
    # box; size against Postgres max_connections when running multiple replicas.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # Comma-separated browser origin allowlist for CORS. Default "*" preserves the
    # original permissive behaviour (safe here: auth is Bearer-token, not cookie, so
    # a wildcard is not a credential-theft vector). Set to specific origins in prod.
    cors_allow_origins: str = "*"

    # --- Async claim processing (Celery + self-hosted Redis) -------------------
    # Redis is an OSS container (like Postgres), not a third-party service. The
    # broker/result-backend default to redis_url so a single REDIS_URL env var is
    # enough to point the API + worker at the same Redis. Connections are lazy:
    # importing app.worker / app.main never requires Redis to be up.
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = ""
    celery_result_backend: str = ""

    policy_path: str = "policy_terms.json"
    test_cases_path: str = "test_cases.json"
    storage_dir: str = "storage"
    degradation_penalty: float = 0.20

    # --- Production policy enforcement flags (all OFF by default) ---------------
    # Each flag gates a policy rule that policy_terms.json defines but that the 12
    # assignment cases (2024 treatment dates, no PED markers, generic-drug bills, no
    # alt-medicine claims) cannot exercise. Default OFF → the rule is a provable PASS
    # / no-op, so the 12-case + synthetic eval are byte-identical. Production flips
    # each ON. See architecture.md §10a (deferred → enforced-but-gated).
    #
    # submission_deadline_enabled: reject a claim submitted more than
    # submission_rules.deadline_days_from_treatment days after the treatment date,
    # measured against ClaimSubmission.submission_date (or today() when absent). OFF
    # by default because the cases use 2024 dates that would all be late.
    submission_deadline_enabled: bool = False
    # sub_limit_scope: how a category sub_limit is interpreted. "per_line_item" (default)
    # caps the consultation-fee line, leaving per_claim_limit as the binding whole-claim
    # cap — the documented §12a reading that reproduces TC004/TC008/TC010. "whole_claim"
    # is the literal alternative (insurer-confirmable); switching it is a config change,
    # no code change. Only affects CONSULTATION (other categories always cap by sub_limit).
    sub_limit_scope: str = "per_line_item"
    # category_match_enforcement_enabled: when the semantic mapper is confident the
    # treatment does not match the filed claim_category, route to MANUAL_REVIEW.
    category_match_enforcement_enabled: bool = False
    category_match_min_confidence: float = 0.7
    # pre_existing_condition_check_enabled: enforce pre_existing_conditions_days against
    # a member's pre_existing_condition_eligible_from enrolment marker (when present).
    pre_existing_condition_check_enabled: bool = False
    # alt_med_session_limit_enabled / practitioner_registration_check_enabled: enforce
    # alternative_medicine.max_sessions_per_year (needs the API-layer session count) and
    # requires_registered_practitioner (a well-formed doctor_registration on the Rx).
    alt_med_session_limit_enabled: bool = False
    practitioner_registration_check_enabled: bool = False
    # generic_mandatory_enabled: disallow a branded pharmacy line when a generic
    # substitute exists (LineItem.has_generic_alternative). Needs a formulary signal.
    generic_mandatory_enabled: bool = False

    # --- Object storage abstraction (local default / optional self-hosted MinIO) ---
    # `object_store="local"` (default) is byte-identical to the original behaviour:
    # files live on disk under storage_dir and are served from there. Setting it to
    # "minio" routes puts/reads through an S3-compatible self-hosted MinIO. MinIO is
    # an OSS container (like Postgres/Redis), not a third-party service. The backend
    # is best-effort: if MinIO is unconfigured/unreachable it falls back to local and
    # logs, so selecting minio can never crash the app.
    object_store: str = "local"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "claims"
    minio_secure: bool = False  # http for self-hosted dev; set True behind TLS

    # --- Agentic self-correction loop (extraction stage) -----------------------
    # When the first (flash) extraction returns a null / low-confidence load-bearing
    # field on a READABLE document, the extractor re-runs once on the stronger Pro
    # model with a targeted re-prompt and keeps the higher-confidence fields. Clean
    # docs extract with high confidence (>=0.9), so this never fires for them — it is
    # purely additive. The threshold is intentionally well below clean confidence.
    extraction_lowconf_threshold: float = 0.6
    self_correction_enabled: bool = True

    # --- Extraction cache (content-addressed, additive) ------------------------
    # Vision extraction is a pure function of (file bytes, model), so its result is
    # cached under sha256(bytes):model in an in-process LRU (+ optional Redis). Within
    # one eval run every rendered document is unique → every key is new → no hits →
    # identical behaviour. A re-upload of the SAME bytes later is a hit (no Gemini).
    extraction_cache_enabled: bool = True

    # --- Sub-feature A: estimated LLM cost (production cost-awareness) ----------
    # Approximate Gemini per-1M-token prices in USD, converted to INR. These are
    # ESTIMATES for surfacing rough per-claim cost — not billing-accurate. Tune
    # freely; public flash/pro rates drift over time. Rates are USD per 1M tokens.
    # Current (mid-2026) list prices: gemini-3.5-flash $1.50/$9.00; gemini-3.1-pro
    # (≤200K ctx) $2.00/$12.00. Update if you pin different models.
    gemini_flash_input_usd_per_1m: float = 1.50
    gemini_flash_output_usd_per_1m: float = 9.00
    gemini_pro_input_usd_per_1m: float = 2.00
    gemini_pro_output_usd_per_1m: float = 12.00
    usd_to_inr: float = 84.0  # approximate FX; estimate only

    # --- Sub-feature C: optional LangSmith tracing (env-gated, no-op without key)
    # When both are set, app/main.py exports LANGCHAIN_TRACING_V2 / LANGCHAIN_API_KEY
    # BEFORE the graph is imported so LangGraph auto-traces to LangSmith. The app
    # runs perfectly without these — LangSmith is purely additive observability.
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "plum-claims"

    # --- Confidence calibration (OFF by default) -------------------------------
    # When enabled AND a fitted calibrator file loads, the composite confidence is
    # mapped through it so the score is statistically meaningful ("0.9 ≈ 90%
    # correct"). Default OFF -> compute() returns the raw composite EXACTLY as
    # before, so the 12 cases' confidences and the 12/12 thresholds are unchanged.
    confidence_calibration_enabled: bool = False
    calibration_map_path: str = "calibration_map.json"

    # --- Resilience layer (circuit breaker / fallback / concurrency cap) --------
    # Engage ONLY on failure/overload, so clean runs (the 12 cases) behave identically:
    # the breaker never opens, the semaphore is non-blocking at low concurrency, and the
    # model fallback is never invoked. circuit_breaker_threshold consecutive INFRA
    # failures open the breaker; it fails fast for circuit_breaker_reset_seconds then
    # allows one HALF_OPEN trial. max_concurrent_llm caps in-flight Gemini calls to
    # protect the rate limit. Set resilience_enabled=False to bypass the wrapper entirely.
    circuit_breaker_threshold: int = 5
    circuit_breaker_reset_seconds: float = 30
    max_concurrent_llm: int = 8
    resilience_enabled: bool = True

    # --- Self-issued JWT auth + RBAC (OFF by default) --------------------------
    # When auth_enabled=False (default) the RBAC FastAPI dependencies are permissive
    # no-ops that return a synthetic "system" ops principal, so every existing
    # endpoint, the 12/12 eval (run_claim, not the API), and the live api/documents
    # tests behave EXACTLY as before — no token required, no DB users table needed.
    # When auth_enabled=True the dependencies enforce: a valid bearer token to
    # submit/list/read; members are scoped to claims.member_id == their member_id;
    # ops can read all + run/inspect evals + list members. The login/me endpoints
    # exist regardless of the flag. jwt_secret MUST be overridden in production.
    auth_enabled: bool = False
    jwt_secret: str = "dev-insecure-change-me"
    # Login brute-force throttle (enforced only when auth_enabled): at most
    # login_rate_limit_max attempts per (username, client-IP) within the window.
    login_rate_limit_max: int = 10
    login_rate_limit_window_seconds: float = 60.0
    # Per-IP throttle for the paid, Gemini-backed endpoints (classify / parse / ask),
    # guarding against cost-amplification DoS. Gated OFF by default (dev/test/eval
    # unaffected); enable in production.
    llm_rate_limit_enabled: bool = False
    llm_rate_limit_max: int = 30
    llm_rate_limit_window_seconds: float = 60.0
    # Wayfinding-only: when True (and auth on), the login page shows an Operator|Member
    # toggle that switches the username placeholder + role description. It does NOT change
    # auth (role always comes from the credentials) and never pre-fills a password. Default
    # OFF for a clean true-prod login; turn ON for a review/demo deployment so both roles
    # are discoverable. Surfaced to the frontend via /api/auth/config.
    show_role_help: bool = False

    # --- Adaptive agentic supervisor (adjudication routing) --------------------
    # When True (default), a supervisor (app/graph/supervisor.py) inspects each claim
    # and fans out ONLY to the rule agents that are APPLICABLE, skipping a rule ONLY
    # when it is PROVABLY guaranteed to PASS (pre_auth on non-DIAGNOSTIC categories;
    # waiting_period when the member is enrolled past the policy's maximum waiting).
    # An absent verdict contributes no FAIL/FLAG (identical to a PASS), so the final
    # decision/amount is byte-identical to running all five rules. Set to False to fan
    # out to all five every time (the original behaviour) for comparison.
    adaptive_routing_enabled: bool = True

    # --- PHI/privacy: transparent at-rest encryption (OFF by default) ----------
    # When phi_encryption_enabled=False (default) the claims `submission`/`result` JSONB
    # is stored as plaintext EXACTLY as today, so every existing reader, the doc viewer,
    # replay, the API tests and the 12/12 eval behave identically. When True, save_claim
    # encrypts those payloads into an {"_enc": "<fernet-token>"} envelope and the readers
    # transparently decrypt; decrypt is tolerant of mixed plaintext/ciphertext rows so the
    # flag can be flipped on a populated DB safely. The key derives from phi_encryption_key
    # (falls back to a dev key from jwt_secret with a warning if unset). PII masking in logs
    # and audit are independent of this flag (always on / opt-in respectively).
    phi_encryption_enabled: bool = False
    phi_encryption_key: str = ""
    jwt_algorithm: str = "HS256"
    # Token lifetime. Shortened from 12 h to one 8 h work shift (PHI access should not
    # carry a day-long stateless token). Override per deployment as needed.
    jwt_expire_minutes: int = 480
    # Default dev password for the seeded `ops` account (documented in README).
    ops_default_password: str = "ops-dev-password"
    # Default dev password for every seeded per-member account (EMP001, …).
    member_default_password: str = "member-dev-password"

    def model_post_init(self, __context) -> None:
        # Resolve read-only asset paths so they work from any CWD.
        object.__setattr__(self, "policy_path", _resolve_asset(self.policy_path))
        object.__setattr__(self, "test_cases_path", _resolve_asset(self.test_cases_path))
        # Celery broker/result-backend default to redis_url when not set explicitly.
        if not self.celery_broker_url:
            object.__setattr__(self, "celery_broker_url", self.redis_url)
        if not self.celery_result_backend:
            object.__setattr__(self, "celery_result_backend", self.redis_url)


settings = Settings()
