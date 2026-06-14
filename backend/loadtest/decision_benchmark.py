"""Deterministic micro-benchmark for the decision CORE.

Runs the REAL deterministic decision logic (`decide_from_facts` — 5 rule checks +
financial calculator + aggregator) over the 630 synthetic labeled cases repeatedly,
and reports throughput (decisions/sec) + p50/p95/p99 per-decision latency.

NO Gemini, NO network, NO DB — this measures the rules-bound path that runs after
the LLM extraction has produced facts. It is the explicit contrast to the
LLM-bound full pipeline (~30s/claim): the deterministic core should do thousands
of decisions/sec on a single core.

PURE-ADDITIVE: imports the production decision path unchanged; runs nothing live.
"""
from __future__ import annotations

import statistics
import time

from app.config import settings
from app.evalrunner.decision_eval import decide_from_facts
from app.evalrunner.synthetic import generate_cases
from app.services.policy_engine import PolicyEngine


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Nearest-rank percentile over an ascending-sorted list (pct in [0,100])."""
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(round((pct / 100.0) * len(sorted_vals) + 0.5)) - 1))
    return sorted_vals[k]


def run_benchmark(passes: int = 20, pe: PolicyEngine | None = None,
                  cases=None) -> dict:
    """Time `decide_from_facts` over every synthetic case, `passes` times.

    Returns a stats dict: total decisions, wall seconds, decisions/sec, and
    per-decision latency p50/p95/p99/min/max/mean (milliseconds). Deterministic
    and side-effect free; safe to call from tests with a small `passes`.
    """
    pe = pe or PolicyEngine(settings.policy_path)
    cases = cases if cases is not None else generate_cases(pe)
    n_cases = len(cases)

    # Per-decision latencies in seconds. Warm one pass first so import/JIT/policy
    # caches don't skew the first samples.
    for case in cases:
        decide_from_facts(case, pe)

    latencies: list[float] = []
    wall_start = time.perf_counter()
    for _ in range(passes):
        for case in cases:
            t0 = time.perf_counter()
            decide_from_facts(case, pe)
            latencies.append(time.perf_counter() - t0)
    wall = time.perf_counter() - wall_start

    total = passes * n_cases
    latencies.sort()
    to_ms = 1000.0
    return {
        "n_cases": n_cases,
        "passes": passes,
        "total_decisions": total,
        "wall_seconds": wall,
        "decisions_per_sec": (total / wall) if wall > 0 else 0.0,
        "latency_ms": {
            "p50": _percentile(latencies, 50) * to_ms,
            "p95": _percentile(latencies, 95) * to_ms,
            "p99": _percentile(latencies, 99) * to_ms,
            "min": latencies[0] * to_ms,
            "max": latencies[-1] * to_ms,
            "mean": (statistics.fmean(latencies) * to_ms) if latencies else 0.0,
        },
    }


def _fmt(stats: dict) -> str:
    lat = stats["latency_ms"]
    return (
        f"Decision-core micro-benchmark (no Gemini, single core)\n"
        f"  cases:            {stats['n_cases']}\n"
        f"  passes:           {stats['passes']}\n"
        f"  total decisions:  {stats['total_decisions']:,}\n"
        f"  wall:             {stats['wall_seconds']:.3f} s\n"
        f"  THROUGHPUT:       {stats['decisions_per_sec']:,.0f} decisions/sec\n"
        f"  latency p50:      {lat['p50']:.4f} ms\n"
        f"  latency p95:      {lat['p95']:.4f} ms\n"
        f"  latency p99:      {lat['p99']:.4f} ms\n"
        f"  latency min/mean/max: {lat['min']:.4f} / {lat['mean']:.4f} / {lat['max']:.4f} ms"
    )


def main() -> None:
    stats = run_benchmark(passes=30)
    print(_fmt(stats))


if __name__ == "__main__":
    main()
