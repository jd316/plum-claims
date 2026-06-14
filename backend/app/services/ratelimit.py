"""Tiny in-process sliding-window rate limiter for the login endpoint.

Brute-force protection: cap failed/attempted logins per (username, client-IP) key within
a window. In-process and best-effort — adequate for a single backend; behind a horizontal
fleet you'd back this with Redis (same interface). The `now` parameter is injectable so the
window can be tested without sleeping.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, max_attempts: int, window_seconds: float):
        self.max = max_attempts
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        """Record an attempt for `key`; return False if it exceeds `max` within the window."""
        t = time.monotonic() if now is None else now
        with self._lock:
            dq = self._hits[key]
            cutoff = t - self.window
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self.max:
                return False
            dq.append(t)
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)
