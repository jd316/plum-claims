"""Content-addressed extraction cache.

Vision extraction (Gemini flash/pro + the self-correction loop) is by far the most
expensive step in the pipeline. Its output is a pure function of (file bytes, model):
the SAME bytes through the SAME model always yields the same ExtractionResult. So we
cache the serialized ExtractionResult under `sha256(file_bytes) + ":" + model_name`.

Backing store:
  * An in-process bounded LRU (default 256 entries) — always present.
  * Optionally a shared Redis (settings.redis_url) so cache hits survive across
    processes / workers. Redis is BEST-EFFORT: if it is unreachable or errors, we
    silently fall back to the in-memory layer and NEVER crash a claim.

Keying by content hash (not file path) means a re-upload of identical bytes — even to
a different path — is a HIT. CRITICAL for the eval: within one run every rendered
document is unique, so every key is new → every lookup misses → the extractor runs
exactly as before. Caching only changes behaviour on a genuine repeat of the same bytes.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from typing import cast

from app.config import settings
from app.models.schemas import ExtractionResult

log = logging.getLogger("plum.extraction_cache")

_REDIS_PREFIX = "plum:extraction:"
_REDIS_TTL_SECONDS = 7 * 24 * 3600  # a week; bytes->result is stable, this just bounds growth


def hash_file(file_path: str) -> str:
    """sha256 hex digest of a file's raw bytes. Stable across calls and paths:
    identical bytes anywhere on disk produce the same digest."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_key(file_path: str, model: str) -> str:
    """The content-addressed key: <sha256(bytes)>:<model_name>."""
    return f"{hash_file(file_path)}:{model}"


class _LRU:
    """A tiny thread-safe bounded LRU over OrderedDict. Stores serialized JSON
    strings (same wire form as Redis) so both layers round-trip identically."""

    def __init__(self, capacity: int):
        self._cap = max(1, capacity)
        self._d: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            if key not in self._d:
                return None
            self._d.move_to_end(key)
            return self._d[key]

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self._cap:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


class ExtractionCache:
    """In-process LRU + optional best-effort Redis. Lookups check memory first
    (fast), then Redis; a Redis hit is promoted into memory. Writes go to both."""

    def __init__(self, capacity: int = 256):
        self._mem = _LRU(capacity)
        self._redis = None
        self._redis_tried = False
        # Observability for the cache-hit proof: counts since process start.
        self.hits = 0
        self.misses = 0

    # -- Redis (lazy, best-effort) ------------------------------------------- #
    def _redis_client(self):
        """Lazily connect to Redis once. Any failure disables Redis for this
        process (memory-only) — we never retry-storm or crash on a down Redis."""
        if self._redis_tried:
            return self._redis
        self._redis_tried = True
        url = getattr(settings, "redis_url", None)
        if not url:
            return None
        try:
            import redis  # local import: redis is optional at runtime
            client = redis.Redis.from_url(url, socket_connect_timeout=0.25, socket_timeout=0.25)
            client.ping()
            self._redis = client
            log.info("extraction_cache: Redis backing enabled (%s)", url)
        except Exception as e:  # noqa: BLE001 — Redis is optional; fall back silently
            log.info("extraction_cache: Redis unavailable (%s); using in-memory only", e)
            self._redis = None
        return self._redis

    # -- Public API ---------------------------------------------------------- #
    def get(self, file_path: str, model: str) -> ExtractionResult | None:
        """Return the cached ExtractionResult for this file's bytes + model, or None."""
        try:
            key = cache_key(file_path, model)
        except OSError:
            return None  # unreadable/missing file → treat as a miss, never crash
        raw = self._mem.get(key)
        if raw is None:
            raw = self._redis_get(key)
            if raw is not None:
                self._mem.put(key, raw)  # promote into the fast layer
        if raw is None:
            self.misses += 1
            return None
        try:
            result = ExtractionResult.model_validate_json(raw)
        except Exception as e:  # noqa: BLE001 — a corrupt entry is a miss, not a crash
            log.warning("extraction_cache: discarding unparseable entry: %s", e)
            self.misses += 1
            return None
        self.hits += 1
        return result

    def put(self, file_path: str, model: str, result: ExtractionResult) -> None:
        """Store `result` under this file's content hash + model. Best-effort."""
        try:
            key = cache_key(file_path, model)
        except OSError:
            return
        raw = result.model_dump_json()
        self._mem.put(key, raw)
        self._redis_set(key, raw)

    # -- Redis helpers (swallow every error) --------------------------------- #
    def _redis_get(self, key: str) -> str | None:
        client = self._redis_client()
        if client is None:
            return None
        try:
            val = client.get(_REDIS_PREFIX + key)
            return cast("str | None", val.decode() if isinstance(val, (bytes, bytearray)) else val)
        except Exception as e:  # noqa: BLE001
            log.debug("extraction_cache: Redis get failed: %s", e)
            return None

    def _redis_set(self, key: str, raw: str) -> None:
        client = self._redis_client()
        if client is None:
            return
        try:
            client.set(_REDIS_PREFIX + key, raw, ex=_REDIS_TTL_SECONDS)
        except Exception as e:  # noqa: BLE001
            log.debug("extraction_cache: Redis set failed: %s", e)

    def clear(self) -> None:
        """Clear the in-memory layer (and reset counters). Redis is left intact."""
        self._mem.clear()
        self.hits = 0
        self.misses = 0


# Module-level singleton used by the extraction wiring.
_cache = ExtractionCache(capacity=256)


def get_cache() -> ExtractionCache:
    return _cache


def get(file_path: str, model: str) -> ExtractionResult | None:
    return _cache.get(file_path, model)


def put(file_path: str, model: str, result: ExtractionResult) -> None:
    _cache.put(file_path, model, result)
