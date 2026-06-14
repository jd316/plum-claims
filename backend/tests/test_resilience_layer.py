"""Deterministic tests for the resilience layer (app/services/resilience.py).

No live Gemini, no mocks of Gemini — these exercise OUR resilience logic directly with
plain Python callables that raise or return. The clock is injected so the circuit
breaker is tested without any real sleeps.
"""
from __future__ import annotations

import threading

import pytest

from app.services.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    LLM_SEMAPHORE,
    call_with_model_fallback,
    is_infra_error,
    with_concurrency_limit,
)


# --------------------------------------------------------------------------- #
# Injectable clock helper                                                     #
# --------------------------------------------------------------------------- #

class FakeClock:
    """Deterministic monotonic clock advanced explicitly by tests."""
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _infra() -> None:
    raise TimeoutError("request timed out")


def _ok() -> str:
    return "ok"


# --------------------------------------------------------------------------- #
# is_infra_error classification                                               #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("exc", [
    TimeoutError("connection timed out"),
    ConnectionError("connection reset by peer"),
    RuntimeError("429 rate limit exceeded"),
    RuntimeError("503 Service Unavailable"),
    RuntimeError("model is overloaded, please retry"),
    RuntimeError("RESOURCE_EXHAUSTED: quota"),
])
def test_is_infra_error_true_for_transient(exc):
    assert is_infra_error(exc) is True


def test_geminierror_after_retries_is_infra():
    from app.services.gemini import GeminiError
    assert is_infra_error(GeminiError("structured generation failed after 3 attempts")) is True


@pytest.mark.parametrize("exc", [
    ValueError("bad value"),
    TypeError("wrong type"),
    KeyError("missing"),
])
def test_is_infra_error_false_for_validation(exc):
    assert is_infra_error(exc) is False


@pytest.mark.parametrize("exc", [
    RuntimeError("patient is not in the provider network"),  # 'network' substring
    RuntimeError("line item amount 1500 is invalid"),         # '500' substring
    RuntimeError("policy quota for this benefit is unrelated"),
])
def test_is_infra_error_false_for_business_text(exc):
    # Word-boundary matching must not read business phrasing as infra signals.
    assert is_infra_error(exc) is False


def test_geminierror_naming_validation_is_not_infra():
    from app.services.gemini import GeminiError
    # A GeminiError whose message names a validation/schema problem is deterministic,
    # so it must NOT be classified infra (no pointless model fallback).
    assert is_infra_error(
        GeminiError("structured generation failed: invalid JSON / schema parse error")
    ) is False


def test_is_infra_error_false_for_pydantic_validation():
    from pydantic import BaseModel, ValidationError

    class M(BaseModel):
        x: int

    try:
        M(x="not-an-int")
    except ValidationError as e:
        assert is_infra_error(e) is False
    else:
        pytest.fail("expected a ValidationError")


# --------------------------------------------------------------------------- #
# Circuit breaker                                                            #
# --------------------------------------------------------------------------- #

def test_breaker_opens_after_threshold_then_fails_fast():
    clock = FakeClock()
    cb = CircuitBreaker(failure_threshold=3, reset_timeout=30, now=clock)

    # 3 consecutive infra failures → OPEN.
    for _ in range(3):
        with pytest.raises(TimeoutError):
            cb.call(_infra)
    assert cb.state == "OPEN"

    # Next call fails FAST (CircuitOpenError), without invoking fn.
    invoked = {"n": 0}
    def trip():
        invoked["n"] += 1
        return "should-not-run"
    with pytest.raises(CircuitOpenError):
        cb.call(trip)
    assert invoked["n"] == 0


def test_breaker_half_open_success_closes():
    clock = FakeClock()
    cb = CircuitBreaker(failure_threshold=2, reset_timeout=30, now=clock)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            cb.call(_infra)
    assert cb.state == "OPEN"

    # Before timeout: still fails fast.
    clock.advance(29)
    with pytest.raises(CircuitOpenError):
        cb.call(_ok)

    # After timeout: HALF_OPEN — one trial allowed; a success closes it.
    clock.advance(2)  # total 31 >= 30
    assert cb.state == "HALF_OPEN"
    assert cb.call(_ok) == "ok"
    assert cb.state == "CLOSED"


def test_breaker_half_open_failure_reopens():
    clock = FakeClock()
    cb = CircuitBreaker(failure_threshold=2, reset_timeout=10, now=clock)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            cb.call(_infra)
    clock.advance(11)
    assert cb.state == "HALF_OPEN"
    # The single trial fails → straight back to OPEN.
    with pytest.raises(TimeoutError):
        cb.call(_infra)
    assert cb.state == "OPEN"


def test_half_open_admits_single_trial_only():
    """When the reset window elapses, only ONE concurrent trial is admitted; other
    concurrent callers fast-fail until the trial records a result."""
    clock = FakeClock()
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=10, now=clock)
    with pytest.raises(TimeoutError):
        cb.call(_infra)
    assert cb.state == "OPEN"
    clock.advance(11)  # window elapsed → next admission is a HALF_OPEN trial

    started = threading.Event()
    finish = threading.Event()
    trial_ran = {"n": 0}

    def slow_trial():
        trial_ran["n"] += 1
        started.set()
        finish.wait(timeout=5)
        return "ok"

    t = threading.Thread(target=lambda: cb.call(slow_trial))
    t.start()
    assert started.wait(timeout=5), "trial call never started"

    # A second concurrent caller must be rejected while the trial is in flight.
    with pytest.raises(CircuitOpenError):
        cb.call(_ok)

    finish.set()
    t.join(timeout=5)
    assert trial_ran["n"] == 1
    assert cb.state == "CLOSED"  # the single trial succeeded → closed


def test_state_property_has_no_side_effect():
    """Reading `state` reports HALF_OPEN after the window but must NOT consume the
    single trial — a subsequent call() can still be admitted."""
    clock = FakeClock()
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=10, now=clock)
    with pytest.raises(TimeoutError):
        cb.call(_infra)
    clock.advance(11)
    # Poll the property repeatedly (as tracing might) — it should stay HALF_OPEN.
    assert cb.state == "HALF_OPEN"
    assert cb.state == "HALF_OPEN"
    # The trial slot is still available: a real call goes through and closes it.
    assert cb.call(_ok) == "ok"
    assert cb.state == "CLOSED"


def test_business_error_does_not_open_breaker():
    clock = FakeClock()
    cb = CircuitBreaker(failure_threshold=3, reset_timeout=30, now=clock)

    def validation_fail():
        raise ValueError("deterministic validation error")

    # Far more than threshold validation errors — breaker must stay CLOSED.
    for _ in range(10):
        with pytest.raises(ValueError):
            cb.call(validation_fail)
    assert cb.state == "CLOSED"
    # And a normal call still goes through.
    assert cb.call(_ok) == "ok"


def test_success_resets_failure_counter():
    clock = FakeClock()
    cb = CircuitBreaker(failure_threshold=3, reset_timeout=30, now=clock)
    # 2 infra failures (below threshold) then a success resets the counter.
    for _ in range(2):
        with pytest.raises(TimeoutError):
            cb.call(_infra)
    assert cb.call(_ok) == "ok"
    # 2 more failures should NOT open it (counter was reset).
    for _ in range(2):
        with pytest.raises(TimeoutError):
            cb.call(_infra)
    assert cb.state == "CLOSED"


# --------------------------------------------------------------------------- #
# call_with_model_fallback                                                    #
# --------------------------------------------------------------------------- #

def test_fallback_invoked_on_infra_failure():
    calls = []
    def fn(model):
        calls.append(model)
        if model == "flash":
            raise TimeoutError("flash timed out")
        return f"result-from-{model}"

    out = call_with_model_fallback(fn, ["flash", "pro"])
    assert out == "result-from-pro"
    assert calls == ["flash", "pro"]


def test_fallback_not_called_when_primary_succeeds():
    calls = []
    def fn(model):
        calls.append(model)
        return f"result-from-{model}"

    out = call_with_model_fallback(fn, ["flash", "pro"])
    assert out == "result-from-flash"
    assert calls == ["flash"]  # pro never tried


def test_fallback_all_fail_raises_last():
    def fn(model):
        raise TimeoutError(f"{model} timed out")
    with pytest.raises(TimeoutError):
        call_with_model_fallback(fn, ["flash", "pro"])


def test_fallback_not_triggered_by_validation_error():
    calls = []
    def fn(model):
        calls.append(model)
        raise ValueError("validation error")
    with pytest.raises(ValueError):
        call_with_model_fallback(fn, ["flash", "pro"])
    assert calls == ["flash"]  # validation error → no escalation


# --------------------------------------------------------------------------- #
# Global concurrency semaphore                                                #
# --------------------------------------------------------------------------- #

def test_semaphore_caps_concurrency():
    """At most N concurrent calls hold the global LLM semaphore at once.

    Uses a real (small) bounded semaphore and threads with a barrier to make the
    assertion deterministic: more workers than the cap, each records the peak
    concurrency observed while inside `with_concurrency_limit`.
    """
    cap = LLM_SEMAPHORE._initial_value  # the configured max_concurrent_llm
    n_workers = cap + 4

    lock = threading.Lock()
    current = {"n": 0}
    peak = {"n": 0}
    release = threading.Event()

    def worker():
        def body():
            with lock:
                current["n"] += 1
                peak["n"] = max(peak["n"], current["n"])
            # Hold the slot until the test lets everyone go, so contention is real.
            release.wait(timeout=5)
            with lock:
                current["n"] -= 1
            return None
        with_concurrency_limit(body)

    threads = [threading.Thread(target=worker) for _ in range(n_workers)]
    for t in threads:
        t.start()

    # Give the first `cap` workers time to acquire and record the peak, then release.
    import time as _t
    deadline = _t.monotonic() + 2.0
    while _t.monotonic() < deadline:
        with lock:
            if current["n"] >= cap:
                break
        _t.sleep(0.005)
    release.set()
    for t in threads:
        t.join(timeout=5)

    assert peak["n"] <= cap, f"peak concurrency {peak['n']} exceeded cap {cap}"
    assert peak["n"] == cap, f"expected to saturate the cap ({cap}), peaked at {peak['n']}"


def test_semaphore_releases_on_exception():
    """An exception inside the guarded body must still release the slot."""
    def boom():
        raise RuntimeError("kaboom")
    for _ in range(LLM_SEMAPHORE._initial_value + 3):
        with pytest.raises(RuntimeError):
            with_concurrency_limit(boom)
    # If slots leaked, this acquire would block; assert it does not.
    acquired = LLM_SEMAPHORE.acquire(timeout=1)
    assert acquired, "semaphore slot leaked after an exception"
    LLM_SEMAPHORE.release()
