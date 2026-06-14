"""Scale / cost projection model for the claims pipeline.

We do NOT load-test the live Gemini path at volume — the vision extraction +
semantic + verifier chain is the ~30s, rate-limited, expensive bottleneck. Instead
we PROJECT full-pipeline cost and throughput from already-measured per-claim numbers
(from the observability run) and a configurable worker / Gemini-concurrency model.

All inputs are constants at the top, sourced from the observability run and from
`app.config` (the same per-1M-token rates the in-app cost estimator uses), so the
projection stays consistent with what the product actually reports per claim.

PURE-ADDITIVE: pure arithmetic, no network, no Gemini, no DB.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------- #
# Measured inputs (from the observability run on gemini-2.5-flash:             #
#   extraction + semantic + verifier, single claim)                            #
# ---------------------------------------------------------------------------- #
TOKENS_PER_CLAIM = 4_000          # ≈ total in+out tokens across the LLM steps
INR_PER_CLAIM = 0.40              # ≈ rupee cost/claim (matches in-app cost estimate)
WALL_SECONDS_PER_CLAIM = 30.0     # ≈ end-to-end wall time of the LLM-bound pipeline

# FX + a USD view, sourced from the same config the cost estimator uses so the
# projection and the in-app per-claim cost never diverge.
try:  # keep the module importable without the app on the path (e.g. bare math)
    from app.config import settings
    USD_TO_INR = settings.usd_to_inr
except Exception:  # noqa: BLE001 — fall back to the documented estimate
    USD_TO_INR = 84.0
USD_PER_CLAIM = INR_PER_CLAIM / USD_TO_INR

# ---------------------------------------------------------------------------- #
# Capacity model knobs                                                          #
# ---------------------------------------------------------------------------- #
# K = number of async workers (Celery slots) processing claims concurrently.
# Each in-flight claim holds one Gemini "lane" for ~WALL_SECONDS_PER_CLAIM.
DEFAULT_WORKERS = 8

# The REAL ceiling is the Gemini request rate limit, not CPU. A flash tier might
# allow ~N requests/min; each claim issues a few LLM calls. We model the ceiling
# as a maximum number of *concurrent* claims the Gemini quota sustains. Beyond
# this, adding workers does NOT raise throughput — calls queue / 429.
GEMINI_CONCURRENCY_CEILING = 16   # concurrent claims the quota sustains (tune to plan)


@dataclass
class Projection:
    workers: int
    effective_concurrency: int       # min(workers, gemini ceiling)
    rate_limited: bool
    claims_per_min: float
    claims_per_hour: float
    claims_per_day: float


def cost_per_n(n: int, inr_per_claim: float = INR_PER_CLAIM) -> dict:
    """Total cost to process `n` claims (linear in per-claim cost)."""
    inr = inr_per_claim * n
    return {"n": n, "inr": round(inr, 2), "usd": round(inr / USD_TO_INR, 2)}


def throughput(workers: int = DEFAULT_WORKERS,
               wall_seconds_per_claim: float = WALL_SECONDS_PER_CLAIM,
               gemini_ceiling: int = GEMINI_CONCURRENCY_CEILING) -> Projection:
    """Sustained throughput as a function of worker concurrency K.

    Throughput scales LINEARLY with K up to the Gemini concurrency ceiling, then
    flattens (the rate limit is the real wall). One worker completes
    1/wall claims/sec; K effective workers do K/wall claims/sec.
    """
    effective = min(workers, gemini_ceiling)
    per_worker_per_sec = 1.0 / wall_seconds_per_claim if wall_seconds_per_claim > 0 else 0.0
    per_sec = effective * per_worker_per_sec
    return Projection(
        workers=workers,
        effective_concurrency=effective,
        rate_limited=workers > gemini_ceiling,
        claims_per_min=per_sec * 60.0,
        claims_per_hour=per_sec * 3600.0,
        claims_per_day=per_sec * 86400.0,
    )


def build_model(deterministic_decisions_per_sec: float | None = None) -> dict:
    """Assemble the full projection: cost tiers, a throughput table over a range of
    worker counts, and (optionally) the measured deterministic-core throughput so
    the LLM-bound vs rules-bound contrast is explicit."""
    cost_tiers = {n: cost_per_n(n) for n in (1_000, 10_000, 1_000_000)}
    worker_grid = [1, 2, 4, 8, 16, 32, 64]
    table = [throughput(k) for k in worker_grid]
    return {
        "inputs": {
            "tokens_per_claim": TOKENS_PER_CLAIM,
            "inr_per_claim": INR_PER_CLAIM,
            "usd_per_claim": round(USD_PER_CLAIM, 6),
            "wall_seconds_per_claim": WALL_SECONDS_PER_CLAIM,
            "gemini_concurrency_ceiling": GEMINI_CONCURRENCY_CEILING,
            "usd_to_inr": USD_TO_INR,
        },
        "cost_tiers": cost_tiers,
        "throughput_table": table,
        "deterministic_decisions_per_sec": deterministic_decisions_per_sec,
    }


def _print_model(model: dict) -> None:
    inp = model["inputs"]
    print("Scale projection — full pipeline (PROJECTED from measured per-claim numbers)\n")
    print("Measured per-claim inputs (observability run, gemini-2.5-flash):")
    print(f"  tokens/claim:           {inp['tokens_per_claim']:,}")
    print(f"  cost/claim:             ₹{inp['inr_per_claim']:.2f}  (≈ ${inp['usd_per_claim']:.4f})")
    print(f"  wall/claim:             {inp['wall_seconds_per_claim']:.0f} s")
    print(f"  Gemini concurrency cap: {inp['gemini_concurrency_ceiling']} concurrent claims (rate-limit ceiling)")
    print()

    print("Cost to process N claims (linear in per-claim cost):")
    print(f"  {'N':>12} | {'₹ (INR)':>14} | {'$ (USD)':>12}")
    print(f"  {'-'*12}-+-{'-'*14}-+-{'-'*12}")
    for n, c in model["cost_tiers"].items():
        print(f"  {n:>12,} | {c['inr']:>14,.2f} | {c['usd']:>12,.2f}")
    print()

    print("Sustained throughput vs worker concurrency K (linear until the Gemini ceiling):")
    print(f"  {'K workers':>10} | {'effective':>9} | {'claims/min':>11} | {'claims/day':>14} | note")
    print(f"  {'-'*10}-+-{'-'*9}-+-{'-'*11}-+-{'-'*14}-+------")
    for p in model["throughput_table"]:
        note = "RATE-LIMITED (Gemini ceiling)" if p.rate_limited else "linear scaling"
        print(f"  {p.workers:>10} | {p.effective_concurrency:>9} | "
              f"{p.claims_per_min:>11,.1f} | {p.claims_per_day:>14,.0f} | {note}")
    print()

    dps = model["deterministic_decisions_per_sec"]
    if dps is not None:
        ceil = model["throughput_table"][-1]
        print("Contrast — LLM-bound vs rules-bound:")
        print(f"  LLM-bound full pipeline:  ~{ceil.claims_per_day:,.0f} claims/day at "
              f"K={ceil.workers} (Gemini ceiling)")
        print(f"  Rules-bound decision core: ~{dps:,.0f} decisions/sec on ONE core "
              f"(≈ {dps*86400:,.0f}/day) — never the bottleneck.")
    print()


def main() -> None:
    # Try to fold in the measured deterministic-core throughput for the contrast row.
    dps = None
    try:
        from loadtest.decision_benchmark import run_benchmark
        dps = run_benchmark(passes=10)["decisions_per_sec"]
    except Exception:  # noqa: BLE001 — projection still prints without the benchmark
        pass
    _print_model(build_model(dps))


if __name__ == "__main__":
    main()
