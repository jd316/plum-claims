"""Production-hardening tests: PHI endpoint authz, readiness probe, and the
secure-by-default boot check. Pure/fast where possible; DB-touching assertions
tolerate Postgres being down."""
import pytest
from fastapi.testclient import TestClient

import tests.conftest  # noqa: F401 — inserts backend/ on sys.path
from app.config import settings


def test_jobs_endpoint_requires_auth_when_on(monkeypatch):
    # /api/jobs/{id} returns a completed claim's ClaimResult (PHI). With auth ON,
    # an unauthenticated poll must be rejected by the require_user dependency
    # BEFORE the handler runs (so no broker call is made).
    monkeypatch.setattr(settings, "auth_enabled", True)
    from app.main import app
    client = TestClient(app)
    r = client.get("/api/jobs/some-job-id")  # no bearer token
    assert r.status_code == 401


def test_jobs_endpoint_open_when_auth_off():
    # OFF (default) preserves the original behaviour: the dependency is a no-op,
    # so the request reaches the handler (which reports queued/failed cleanly).
    from app.main import app
    client = TestClient(app)
    r = client.get("/api/jobs/some-job-id")
    assert r.status_code in (200, 503)  # 200 normally; 503 only if the broker is down


def test_readiness_probe_reports_checks():
    from app.main import app
    client = TestClient(app)
    r = client.get("/api/ready")
    assert r.status_code in (200, 503)
    body = r.json()
    assert "checks" in body and "db" in body["checks"] and "redis" in body["checks"]


def test_health_is_liveness_only():
    from app.main import app
    r = TestClient(app).get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


# --- secure-by-default boot check (production) ------------------------------- #
def _strong_auth(monkeypatch):
    """Satisfy the auth-on secret/password requirements so the ONLY remaining
    problem under test is the one we're asserting."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "x" * 48)
    monkeypatch.setattr(settings, "ops_default_password", "strong-ops-pw-123456")
    monkeypatch.setattr(settings, "member_default_password", "strong-member-pw-123456")


def test_prod_refuses_auth_disabled(monkeypatch):
    from app.main import _check_insecure_defaults
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_enabled", False)
    with pytest.raises(RuntimeError, match="AUTH_ENABLED"):
        _check_insecure_defaults()


def test_prod_refuses_phi_encryption_disabled(monkeypatch):
    from app.main import _check_insecure_defaults
    monkeypatch.setattr(settings, "app_env", "production")
    _strong_auth(monkeypatch)
    monkeypatch.setattr(settings, "phi_encryption_enabled", False)
    with pytest.raises(RuntimeError, match="PHI_ENCRYPTION_ENABLED"):
        _check_insecure_defaults()


def test_dev_only_warns_never_raises(monkeypatch):
    # development is the default test env: insecure config warns, never crashes.
    from app.main import _check_insecure_defaults
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "phi_encryption_enabled", False)
    _check_insecure_defaults()  # must not raise
