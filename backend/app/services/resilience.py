"""Resilience layer for LLM calls — pure, deterministic, no new external service.

Three independent primitives that engage ONLY on failure / overload, so clean runs
(the 12 cases) behave identically:

1. CircuitBreaker  — fail fast after repeated INFRA failures, auto-recover after a
   reset timeout. Time source is injectable so tests are deterministic without sleeps.
2. A module-level bounded semaphore (`LLM_SEMAPHORE`) capping concurrent Gemini calls.
3. call_with_model_fallback — try a primary model, escalate to fallbacks on infra error.

INFRA vs business: only transient infrastructure failures (timeout / 429 / 503 /
connection / GeminiError-after-retries) count toward the breaker and trigger fallback.
A deterministic validation error (ValueError / pydantic ValidationError) must NOT trip
the breaker nor cause a model fallback — it would just fail again.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable, TypeVar

from app.config import settings

_T = TypeVar("_T")


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit breaker is OPEN."""


# --------------------------------------------------------------------------- #
# Infra-error classification                                                   #
# --------------------------------------------------------------------------- #

# Transient signals indicating an infrastructure / overload problem (vs a deterministic
# validation error that would fail identically on retry). Matched on word boundaries so
# short fragments don't spuriously hit business-error text (e.g. an amount "1500" or a
# phrase like "provider network" must NOT be read as a 500 / network infra failure).
_INFRA_SIGNALS = (
    "timeout", "timed out", "deadline exceeded",
    "429", "rate limit", "resource exhausted", "resource_exhausted",
    "503", "502", "504", "service unavailable", "unavailable",
    "internal server error",
    "connection reset", "connection refused", "connection aborted",
    "broken pipe", "econnreset", "econnrefused",
    "overloaded",
)
_INFRA_RE = re.compile(
    "|".join(r"\b" + re.escape(s) + r"\b" for s in _INFRA_SIGNALS), re.IGNORECASE
)

# Signals that a GeminiError (raised only after our client's retries are exhausted) wraps
# a deterministic VALIDATION problem rather than a transient one — so we don't fall back.
_VALIDATION_RE = re.compile(r"validation|invalid json|schema|parse|pydantic", re.IGNORECASE)


def is_infra_error(exc: BaseException) -> bool:
    """True if `exc` looks like a transient infrastructure / overload failure.

    Heuristic by design, matched on word boundaries to avoid misclassifying business
    errors. Deterministic validation errors (ValueError, TypeError, KeyError, pydantic
    ValidationError) are treated as NOT infra so they never open the breaker or cause a
    pointless model fallback.
    """
    # Explicit non-infra: plain ValueError / TypeError / KeyError and pydantic validation.
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return False
    type_name = type(exc).__name__
    if "ValidationError" in type_name:
        return False

    message = str(exc)

    # GeminiError is raised by our client only after its internal retries are exhausted.
    # Having survived backoff/retry it is infra-transient UNLESS its message clearly names
    # a validation problem (e.g. "invalid JSON" / schema parse failure).
    if "geminierror" in type_name.lower():
        return not bool(_VALIDATION_RE.search(message))

    return bool(_INFRA_RE.search(f"{type_name} {message}"))


# --------------------------------------------------------------------------- #
# Circuit breaker                                                             #
# --------------------------------------------------------------------------- #

_CLOSED = "CLOSED"
_OPEN = "OPEN"
_HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Thread-safe circuit breaker (CLOSED -> OPEN -> HALF_OPEN).

    - Opens after `failure_threshold` consecutive INFRA failures; while OPEN, calls
      fail fast with CircuitOpenError for `reset_timeout` seconds.
    - After the timeout the next call is allowed through as a HALF_OPEN trial: success
      closes the breaker, failure re-opens it.
    - Only infra failures (per `is_infra_error`) count; a business/validation error is
      re-raised untouched and does NOT advance the failure counter.

    The clock is injectable (`now` callable) so tests are deterministic without real
    sleeps; production uses `time.monotonic`.
    """

    def __init__(
        self,
        failure_threshold: int | None = None,
        reset_timeout: float | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = (
            failure_threshold if failure_threshold is not None
            else settings.circuit_breaker_threshold
        )
        self.reset_timeout = (
            reset_timeout if reset_timeout is not None
            else settings.circuit_breaker_reset_seconds
        )
        self._now = now
        self._lock = threading.Lock()
        self._state = _CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        # True while a single HALF_OPEN trial call is in flight; blocks any further
        # admissions until that trial records success (→CLOSED) or failure (→OPEN).
        self._trial_in_flight = False

    # -- introspection (mostly for tests / tracing) -------------------------- #
    @property
    def state(self) -> str:
        """Current state, resolving an elapsed OPEN window to HALF_OPEN.

        Side-effect-free: it reports HALF_OPEN once the reset window has elapsed but
        does NOT mutate the machine (only an actual admission in `call()` transitions
        state), so polling this for tracing never disturbs control flow."""
        with self._lock:
            if (self._state == _OPEN and self._opened_at is not None
                    and self._now() - self._opened_at >= self.reset_timeout):
                return _HALF_OPEN
            return self._state

    # -- gate + result recording (all under self._lock) --------------------- #
    def _try_admit_locked(self) -> bool:
        """Decide whether to admit a call right now, mutating state as needed.

        Returns True and (when crossing into a trial) marks the trial in flight.
        Returns False if the breaker is OPEN or a HALF_OPEN trial is already running."""
        if self._state == _CLOSED:
            return True
        if self._state == _OPEN:
            if (self._opened_at is not None
                    and self._now() - self._opened_at >= self.reset_timeout):
                # Window elapsed → admit exactly ONE trial.
                self._state = _HALF_OPEN
                self._trial_in_flight = True
                return True
            return False
        # _HALF_OPEN: admit only if no trial is already in flight.
        if not self._trial_in_flight:
            self._trial_in_flight = True
            return True
        return False

    def _on_success_locked(self) -> None:
        self._consecutive_failures = 0
        self._state = _CLOSED
        self._opened_at = None
        self._trial_in_flight = False

    def _on_infra_failure_locked(self) -> None:
        self._consecutive_failures += 1
        if (self._state == _HALF_OPEN
                or self._consecutive_failures >= self.failure_threshold):
            self._state = _OPEN
            self._opened_at = self._now()
        self._trial_in_flight = False

    def call(self, fn: Callable[[], _T]) -> _T:
        """Run `fn` through the breaker.

        Raises CircuitOpenError immediately if OPEN (or if a HALF_OPEN trial is already
        in flight). On success, closes/resets. On an INFRA failure, advances the counter
        (and opens at threshold), then re-raises. A non-infra (business/validation) error
        is re-raised WITHOUT counting; if it interrupts a HALF_OPEN trial the trial slot
        is released so the breaker can probe again.
        """
        with self._lock:
            if not self._try_admit_locked():
                raise CircuitOpenError(
                    f"circuit OPEN — failing fast for {self.reset_timeout}s "
                    f"after {self._consecutive_failures} consecutive infra failures"
                )

        try:
            result = fn()
        except BaseException as exc:
            with self._lock:
                if is_infra_error(exc):
                    self._on_infra_failure_locked()
                else:
                    # Non-infra error: counter untouched, but free a HALF_OPEN trial
                    # slot so a deterministic error doesn't wedge the breaker open.
                    self._trial_in_flight = False
            raise

        with self._lock:
            self._on_success_locked()
        return result


# --------------------------------------------------------------------------- #
# Global concurrency semaphore                                                #
# --------------------------------------------------------------------------- #

# Bounded so we never exceed N concurrent real Gemini calls (protects the rate limit).
# Transparent at low concurrency: acquiring is non-blocking when < N are in flight.
LLM_SEMAPHORE = threading.BoundedSemaphore(settings.max_concurrent_llm)

# Shared breaker guarding all real Gemini calls.
GEMINI_BREAKER = CircuitBreaker()


def with_concurrency_limit(fn: Callable[[], _T]) -> _T:
    """Run `fn` while holding the global LLM semaphore (cap = max_concurrent_llm)."""
    LLM_SEMAPHORE.acquire()
    try:
        return fn()
    finally:
        LLM_SEMAPHORE.release()


def guarded_call(fn: Callable[[], _T]) -> _T:
    """Run a real Gemini call under both the semaphore and the shared breaker.

    Order: breaker outermost (so a fast-fail does NOT consume a semaphore slot),
    semaphore innermost (caps actual in-flight calls). On success, behaviour is
    identical to calling `fn()` directly.
    """
    if not settings.resilience_enabled:
        return fn()
    return GEMINI_BREAKER.call(lambda: with_concurrency_limit(fn))


# --------------------------------------------------------------------------- #
# Layered model fallback                                                      #
# --------------------------------------------------------------------------- #

def call_with_model_fallback(fn: Callable[..., _T], models: list[str]) -> _T:
    """Try `fn(model=models[0])`; on an INFRA failure, escalate to the next model.

    Returns the first success. Re-raises the last error if every model fails. A
    non-infra (validation/business) error is raised immediately without trying the
    next model — it would just fail the same way. When resilience_enabled is False the
    fallback is bypassed entirely (only the primary model is attempted).
    """
    if not models:
        raise ValueError("call_with_model_fallback requires at least one model")
    if not settings.resilience_enabled:
        return fn(model=models[0])
    last: BaseException | None = None
    for model in models:
        try:
            return fn(model=model)
        except BaseException as exc:
            last = exc
            if not is_infra_error(exc):
                raise
            # infra failure → try the next model (if any)
    assert last is not None
    raise last
