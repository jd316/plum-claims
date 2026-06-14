"""Phase 6: conformal risk control + operator-outcome recalibration (pure cores)."""
from app.services.conformal import risk_controlled_threshold
from scripts.recalibrate_from_outcomes import recalibrate


# --- Conformal risk control -------------------------------------------------

def test_threshold_separates_reliable_from_unreliable():
    # High-confidence items are mostly correct; low-confidence items are mostly wrong.
    # The gate should pick a threshold that keeps the auto-approved error within alpha.
    scores = [0.95] * 50 + [0.55] * 50
    correct = [1] * 49 + [0] + [0] * 40 + [1] * 10  # ~2% error high, ~80% error low
    # alpha=0.25 is achievable for the reliable bucket at n=50 under the (conservative)
    # Hoeffding bound; adding the unreliable 0.55 bucket would blow past it, so the gate
    # keeps only the 0.95 bucket — exactly the desired separation.
    out = risk_controlled_threshold(scores, correct, alpha=0.25, delta=0.05)
    assert out["threshold"] >= 0.95          # only the reliable bucket is auto-approved
    assert out["empirical_error"] <= 0.10
    assert out["error_upper_bound"] <= 0.25
    assert 0 < out["auto_approve_rate"] <= 0.5


def test_impossible_alpha_approves_nothing():
    # No achievable set meets a 0% error bound under finite samples → approve nothing.
    out = risk_controlled_threshold([0.9, 0.8, 0.7], [1, 0, 1], alpha=0.0)
    assert out["auto_approve_rate"] == 0.0
    assert out["threshold"] > 0.9


def test_empty_calibration_set_is_safe():
    out = risk_controlled_threshold([], [], alpha=0.05)
    assert out["n_total"] == 0 and out["auto_approve_rate"] == 0.0


# --- Recalibration on operator outcome labels -------------------------------

def test_recalibrate_too_few_labels_returns_none():
    cal, report = recalibrate([{"confidence": 0.9, "correct": True}] * 3)
    assert cal is None and report["method"] is None


def test_recalibrate_platt_at_low_volume():
    # 40 labels where higher confidence ⇒ more often correct → Platt fit, improves ECE.
    pairs = []
    for i in range(40):
        c = 0.5 + 0.5 * (i / 39)
        pairs.append({"confidence": round(c, 3), "correct": (i % 5 != 0) if c > 0.7 else (i % 2 == 0)})
    cal, report = recalibrate(pairs, min_isotonic=200)
    assert cal is not None and report["method"] == "platt"
    assert report["n_holdout"] > 0 and report["ece_after"] is not None
    # apply() stays in [0,1]
    assert all(0.0 <= cal.apply(p["confidence"]) <= 1.0 for p in pairs)


def test_recalibrate_switches_to_isotonic_at_volume():
    pairs = [{"confidence": round(0.5 + 0.5 * (i % 100) / 99, 3),
              "correct": (i % 3 != 0)} for i in range(250)]
    cal, report = recalibrate(pairs, min_isotonic=200)
    assert cal is not None and report["method"] == "isotonic"
