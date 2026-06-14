"""Live cache-hit proof (1 Gemini call total).

Renders a real TC004 bill, then calls extract_document_cached TWICE on the SAME file:
  * Call 1 is a MISS → runs the real extractor (one Gemini round-trip), stores it.
  * Call 2 is a HIT → returns an EQUAL result with NO Gemini call.

We prove "no second Gemini call" three independent ways:
  1. info["cache_hit"] is True on the second call.
  2. The cache's hit counter increments by exactly one.
  3. The second call is dramatically faster than the first (no network round-trip).
"""
import time

import pytest

from app.config import settings
from app.fixtures.loader import load_cases
from app.fixtures.renderer import render_case_documents
from app.agents.extraction import extract_document_cached
from app.services import extraction_cache
from app.models.schemas import DocumentInput
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.live
CASES = {c["case_id"]: c for c in load_cases(str(REPO_ROOT / "test_cases.json"))}


def test_extraction_cache_hit_proof(tmp_path):
    case = CASES["TC004"]
    paths = render_case_documents(case, str(tmp_path / "TC004"))
    # Pick the bill document (HOSPITAL_BILL) — any rendered file works for the proof.
    file_id, stored_path = next(iter(paths.items()))
    doc = DocumentInput(file_id=file_id, file_name=f"{file_id}.png", stored_path=stored_path)

    cache = extraction_cache.get_cache()
    cache.clear()  # ensure a cold miss for this run (in-memory layer)
    # Also evict any stale Redis entry for this exact bytes+model so call 1 is a real miss.
    extraction_cache.put  # noqa: B018 — referenced for clarity; we clear via key below
    key = extraction_cache.cache_key(stored_path, settings.gemini_model)
    client = cache._redis_client()
    if client is not None:
        try:
            client.delete("plum:extraction:" + key)
        except Exception:
            pass

    hits0, misses0 = cache.hits, cache.misses

    t0 = time.monotonic()
    first, info1 = extract_document_cached(doc)
    t_first = time.monotonic() - t0

    t1 = time.monotonic()
    second, info2 = extract_document_cached(doc)
    t_second = time.monotonic() - t1

    # 1. Flags: first is a miss, second is a hit.
    assert info1["cache_hit"] is False
    assert info2["cache_hit"] is True

    # 2. The cached result equals the freshly-extracted one.
    assert second == first

    # 3. Counters: exactly one new hit and one new miss across the two calls.
    assert cache.hits == hits0 + 1
    assert cache.misses == misses0 + 1

    # 4. Timing: the cache hit is far faster than the live extraction (no Gemini).
    assert t_second < t_first
    assert t_second < 0.1, f"cache hit took {t_second:.3f}s — expected ~instant"

    print(f"\nCACHE-HIT PROOF: first(miss)={t_first:.3f}s  second(hit)={t_second:.4f}s  "
          f"speedup={t_first / max(t_second, 1e-6):.0f}x  equal={second == first}")
