"""JWT revocation list (logout / token compromise).

A stateless JWT cannot otherwise be invalidated before its `exp`. We store the revoked
token's `jti` with a TTL equal to its REMAINING lifetime, so the set self-prunes (a
revoked token is forgotten the moment it would have expired anyway). Redis-backed when
reachable (shared across instances); falls back to a per-process map. Only consulted
when auth is enabled.
"""
from __future__ import annotations

import logging
import time

from app.config import settings

log = logging.getLogger("plum.auth")


class RevocationStore:
    def __init__(self) -> None:
        self._redis = None
        self._tried = False
        self._mem: dict[str, float] = {}

    def _client(self):
        if self._tried:
            return self._redis
        self._tried = True
        url = getattr(settings, "redis_url", None)
        if not url:
            return None
        try:
            import redis
            c = redis.Redis.from_url(url, socket_connect_timeout=0.25, socket_timeout=0.25)
            c.ping()
            self._redis = c
        except Exception as e:  # noqa: BLE001 — optional; in-memory fallback
            log.info("revocation: Redis unavailable (%s); using in-memory set", e)
            self._redis = None
        return self._redis

    def revoke(self, jti: str | None, ttl_seconds: float) -> None:
        if not jti:
            return
        ttl = max(1, int(ttl_seconds))
        c = self._client()
        if c is not None:
            try:
                c.setex(f"revoked:{jti}", ttl, "1")
                return
            except Exception:  # noqa: BLE001 — degrade to in-memory
                pass
        self._mem[jti] = time.monotonic() + ttl

    def is_revoked(self, jti: str | None) -> bool:
        if not jti:
            return False
        c = self._client()
        if c is not None:
            try:
                return c.get(f"revoked:{jti}") is not None
            except Exception:  # noqa: BLE001 — degrade to in-memory
                pass
        exp = self._mem.get(jti)
        if exp is None:
            return False
        if exp < time.monotonic():
            self._mem.pop(jti, None)
            return False
        return True


_store: RevocationStore | None = None


def get_revocation_store() -> RevocationStore:
    global _store
    if _store is None:
        _store = RevocationStore()
    return _store
