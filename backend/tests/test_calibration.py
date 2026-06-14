"""Deterministic tests for confidence calibration. No Gemini / no network.

Covers the metrics (ECE, reliability curve), the calibrators (monotonicity,
clamping, JSON round-trip, that isotonic reduces ECE on miscalibrated data), and
the OFF-by-default wiring into confidence.compute (raw composite unchanged when
disabled; identity ≈ input when enabled)."""
import json

import pytest

from app.services.calibration import (
    IsotonicCalibrator,
    PlattCalibrator,
    expected_calibration_error,
    fit_isotonic,
    fit_platt,
    load_calibrator,
    reliability_curve,
    save_calibrator,
)


# --------------------------------------------------------------------------- #
# ECE + reliability curve                                                     #
# --------------------------------------------------------------------------- #

def test_ece_zero_for_perfectly_calibrated():
    # Build data where, within each of 5 bins, accuracy == mean confidence exactly.
    # Bin centers 0.1, 0.3, 0.5, 0.7, 0.9 with matching fraction correct.
    confidences, correct = [], []
    for center, frac_correct_per_10 in [(0.1, 1), (0.3, 3), (0.5, 5), (0.7, 7), (0.9, 9)]:
        for i in range(10):
            confidences.append(center)
            correct.append(1 if i < frac_correct_per_10 else 0)
    ece = expected_calibration_error(confidences, correct, n_bins=5)
    assert ece == pytest.approx(0.0, abs=1e-9)


def test_ece_positive_for_miscalibrated():
    # Overconfident: everyone says 0.9 but only half are correct.
    confidences = [0.9] * 20
    correct = [1] * 10 + [0] * 10
    ece = expected_calibration_error(confidences, correct, n_bins=10)
    assert ece == pytest.approx(0.4, abs=1e-9)  # |0.5 - 0.9|


def test_ece_empty_is_zero():
    assert expected_calibration_error([], [], n_bins=10) == 0.0


def test_reliability_curve_bins_and_counts():
    confidences = [0.05, 0.15, 0.95, 0.95]
    correct = [0, 1, 1, 0]
    curve = reliability_curve(confidences, correct, n_bins=10)
    # 3 non-empty bins: [0,0.1), [0.1,0.2), [0.9,1.0]
    assert len(curve) == 3
    last = curve[-1]
    assert last["count"] == 2 and last["accuracy"] == pytest.approx(0.5)
    assert last["mean_confidence"] == pytest.approx(0.95)


def test_reliability_curve_includes_confidence_one():
    # confidence == 1.0 must fall in the last (closed) bin, not be dropped.
    curve = reliability_curve([1.0, 1.0], [1, 0], n_bins=10)
    assert len(curve) == 1 and curve[0]["count"] == 2


# --------------------------------------------------------------------------- #
# Isotonic calibrator                                                         #
# --------------------------------------------------------------------------- #

def _miscalibrated_overconfident():
    """Overconfident dataset: confidences cluster ~0.9 but only ~50% correct,
    plus a spread of lower confidences that ARE roughly calibrated."""
    confidences, correct = [], []
    # 40 high-confidence (0.9) predictions, only 50% correct -> very overconfident.
    for i in range(40):
        confidences.append(0.9)
        correct.append(1 if i % 2 == 0 else 0)
    # 20 mid-confidence (0.5) predictions, ~50% correct -> calibrated here.
    for i in range(20):
        confidences.append(0.5)
        correct.append(1 if i % 2 == 0 else 0)
    # 20 low-confidence (0.2) predictions, ~20% correct -> calibrated here.
    for i in range(20):
        confidences.append(0.2)
        correct.append(1 if i < 4 else 0)
    return confidences, correct


def test_isotonic_is_monotone_nondecreasing():
    confidences, correct = _miscalibrated_overconfident()
    iso = fit_isotonic(confidences, correct)
    xs = [i / 50 for i in range(51)]
    ys = [iso.apply(x) for x in xs]
    assert all(b >= a - 1e-12 for a, b in zip(ys, ys[1:]))


def test_isotonic_apply_in_unit_interval():
    confidences, correct = _miscalibrated_overconfident()
    iso = fit_isotonic(confidences, correct)
    for x in (-5.0, -0.1, 0.0, 0.37, 0.9, 1.0, 2.0):
        assert 0.0 <= iso.apply(x) <= 1.0


def test_isotonic_round_trip():
    confidences, correct = _miscalibrated_overconfident()
    iso = fit_isotonic(confidences, correct)
    restored = IsotonicCalibrator.from_dict(iso.to_dict())
    for x in (0.0, 0.2, 0.5, 0.9, 1.0):
        assert restored.apply(x) == pytest.approx(iso.apply(x))


def test_isotonic_reduces_ece_on_miscalibrated():
    confidences, correct = _miscalibrated_overconfident()
    before = expected_calibration_error(confidences, correct, n_bins=10)
    iso = fit_isotonic(confidences, correct)
    recalibrated = [iso.apply(c) for c in confidences]
    after = expected_calibration_error(recalibrated, correct, n_bins=10)
    assert after < before
    assert before > 0.1  # sanity: the synthetic data really is miscalibrated


# --------------------------------------------------------------------------- #
# Platt calibrator                                                            #
# --------------------------------------------------------------------------- #

def test_platt_round_trip_and_range():
    confidences, correct = _miscalibrated_overconfident()
    platt = fit_platt(confidences, correct)
    restored = PlattCalibrator.from_dict(platt.to_dict())
    for x in (0.0, 0.5, 1.0):
        assert 0.0 <= platt.apply(x) <= 1.0
        assert restored.apply(x) == pytest.approx(platt.apply(x))


def test_platt_single_class_falls_back_to_identity():
    platt = fit_platt([0.3, 0.7, 0.9], [1, 1, 1])
    assert platt.a == 1.0 and platt.b == 0.0


# --------------------------------------------------------------------------- #
# Persistence                                                                 #
# --------------------------------------------------------------------------- #

def test_save_load_calibrator(tmp_path):
    confidences, correct = _miscalibrated_overconfident()
    iso = fit_isotonic(confidences, correct)
    path = tmp_path / "calib.json"
    save_calibrator(iso, path)
    loaded = load_calibrator(path)
    assert isinstance(loaded, IsotonicCalibrator)
    assert loaded.apply(0.9) == pytest.approx(iso.apply(0.9))


def test_load_missing_returns_none(tmp_path):
    assert load_calibrator(tmp_path / "nope.json") is None


def test_load_invalid_returns_none(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    assert load_calibrator(bad) is None
    bad2 = tmp_path / "bad2.json"
    bad2.write_text(json.dumps({"method": "unknown-method"}))
    assert load_calibrator(bad2) is None


# --------------------------------------------------------------------------- #
# confidence.compute wiring — OFF by default                                  #
# --------------------------------------------------------------------------- #

def test_compute_disabled_returns_raw_composite():
    """With calibration disabled (the default), compute returns the raw composite
    EXACTLY. Known input -> known output: 0.30*0.92 + 0.30*1 + 0.20*1 + 0.20*0.9."""
    from app.services.confidence import compute

    c = compute(extraction_quality=0.92, rule_certainty=1.0, completeness=1.0,
                verifier_agreement=0.9, failures=0)
    expected = round(0.30 * 0.92 + 0.30 * 1.0 + 0.20 * 1.0 + 0.20 * 0.9, 3)
    assert c.final == expected  # 0.956


def test_compute_with_identity_calibrator_enabled(tmp_path, monkeypatch):
    """With an identity calibrator loaded + calibration enabled, the output ≈ input
    (proves the wiring path runs and is a no-op for the identity map)."""
    import app.services.confidence as confmod
    from app.config import settings

    # Identity isotonic map over [0,1].
    identity = IsotonicCalibrator(x=[0.0, 1.0], y=[0.0, 1.0])
    path = tmp_path / "identity.json"
    save_calibrator(identity, path)

    confmod._calibrator.cache_clear()
    monkeypatch.setattr(settings, "confidence_calibration_enabled", True)
    monkeypatch.setattr(settings, "calibration_map_path", str(path))

    c = confmod.compute(extraction_quality=0.92, rule_certainty=1.0, completeness=1.0,
                        verifier_agreement=0.9, failures=0)
    assert c.final == pytest.approx(0.956, abs=1e-3)  # identity map -> raw composite
    confmod._calibrator.cache_clear()
