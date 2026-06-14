"""Refit the confidence calibrator on OPERATOR OUTCOME labels (the right domain).

The committed calibration_map.json is extraction-domain (which is why it ships OFF). The
correct training signal for the DECISION confidence is operator agreement on the final
decision — captured via POST /api/claims/{id}/mark-outcome into the append-only audit log.
This job reads those (confidence, correct) pairs and fits a calibrator:

  * Platt (logistic) at low volume — isotonic overfits below a few hundred samples;
  * isotonic once enough labels accumulate (default ≥ 200).

It reports held-out ECE before/after (a simple split) and writes the calibrator JSON.
Run:  cd backend && .venv/bin/python -m scripts.recalibrate_from_outcomes [--out PATH] [--min-isotonic N]

NOT run automatically; an operator/cron invokes it, reviews the ECE, then flips
CONFIDENCE_CALIBRATION_ENABLED on.
"""
from __future__ import annotations

import argparse

from app.services import calibration


def recalibrate(pairs: list[dict], min_isotonic: int = 200, holdout_frac: float = 0.3):
    """Pure core: fit a calibrator from [{confidence, correct}, ...] and return
    (calibrator, report). Splits deterministically (every k-th sample to held-out) so the
    ECE-before/after numbers are reproducible. Returns (None, report) if there are too few
    labels (< 8) or only one outcome class to fit meaningfully."""
    conf = [float(p["confidence"]) for p in pairs]
    corr = [1 if p["correct"] else 0 for p in pairs]
    n = len(conf)
    report: dict = {"n": n, "method": None, "ece_before": None, "ece_after": None,
                    "n_train": 0, "n_holdout": 0}
    if n < 8 or len(set(corr)) < 2:
        report["note"] = "insufficient or single-class labels — not enough to calibrate"
        return None, report
    # Deterministic split: every 1/holdout_frac-th sample goes to the held-out set.
    step = max(2, round(1 / holdout_frac))
    tr_c, tr_y, ho_c, ho_y = [], [], [], []
    for i, (c, y) in enumerate(zip(conf, corr)):
        if i % step == 0:
            ho_c.append(c); ho_y.append(y)
        else:
            tr_c.append(c); tr_y.append(y)
    method = "isotonic" if n >= min_isotonic else "platt"
    cal = (calibration.fit_isotonic(tr_c, tr_y) if method == "isotonic"
           else calibration.fit_platt(tr_c, tr_y))
    ece_before = calibration.expected_calibration_error(ho_c, ho_y)
    ece_after = calibration.expected_calibration_error([cal.apply(c) for c in ho_c], ho_y)
    report.update({"method": method, "ece_before": round(ece_before, 4),
                   "ece_after": round(ece_after, 4),
                   "n_train": len(tr_c), "n_holdout": len(ho_c)})
    return cal, report


def main() -> None:
    from app.config import settings
    from app.services.audit import outcome_labels

    ap = argparse.ArgumentParser(description="Recalibrate confidence on operator outcome labels")
    ap.add_argument("--out", default=settings.calibration_map_path,
                    help="where to write the fitted calibrator JSON")
    ap.add_argument("--min-isotonic", type=int, default=200,
                    help="sample count at/above which to switch Platt→isotonic")
    args = ap.parse_args()

    pairs = outcome_labels()
    cal, report = recalibrate(pairs, min_isotonic=args.min_isotonic)
    print(report)
    if cal is None:
        print("No calibrator written.")
        return
    path = calibration.save_calibrator(cal, args.out)
    print(f"Wrote {report['method']} calibrator → {path}. "
          f"Review the ECE, then set CONFIDENCE_CALIBRATION_ENABLED=true to enable.")


if __name__ == "__main__":
    main()
