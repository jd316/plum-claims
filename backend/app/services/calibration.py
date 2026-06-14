"""Confidence calibration machinery — make a raw confidence score statistically
meaningful so that "0.9 ≈ 90% correct".

This module is PURE and deterministic given fixed input:

  - `expected_calibration_error` / `reliability_curve`: standard ECE + the binned
    (mean_confidence, accuracy, count) table behind a reliability diagram.
  - `fit_isotonic` / `fit_platt`: fit a monotone (isotonic) or logistic (Platt)
    map from raw confidence -> calibrated probability on labelled (confidence,
    correct) pairs. Both calibrators expose `.apply(raw) -> float` clamped to
    [0, 1] and `.to_dict()` / `.from_dict()` for JSON save / load.
  - `save_calibrator` / `load_calibrator`: persist a fitted calibrator to JSON.

It depends only on numpy + scikit-learn (libraries, not services). Nothing here is
imported by the live decision pipeline unless calibration is explicitly enabled in
settings; by default the product's confidence behaviour is unchanged.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


# --------------------------------------------------------------------------- #
# Metrics: ECE + reliability curve                                            #
# --------------------------------------------------------------------------- #

def _as_arrays(confidences, correct) -> tuple[np.ndarray, np.ndarray]:
    conf = np.asarray(confidences, dtype=float)
    corr = np.asarray(correct, dtype=float)
    if conf.shape != corr.shape:
        raise ValueError("confidences and correct must have the same length")
    return conf, corr


def reliability_curve(confidences, correct, n_bins: int = 10) -> list[dict]:
    """Bin predictions by confidence into `n_bins` equal-width bins over [0, 1] and
    return, for each NON-EMPTY bin, a dict with:

        {bin_lower, bin_upper, mean_confidence, accuracy, count}

    This is the data behind a reliability diagram: a perfectly calibrated model has
    mean_confidence == accuracy in every bin.
    """
    conf, corr = _as_arrays(confidences, correct)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out: list[dict] = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        # Last bin is closed on the right so confidence == 1.0 is included.
        if i == n_bins - 1:
            mask = (conf >= lo) & (conf <= hi)
        else:
            mask = (conf >= lo) & (conf < hi)
        count = int(mask.sum())
        if count == 0:
            continue
        out.append({
            "bin_lower": float(lo),
            "bin_upper": float(hi),
            "mean_confidence": float(conf[mask].mean()),
            "accuracy": float(corr[mask].mean()),
            "count": count,
        })
    return out


def expected_calibration_error(confidences, correct, n_bins: int = 10) -> float:
    """Standard Expected Calibration Error: the count-weighted average gap between
    a bin's mean confidence and its empirical accuracy, over equal-width bins.

        ECE = sum_b (n_b / N) * | acc(b) - conf(b) |

    0.0 means perfectly calibrated. Returns 0.0 for an empty input.
    """
    conf, corr = _as_arrays(confidences, correct)
    n = conf.shape[0]
    if n == 0:
        return 0.0
    ece = 0.0
    for b in reliability_curve(conf, corr, n_bins=n_bins):
        ece += (b["count"] / n) * abs(b["accuracy"] - b["mean_confidence"])
    return float(ece)


# --------------------------------------------------------------------------- #
# Calibrators                                                                  #
# --------------------------------------------------------------------------- #

def _clamp01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


@dataclass
class IsotonicCalibrator:
    """Monotone non-decreasing calibration map, stored as a thinned (x, y) curve.

    `.apply(raw)` linearly interpolates the fitted step function and clamps to
    [0, 1]. Serialisable to/from a plain dict so it can be committed as JSON and
    reloaded with no scikit-learn fit at inference time.
    """
    x: list[float]
    y: list[float]

    method = "isotonic"

    def apply(self, raw: float) -> float:
        if not self.x:
            return _clamp01(raw)
        xp = np.asarray(self.x, dtype=float)
        yp = np.asarray(self.y, dtype=float)
        # np.interp clamps to endpoints outside [x[0], x[-1]] — the desired behaviour.
        return _clamp01(float(np.interp(float(raw), xp, yp)))

    def to_dict(self) -> dict:
        return {"method": self.method, "x": list(self.x), "y": list(self.y)}

    @classmethod
    def from_dict(cls, d: dict) -> "IsotonicCalibrator":
        return cls(x=[float(v) for v in d["x"]], y=[float(v) for v in d["y"]])


@dataclass
class PlattCalibrator:
    """Logistic (Platt) calibration: sigmoid(a * raw + b). Two scalars, trivially
    serialisable. `.apply` clamps to [0, 1] (the sigmoid is already in (0, 1))."""
    a: float
    b: float

    method = "platt"

    def apply(self, raw: float) -> float:
        z = self.a * float(raw) + self.b
        # Numerically stable logistic.
        if z >= 0:
            p = 1.0 / (1.0 + np.exp(-z))
        else:
            ez = np.exp(z)
            p = ez / (1.0 + ez)
        return _clamp01(float(p))

    def to_dict(self) -> dict:
        return {"method": self.method, "a": float(self.a), "b": float(self.b)}

    @classmethod
    def from_dict(cls, d: dict) -> "PlattCalibrator":
        return cls(a=float(d["a"]), b=float(d["b"]))


def fit_isotonic(confidences, correct) -> IsotonicCalibrator:
    """Fit an isotonic (monotone non-decreasing) calibration map. Deterministic.

    The fitted curve is stored as the unique sorted knot points of the isotonic
    regression so it round-trips through JSON without scikit-learn at load time.
    """
    conf, corr = _as_arrays(confidences, correct)
    if conf.shape[0] == 0:
        return IsotonicCalibrator(x=[], y=[])
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(conf, corr)
    # Sample the fitted step function at the sorted unique input confidences; this
    # captures every change point of the monotone map for interpolation at apply().
    xs = np.unique(conf)
    ys = iso.predict(xs)
    return IsotonicCalibrator(x=[float(v) for v in xs], y=[float(v) for v in ys])


def fit_platt(confidences, correct) -> PlattCalibrator:
    """Fit a logistic (Platt) calibrator. Deterministic (lbfgs on a 1-D feature).

    Falls back to the identity (a=1, b=0) if the labels are single-class, where a
    logistic fit is undefined.
    """
    conf, corr = _as_arrays(confidences, correct)
    if conf.shape[0] == 0 or len(np.unique(corr)) < 2:
        return PlattCalibrator(a=1.0, b=0.0)
    lr = LogisticRegression(C=1e6, solver="lbfgs")
    lr.fit(conf.reshape(-1, 1), corr.astype(int))
    return PlattCalibrator(a=float(np.ravel(lr.coef_)[0]), b=float(np.ravel(lr.intercept_)[0]))


# --------------------------------------------------------------------------- #
# Persistence                                                                  #
# --------------------------------------------------------------------------- #

_REGISTRY = {"isotonic": IsotonicCalibrator, "platt": PlattCalibrator}


def calibrator_from_dict(d: dict):
    """Reconstruct a calibrator from its serialised dict (dispatch on `method`)."""
    method = d.get("method")
    cls = _REGISTRY.get(method) if isinstance(method, str) else None
    if cls is None:
        raise ValueError(f"unknown calibrator method: {method!r}")
    return cls.from_dict(d)


def save_calibrator(cal, path: str | pathlib.Path) -> str:
    """Serialise a fitted calibrator to JSON at `path`. Returns the path."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cal.to_dict(), indent=2))
    return str(path)


def load_calibrator(path: str | pathlib.Path):
    """Load a calibrator from JSON. Returns None if the file is missing or invalid
    (so callers can fall back to the raw score without crashing)."""
    path = pathlib.Path(path)
    if not path.exists():
        return None
    try:
        return calibrator_from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, ValueError, KeyError, OSError):
        return None
