"""Conformal risk control for the auto-approve gate.

Instead of claiming a point probability ("confidence 0.9 ≈ 90% correct"), which is
fragile to mis-calibration, this picks a confidence THRESHOLD τ on the auto-approve gate
with a distribution-free guarantee: among claims auto-approved (score ≥ τ), the error rate
is ≤ α with high probability, under only the exchangeability of the calibration set. This
is the "risk-controlled confidence" remediation in architecture.md §10 / the roadmap, and
mirrors what scikit-learn-contrib/MAPIE provides — kept dependency-free here (a one-sided
Hoeffding bound + fixed-sequence testing) so it is pure and unit-testable.

Pure + deterministic. Nothing in the live pipeline imports this unless an operator wires the
chosen threshold into the auto-approve decision; by default behaviour is unchanged.
"""
from __future__ import annotations

import math


def _hoeffding_upper(p_hat: float, n: int, delta: float) -> float:
    """One-sided Hoeffding upper confidence bound on a [0,1] mean from n samples at
    level delta: P(true_mean > p_hat + ε) ≤ delta, ε = sqrt(ln(1/δ)/(2n))."""
    if n == 0:
        return 1.0
    eps = math.sqrt(math.log(1.0 / delta) / (2.0 * n))
    return min(1.0, p_hat + eps)


def risk_controlled_threshold(scores, correct, alpha: float, delta: float = 0.05) -> dict:
    """Pick the most permissive score threshold τ such that the error rate among
    auto-approved items (score ≥ τ) has a (1−δ) upper bound ≤ α.

    Args:
        scores:  per-decision confidence scores (0–1), one per labelled outcome.
        correct: 1 if the automated decision was right, 0 otherwise (operator labels).
        alpha:   maximum tolerated error rate on the auto-approved set (e.g. 0.05).
        delta:   confidence level for the upper bound (default 0.05 → 95%).

    Returns a dict: {threshold, auto_approve_rate, empirical_error, error_upper_bound,
    n_approved, n_total}. If even the strictest non-empty set cannot meet the bound, the
    threshold is set above the max score (auto-approve nothing) — the safe fallback.

    Uses fixed-sequence testing: candidate thresholds are walked from strictest (highest
    score, smallest/safest approved set) toward most permissive, accepting while the bound
    holds and stopping at the first violation — which controls the family-wise error.
    """
    pairs = sorted(zip([float(s) for s in scores], [int(c) for c in correct]),
                   key=lambda p: p[0], reverse=True)
    n_total = len(pairs)
    if n_total == 0:
        return {"threshold": 1.01, "auto_approve_rate": 0.0, "empirical_error": 0.0,
                "error_upper_bound": 1.0, "n_approved": 0, "n_total": 0}
    uniq_desc = sorted({p[0] for p in pairs}, reverse=True)
    chosen = None
    for tau in uniq_desc:
        approved = [c for s, c in pairs if s >= tau]
        n = len(approved)
        err = sum(1 - c for c in approved) / n
        ucb = _hoeffding_upper(err, n, delta)
        if ucb <= alpha:
            chosen = tau          # guarantee still holds → keep lowering τ (more permissive)
        else:
            break                 # first violation → stop (fixed-sequence testing)
    if chosen is None:
        # Even the top item(s) can't meet the bound → approve nothing.
        return {"threshold": round(uniq_desc[0] + 0.01, 6), "auto_approve_rate": 0.0,
                "empirical_error": 0.0, "error_upper_bound": 1.0,
                "n_approved": 0, "n_total": n_total}
    approved = [c for s, c in pairs if s >= chosen]
    n = len(approved)
    err = sum(1 - c for c in approved) / n
    return {"threshold": float(chosen), "auto_approve_rate": n / n_total,
            "empirical_error": err, "error_upper_bound": _hoeffding_upper(err, n, delta),
            "n_approved": n, "n_total": n_total}
