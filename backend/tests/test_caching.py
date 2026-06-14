"""Deterministic tests for the additive caching + idempotency layer (no Gemini).

Covers all three caches:
  * Policy cache       — get_policy_engine() singleton + invalidation.
  * Extraction cache   — content-addressed get/put, hash stability, path-independence,
                         and that extract_document_cached serves a pre-seeded hit WITHOUT
                         ever calling the real extractor.
  * Idempotency store  — unseen→None, seen→claim_id, two submits one key→one claim_id.
"""
import os

from PIL import Image

from app.config import settings
from app.models.schemas import (ExtractionResult, DocumentInput, DocumentQuality,
                                 StrField, NumField, LineItem)
from app.services.policy_engine import (PolicyEngine, get_policy_engine,
                                        invalidate_policy_cache)
from app.services import extraction_cache
from app.services.extraction_cache import hash_file, cache_key, ExtractionCache
from app.services.idempotency import IdempotencyStore
from tests.conftest import REPO_ROOT

POLICY_PATH = str(REPO_ROOT / "policy_terms.json")


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #

def _write_png(path, color=(200, 30, 30), unique=True):
    """Write a real tiny PNG to `path` and return its bytes. By default a few random
    pixels are stamped in so the bytes (and thus the content hash) are unique per run —
    this keeps cold-miss / counter assertions independent of any shared Redis that may
    persist entries across runs. Pass unique=False for a deterministic image."""
    img = Image.new("RGB", (8, 8), color)
    if unique:
        px = img.load()
        for i in range(8):
            px[i, 0] = tuple(os.urandom(3))
    img.save(str(path), format="PNG")
    return path.read_bytes()


def _sample_result(file_id="F001", patient="Asha Rao", total=1350.0):
    return ExtractionResult(
        file_id=file_id, doc_type="HOSPITAL_BILL",
        quality=DocumentQuality(readable=True, overall_confidence=0.95),
        patient_name=StrField(value=patient, confidence=0.97, source_text=patient),
        total_amount=NumField(value=total, confidence=0.96, source_text=str(total)),
        line_items=[LineItem(description="Consultation", amount=total)],
    )


# --------------------------------------------------------------------------- #
# Policy cache                                                                 #
# --------------------------------------------------------------------------- #

def test_policy_cache_returns_cached_instance():
    invalidate_policy_cache()
    a = get_policy_engine(POLICY_PATH)
    b = get_policy_engine(POLICY_PATH)
    assert a is b  # same object, only one file read


def test_policy_cache_values_match_fresh_read():
    fresh = PolicyEngine(POLICY_PATH)
    cached = get_policy_engine(POLICY_PATH)
    assert cached.policy_id == fresh.policy_id
    assert cached.per_claim_limit() == fresh.per_claim_limit()
    assert cached.member("EMP005")["name"] == fresh.member("EMP005")["name"]


def test_policy_cache_invalidation_forces_reread():
    first = get_policy_engine(POLICY_PATH)
    invalidate_policy_cache(POLICY_PATH)
    second = get_policy_engine(POLICY_PATH)
    assert first is not second              # a fresh instance after invalidation
    assert second.policy_id == first.policy_id  # but identical values


# --------------------------------------------------------------------------- #
# Extraction cache — hashing                                                   #
# --------------------------------------------------------------------------- #

def test_hash_stable_same_bytes_same_key(tmp_path):
    p = tmp_path / "bill.png"
    _write_png(p)
    h1 = hash_file(str(p))
    h2 = hash_file(str(p))
    assert h1 == h2  # stable across calls
    assert cache_key(str(p), "m") == cache_key(str(p), "m")


def test_hash_same_bytes_different_path(tmp_path):
    """Identical bytes at two different paths hash identically (content-addressed)."""
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "sub" / "b.png"
    p2.parent.mkdir()
    data = _write_png(p1)
    p2.write_bytes(data)
    assert hash_file(str(p1)) == hash_file(str(p2))


def test_hash_different_bytes_different_key(tmp_path):
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    _write_png(p1, color=(10, 10, 10))
    _write_png(p2, color=(250, 250, 250))
    assert hash_file(str(p1)) != hash_file(str(p2))


# --------------------------------------------------------------------------- #
# Extraction cache — get/put round-trip                                        #
# --------------------------------------------------------------------------- #

def test_extraction_cache_put_get_equal(tmp_path):
    cache = ExtractionCache(capacity=8)
    p = tmp_path / "bill.png"
    _write_png(p)
    res = _sample_result()
    assert cache.get(str(p), "flash") is None  # cold miss
    cache.put(str(p), "flash", res)
    got = cache.get(str(p), "flash")
    assert got is not None
    assert got == res  # pydantic structural equality


def test_extraction_cache_miss_on_different_bytes(tmp_path):
    cache = ExtractionCache(capacity=8)
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    _write_png(p1, color=(0, 0, 0))
    _write_png(p2, color=(255, 255, 255))
    cache.put(str(p1), "flash", _sample_result())
    assert cache.get(str(p2), "flash") is None  # different bytes → miss


def test_extraction_cache_hit_same_bytes_different_path(tmp_path):
    """Path-independent: seed under one path, hit under a different path with the
    same bytes (the real-world re-upload scenario)."""
    cache = ExtractionCache(capacity=8)
    p1 = tmp_path / "orig.png"
    p2 = tmp_path / "reupload.png"
    data = _write_png(p1)
    p2.write_bytes(data)
    cache.put(str(p1), "flash", _sample_result())
    got = cache.get(str(p2), "flash")
    assert got is not None and got == _sample_result()


def test_extraction_cache_miss_on_different_model(tmp_path):
    cache = ExtractionCache(capacity=8)
    p = tmp_path / "bill.png"
    _write_png(p)
    cache.put(str(p), "flash", _sample_result())
    assert cache.get(str(p), "pro") is None  # model is part of the key


def test_extraction_cache_counters(tmp_path):
    cache = ExtractionCache(capacity=8)
    p = tmp_path / "bill.png"
    _write_png(p, unique=True)  # unique bytes → independent of any shared Redis state
    cache.get(str(p), "flash")  # miss
    cache.put(str(p), "flash", _sample_result())
    cache.get(str(p), "flash")  # hit
    assert cache.hits == 1 and cache.misses == 1


# --------------------------------------------------------------------------- #
# extract_document_cached — pre-seeded hit never calls the real extractor      #
# --------------------------------------------------------------------------- #

def test_extract_document_cached_hit_skips_extractor(tmp_path):
    """Pre-seed the module-level cache for a file's hash, then call
    extract_document_cached: it must return the seeded result and NEVER reach Gemini.
    We prove "never reached" structurally — the seeded result is a fixed object that
    a real extraction could not produce — and via the cache hit counter."""
    from app.agents.extraction import extract_document_cached

    p = tmp_path / "tc004_bill.png"
    _write_png(p)
    doc = DocumentInput(file_id="F042", file_name="bill.png", stored_path=str(p))

    seeded = _sample_result(file_id="SEED", patient="Cached Patient", total=4242.0)
    model = settings.gemini_model
    extraction_cache.get_cache().clear()
    extraction_cache.put(str(p), model, seeded)
    hits_before = extraction_cache.get_cache().hits

    result, info = extract_document_cached(doc)

    assert info["cache_hit"] is True
    assert result.patient_name.value == "Cached Patient"
    assert result.total_amount.value == 4242.0
    assert result.file_id == "F042"  # the caller's file_id is restored on a hit
    assert extraction_cache.get_cache().hits == hits_before + 1


# --------------------------------------------------------------------------- #
# Idempotency store                                                            #
# --------------------------------------------------------------------------- #

def test_idempotency_unseen_key_returns_none():
    store = IdempotencyStore()
    store.clear()
    assert store.get("never-seen") is None
    assert store.get(None) is None  # no key → always a miss


def test_idempotency_seen_key_returns_claim_id():
    store = IdempotencyStore()
    store.clear()
    store.put("key-1", "CLM-abc")
    assert store.get("key-1") == "CLM-abc"


def test_idempotency_two_submits_one_key_one_claim():
    """A double-submit under one key converges on a single claim_id (first writer
    wins — the second put does not overwrite)."""
    store = IdempotencyStore()
    store.clear()
    store.put("retry-key", "CLM-first")
    store.put("retry-key", "CLM-second")  # network retry assigns a new id, but...
    assert store.get("retry-key") == "CLM-first"  # ...the stored mapping is stable


def test_idempotency_none_key_put_is_noop():
    store = IdempotencyStore()
    store.clear()
    store.put(None, "CLM-x")
    assert store.get(None) is None
