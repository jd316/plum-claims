"""Deterministic tests for the load-test / scale-projection tooling.

No Gemini, no network, no Locust launch — just the math + import sanity.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from loadtest import decision_benchmark, scale_projection

_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Decision-core benchmark                                                      #
# --------------------------------------------------------------------------- #

def test_benchmark_returns_sane_stats():
    """A small pass count yields positive throughput and ordered percentiles."""
    stats = decision_benchmark.run_benchmark(passes=2)
    assert stats["passes"] == 2
    assert stats["n_cases"] > 0
    assert stats["total_decisions"] == 2 * stats["n_cases"]
    assert stats["wall_seconds"] > 0
    assert stats["decisions_per_sec"] > 0

    lat = stats["latency_ms"]
    assert lat["min"] <= lat["p50"] <= lat["p95"] <= lat["p99"] <= lat["max"]
    assert lat["min"] <= lat["mean"] <= lat["max"]


def test_benchmark_core_is_fast():
    """Sanity: the rules-bound core does well over 1000 decisions/sec — it is never
    the bottleneck. Generous floor to stay stable on slow CI."""
    stats = decision_benchmark.run_benchmark(passes=3)
    assert stats["decisions_per_sec"] > 1_000


def test_percentile_helper_monotonic():
    vals = [float(i) for i in range(1, 101)]  # 1..100 ascending
    p = decision_benchmark._percentile
    assert p(vals, 50) <= p(vals, 95) <= p(vals, 99)
    assert p(vals, 99) <= vals[-1]
    assert p([], 50) == 0.0


# --------------------------------------------------------------------------- #
# Scale projection math                                                        #
# --------------------------------------------------------------------------- #

def test_cost_per_n_is_linear():
    per = scale_projection.INR_PER_CLAIM
    assert scale_projection.cost_per_n(1_000)["inr"] == pytest.approx(per * 1_000)
    assert scale_projection.cost_per_n(1_000_000)["inr"] == pytest.approx(per * 1_000_000)
    # USD view divides by the configured FX rate (rounded to paise/cents).
    c = scale_projection.cost_per_n(10_000)
    assert c["usd"] == pytest.approx(round(c["inr"] / scale_projection.USD_TO_INR, 2))


def test_throughput_scales_linearly_below_ceiling():
    """Below the Gemini ceiling, doubling workers doubles claims/day."""
    ceiling = scale_projection.GEMINI_CONCURRENCY_CEILING
    k1 = ceiling // 4 or 1
    k2 = k1 * 2
    assert k2 <= ceiling
    p1 = scale_projection.throughput(workers=k1)
    p2 = scale_projection.throughput(workers=k2)
    assert not p1.rate_limited and not p2.rate_limited
    assert p2.claims_per_day == pytest.approx(2 * p1.claims_per_day)
    assert p2.claims_per_min == pytest.approx(2 * p1.claims_per_min)


def test_throughput_flattens_at_rate_limit_ceiling():
    """Above the ceiling, adding workers does not raise throughput (Gemini quota)."""
    ceiling = scale_projection.GEMINI_CONCURRENCY_CEILING
    at = scale_projection.throughput(workers=ceiling)
    over = scale_projection.throughput(workers=ceiling * 4)
    assert at.effective_concurrency == ceiling
    assert over.effective_concurrency == ceiling
    assert over.rate_limited is True
    assert over.claims_per_day == pytest.approx(at.claims_per_day)


def test_throughput_matches_wall_time_formula():
    """One worker completes 1/wall claims per second."""
    wall = scale_projection.WALL_SECONDS_PER_CLAIM
    p = scale_projection.throughput(workers=1)
    assert p.claims_per_min == pytest.approx(60.0 / wall)
    assert p.claims_per_day == pytest.approx(86400.0 / wall)


def test_build_model_shape():
    model = scale_projection.build_model(deterministic_decisions_per_sec=12345.0)
    assert set(model["cost_tiers"]) == {1_000, 10_000, 1_000_000}
    assert model["deterministic_decisions_per_sec"] == 12345.0
    assert model["inputs"]["tokens_per_claim"] == scale_projection.TOKENS_PER_CLAIM
    assert len(model["throughput_table"]) >= 3
    # Last grid entry should be rate-limited (grid runs past the ceiling).
    assert model["throughput_table"][-1].rate_limited is True


# --------------------------------------------------------------------------- #
# Locustfile imports without launching                                         #
# --------------------------------------------------------------------------- #

def test_locustfile_imports_without_launch():
    """Import the locustfile in a SUBPROCESS and confirm it defines the two user
    classes without launching a load test.

    A subprocess is used because importing locust monkey-patches ssl via gevent;
    doing that inside the pytest process (where ssl is already imported) triggers a
    RecursionError on recent Pythons. The subprocess imports cleanly from a fresh
    interpreter, which is exactly the real `locust -f locustfile.py` entrypoint."""
    code = (
        "from loadtest.locustfile import ReadOnlyUser, AsyncEnqueueUser\n"
        "from locust import HttpUser\n"
        "assert issubclass(ReadOnlyUser, HttpUser)\n"
        "assert issubclass(AsyncEnqueueUser, HttpUser)\n"
        "print('OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(_BACKEND_DIR),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"locustfile import failed:\n{proc.stderr}"
    assert "OK" in proc.stdout
