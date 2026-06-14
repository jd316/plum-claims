"""Idempotency-key store for claim submissions.

A network retry of a POST /api/claims (mobile flaps, proxy timeout, user double-tap)
can submit the SAME claim twice and process it — billing the work twice and creating
duplicate claim records. The fix is an idempotency key: the client sends an
`Idempotency-Key` header; the FIRST time we see it we process normally and remember
key -> claim_id; a REPEAT of that key returns the SAME stored claim's result with
`idempotent_replay: true` instead of re-processing.

ADDITIVE: a submit with NO key behaves exactly as today (always processed). The map is
keyed by an opaque client-chosen string, so distinct claims with distinct keys never
collide and the 12-case eval / live tests (which send no key) are untouched.

Backing store: Redis (settings.redis_url) when reachable so the mapping is shared and
survives restarts, else an in-process dict fallback. Best-effort — a down Redis never
crashes a submit; it just degrades idempotency to per-process. Entries carry a TTL
(retention window): a repeat within the window replays; after it, the key is forgotten
and a re-submit is processed as new."""
from __future__ import annotations

import logging
import threading
from typing import cast

from app.config import settings

log = logging.getLogger("plum.idempotency")

_REDIS_PREFIX = "plum:idempotency:"
DEFAULT_RETENTION_SECONDS = 24 * 3600  # how long a key replays the same claim


class IdempotencyStore:
    """Maps an idempotency key -> claim_id. Redis-backed when available, else
    an in-process dict. Every Redis interaction swallows errors and falls back."""

    def __init__(self, retention_seconds: int = DEFAULT_RETENTION_SECONDS):
        self._retention = retention_seconds
        self._mem: dict[str, str] = {}
        self._lock = threading.Lock()
        self._redis = None
        self._redis_tried = False

    def _redis_client(self):
        if self._redis_tried:
            return self._redis
        self._redis_tried = True
        url = getattr(settings, "redis_url", None)
        if not url:
            return None
        try:
            import redis
            client = redis.Redis.from_url(url, socket_connect_timeout=0.25, socket_timeout=0.25)
            client.ping()
            self._redis = client
            log.info("idempotency: Redis backing enabled (%s)", url)
        except Exception as e:  # noqa: BLE001 — optional; fall back to in-process map
            log.info("idempotency: Redis unavailable (%s); using in-memory map", e)
            self._redis = None
        return self._redis

    def ping(self) -> bool:
        """True if the Redis backing store is reachable (for readiness probes).
        Never raises — Redis is optional and the store degrades to in-memory."""
        try:
            return self._redis_client() is not None
        except Exception:  # noqa: BLE001
            return False

    def get(self, key: str | None) -> str | None:
        """Return the claim_id previously stored for `key`, or None if unseen
        (or key is None/empty). None key always misses → caller processes normally."""
        if not key:
            return None
        client = self._redis_client()
        if client is not None:
            try:
                val = client.get(_REDIS_PREFIX + key)
                if val is not None:
                    return cast("str | None", val.decode() if isinstance(val, (bytes, bytearray)) else val)
            except Exception as e:  # noqa: BLE001 — degrade to memory on Redis error
                log.debug("idempotency: Redis get failed: %s", e)
        with self._lock:
            return self._mem.get(key)

    def put(self, key: str | None, claim_id: str) -> None:
        """Record key -> claim_id (no-op if key is None/empty). First writer wins:
        we do NOT overwrite an existing mapping, so a racing double-submit that both
        complete still converge on the first stored claim_id on the next read."""
        if not key:
            return
        client = self._redis_client()
        if client is not None:
            try:
                # NX = only set if absent (first writer wins); EX = retention TTL.
                client.set(_REDIS_PREFIX + key, claim_id, nx=True, ex=self._retention)
            except Exception as e:  # noqa: BLE001
                log.debug("idempotency: Redis set failed: %s", e)
        with self._lock:
            self._mem.setdefault(key, claim_id)

    def clear(self) -> None:
        """Clear the in-process map (test helper). Redis is left intact."""
        with self._lock:
            self._mem.clear()


# Module-level singleton used by the API wiring.
_store = IdempotencyStore()


def get_store() -> IdempotencyStore:
    return _store
