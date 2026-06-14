"""Object-storage abstraction with a local-disk default and an optional
self-hosted MinIO (S3-compatible) backend.

Design goals:
  * `object_store="local"` (the default) is BYTE-IDENTICAL to the original behaviour:
    files live on disk under settings.storage_dir, and `get_path` returns the real
    on-disk path the doc-serving endpoint streams via FileResponse. Nothing about the
    local path layout changes, so every existing reader keeps working unchanged.
  * `object_store="minio"` routes puts/reads through a self-hosted MinIO. MinIO is an
    OSS container (like Postgres/Redis), enabled purely by config.
  * Best-effort: if MinIO is unconfigured or unreachable, the store FALLS BACK to local
    and logs — selecting minio can never crash the app.

A "key" is a storage-relative path like "uploads/CLM-abc/F001.png". In local mode the
key maps to settings.storage_dir/<key>; in minio mode it is the object name within the
bucket. Callers keep storing the LOCAL absolute path as `stored_path` (unchanged), and
in minio mode the bytes are ALSO mirrored to the bucket so the object exists remotely.
"""
from __future__ import annotations

import logging
import os
import shutil
from abc import ABC, abstractmethod

from app.config import settings

log = logging.getLogger("plum.object_store")


def storage_key(*parts: str) -> str:
    """Build a forward-slash storage key from path parts (e.g. ('uploads', cid, fid))."""
    return "/".join(p.strip("/") for p in parts if p)


class ObjectStore(ABC):
    backend: str

    @abstractmethod
    def put(self, key: str, src_path: str) -> str:
        """Store the file at src_path under key. Returns the local absolute path that
        callers persist as stored_path (kept stable across backends)."""

    @abstractmethod
    def get_path(self, key: str) -> str:
        """Return a path/URL for the object: a local filesystem path in local mode, or
        a presigned URL in minio mode (falling back to the local path if presign fails)."""

    @abstractmethod
    def open(self, key: str) -> bytes:
        """Return the object's bytes."""

    def local_path(self, key: str) -> str:
        """The canonical on-disk path for a key (used as stored_path everywhere)."""
        return os.path.join(settings.storage_dir, *key.split("/"))


class LocalObjectStore(ObjectStore):
    """Files on disk under settings.storage_dir — the original behaviour, unchanged."""
    backend = "local"

    def put(self, key: str, src_path: str) -> str:
        dst = self.local_path(key)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        # No-op when the upload was already streamed to exactly this path (the common
        # case in _ingest_claim) — otherwise copy it into place.
        if os.path.abspath(src_path) != os.path.abspath(dst):
            shutil.copy2(src_path, dst)
        return dst

    def get_path(self, key: str) -> str:
        return self.local_path(key)

    def open(self, key: str) -> bytes:
        with open(self.local_path(key), "rb") as f:
            return f.read()


class MinioObjectStore(ObjectStore):
    """Self-hosted MinIO (S3-compatible). Mirrors bytes to the bucket while keeping a
    local copy so stored_path/FileResponse serving stays identical. Any MinIO error is
    swallowed and degrades to the local copy — never crashes a request."""
    backend = "minio"

    def __init__(self) -> None:
        self._local = LocalObjectStore()
        self._client = None
        try:
            from minio import Minio
            self._client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
            self._ensure_bucket()
        except Exception as e:  # noqa: BLE001 — unreachable/unconfigured → local fallback
            log.warning("MinIO init failed; falling back to local object store: %s", e)
            self._client = None

    def _ensure_bucket(self) -> None:
        if self._client and not self._client.bucket_exists(settings.minio_bucket):
            self._client.make_bucket(settings.minio_bucket)

    def put(self, key: str, src_path: str) -> str:
        # Always keep the local copy first (stored_path stability + serving fallback).
        local = self._local.put(key, src_path)
        if self._client:
            try:
                self._client.fput_object(settings.minio_bucket, key, local)
            except Exception as e:  # noqa: BLE001
                log.warning("MinIO put failed for %s; local copy retained: %s", key, e)
        return local

    def get_path(self, key: str) -> str:
        if self._client:
            try:
                from datetime import timedelta
                return self._client.presigned_get_object(
                    settings.minio_bucket, key, expires=timedelta(hours=1))
            except Exception as e:  # noqa: BLE001
                log.warning("MinIO presign failed for %s; using local path: %s", key, e)
        return self._local.get_path(key)

    def open(self, key: str) -> bytes:
        if self._client:
            try:
                resp = self._client.get_object(settings.minio_bucket, key)
                try:
                    return resp.read()
                finally:
                    resp.close(); resp.release_conn()
            except Exception as e:  # noqa: BLE001
                log.warning("MinIO get failed for %s; reading local copy: %s", key, e)
        return self._local.open(key)


_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    """Return the configured object store (cached). Defaults to local; selecting an
    unconfigured/unreachable minio backend degrades to local without crashing."""
    global _store
    if _store is not None:
        return _store
    if settings.object_store == "minio":
        store: ObjectStore = MinioObjectStore()
        # If the MinIO client could not be constructed, behave as local outright so
        # get_path returns a local path (not a presigned URL) — fully unchanged serving.
        if getattr(store, "_client", None) is None:
            log.info("object_store=minio requested but MinIO unavailable; using local backend")
            store = LocalObjectStore()
    else:
        store = LocalObjectStore()
    _store = store
    return _store


def reset_object_store_cache() -> None:
    """Test hook: drop the cached store so a changed settings.object_store re-resolves."""
    global _store
    _store = None
