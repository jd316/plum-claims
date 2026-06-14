"""Login brute-force throttle (app.services.ratelimit + /api/auth/login)."""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services.ratelimit import SlidingWindowLimiter


def test_sliding_window_blocks_then_recovers():
    lim = SlidingWindowLimiter(max_attempts=2, window_seconds=10.0)
    assert lim.allow("k", now=0.0)
    assert lim.allow("k", now=1.0)
    assert not lim.allow("k", now=2.0)        # third within window → blocked
    assert lim.allow("k", now=12.5)           # first two have aged out → allowed again


def test_keys_are_independent():
    lim = SlidingWindowLimiter(max_attempts=1, window_seconds=10.0)
    assert lim.allow("a", now=0.0)
    assert not lim.allow("a", now=0.1)
    assert lim.allow("b", now=0.1)            # different key, own budget


def test_login_endpoint_429_after_limit(monkeypatch):
    import app.main as m
    monkeypatch.setattr(settings, "auth_enabled", True)
    # Isolate this test's limiter and make every credential invalid (no DB needed).
    monkeypatch.setattr(m, "_login_limiter", SlidingWindowLimiter(3, 60.0))
    monkeypatch.setattr(m.auth_service, "authenticate", lambda u, p: None)
    client = TestClient(m.app)
    body = {"username": "ops", "password": "x"}
    assert client.post("/api/auth/login", json=body).status_code == 401
    assert client.post("/api/auth/login", json=body).status_code == 401
    assert client.post("/api/auth/login", json=body).status_code == 401
    assert client.post("/api/auth/login", json=body).status_code == 429  # 4th → throttled
