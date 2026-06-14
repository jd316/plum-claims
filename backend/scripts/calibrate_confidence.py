"""Fit a REAL confidence calibrator from live extraction outcomes.

This assembles a labelled (raw_confidence, correct) dataset from the production
vision extractor on documents whose ground-truth total is KNOWN, fits an isotonic
calibrator, and reports ECE before/after with a reliability table.

Primary source — TC004 hospital bill (ground-truth total = 1500), rendered across a
legibility spectrum (clean, rubber-stamped, phone-photo, stamped+phone, multilingual
header, and combinations). For each variant we run `extract_document` and record:

    (total_amount.confidence, int(round(total_amount.value or -1) == 1500))

This yields real (confidence, correct) pairs across legible -> degraded documents:
clean renders extract the total at high confidence and are correct; degraded variants
drop confidence and sometimes miss — exactly the spread a calibrator needs.

Secondary source (best-effort) — the existing CORD eval set, if present, folded in via
`score_cord` (per-item total_amount confidence + correctness).

Outputs (committed):
  - backend/calibration_map.json   — the fitted isotonic calibrator
  - docs/calibration_report.md      — dataset, ECE before/after, reliability table, caveats

Robust by design: every extraction is wrapped; if Gemini/network is constrained we
fit on whatever pairs were collected and note the small n. It never crashes.

Run from backend/:  .venv/bin/python scripts/calibrate_confidence.py
Bounded to ~25-30 live Gemini calls.
"""
from __future__ import annotations

import pathlib
import sys
import uuid

# Make `app` importable when run as a plain script from backend/.
_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.fixtures import messy  # noqa: E402
from app.fixtures.loader import load_cases  # noqa: E402
from app.models.schemas import DocumentInput  # noqa: E402
from app.services.calibration import (  # noqa: E402
    expected_calibration_error,
    fit_isotonic,
    reliability_curve,
    save_calibrator,
)

_REPO_ROOT = _BACKEND_DIR.parent
CALIB_PATH = _BACKEND_DIR / "calibration_map.json"
REPORT_PATH = _REPO_ROOT / "docs" / "calibration_report.md"
TC004_TOTAL = 1500.0


# --------------------------------------------------------------------------- #
# Variant rendering: a legibility spectrum on a known-total bill              #
# --------------------------------------------------------------------------- #

def _variant_images(case: dict) -> list[tuple[str, "object"]]:
    """Return (variant_name, PIL.Image) pairs spanning clean -> heavily degraded.

    All transforms are deterministic, so re-running yields the same images (and,
    modulo model nondeterminism, comparable pairs)."""
    from PIL import ImageEnhance, ImageFilter

    base = messy.render_tc004_bill(case)
    variants: list[tuple[str, object]] = [("clean", base)]

    # Single degradations.
    variants.append(("stamp_paid", messy.add_rubber_stamp(base, "PAID")))
    variants.append(("stamp_original", messy.add_rubber_stamp(base, "ORIGINAL")))
    variants.append(("phone_photo", messy.phone_photo(base)))
    variants.append(("multilingual", messy.multilingual_header(base)))

    # Combined degradations (harder).
    stamped = messy.add_rubber_stamp(base, "PAID")
    variants.append(("stamp_phone", messy.phone_photo(stamped)))
    variants.append(("multilingual_stamp", messy.add_rubber_stamp(
        messy.multilingual_header(base), "PAID")))
    variants.append(("multilingual_phone", messy.phone_photo(
        messy.multilingual_header(base))))

    # A graded blur ramp: progressively harder to read the total, spanning the
    # confidence axis (light blur stays legible; heavy blur degrades the read).
    for radius in (1.0, 2.0, 3.0, 4.5, 6.0):
        variants.append((f"blur_{radius:g}", base.filter(ImageFilter.GaussianBlur(radius))))

    # A low-contrast ramp (faded thermal-print look).
    for factor in (0.55, 0.4):
        variants.append((f"lowcontrast_{factor:g}",
                         ImageEnhance.Contrast(base).enhance(factor)))

    # Phone photo over a blurred + low-contrast base — the worst legibility.
    worst = messy.phone_photo(
        ImageEnhance.Contrast(base.filter(ImageFilter.GaussianBlur(3.0))).enhance(0.5))
    variants.append(("worst_case", worst))

    return variants


# --------------------------------------------------------------------------- #
# Pair collection                                                             #
# --------------------------------------------------------------------------- #

def _correct(value: float | None) -> int:
    return int(value is not None and round(value) == TC004_TOTAL)


def collect_tc004_pairs(tmp_dir: pathlib.Path) -> list[dict]:
    """Render the TC004 legibility spectrum, extract each variant, and return
    per-variant records with (confidence, correct). Bounded live Gemini calls."""
    from app.agents.extraction import extract_document

    cases = load_cases(settings.test_cases_path)
    case = next(c for c in cases if c["case_id"] == "TC004")

    tmp_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    for name, img in _variant_images(case):
        path = tmp_dir / f"{name}.png"
        img.convert("RGB").save(path)
        doc = DocumentInput(file_id=str(uuid.uuid4()), file_name=path.name,
                            stored_path=str(path))
        try:
            extraction = extract_document(doc)
        except Exception as e:  # never let one bad call sink the batch
            records.append({"variant": name, "error": f"{type(e).__name__}: {e}"})
            continue
        val = extraction.total_amount.value
        records.append({
            "variant": name,
            "confidence": float(extraction.total_amount.confidence),
            "extracted_total": val,
            "correct": _correct(val),
            "readable": extraction.quality.readable,
        })
    return records


def collect_cord_pairs() -> list[dict]:
    """Best-effort: fold in the existing CORD eval set if present. Returns
    per-item (confidence, correct) records (correct == total within tolerance)."""
    cord_dir = _BACKEND_DIR / "eval_datasets" / "cord"
    if not cord_dir.exists():
        return []
    try:
        from app.evalrunner.extraction_eval import score_cord
        result = score_cord(cord_dir)
    except Exception as e:
        print(f"  CORD: skipped ({type(e).__name__}: {e})")
        return []
    records: list[dict] = []
    for r in result.get("records", []):
        if "error" in r or r.get("total_confidence") is None:
            continue
        records.append({
            "variant": f"cord:{r.get('image', '?')}",
            "confidence": float(r["total_confidence"]),
            "extracted_total": r.get("extracted_total"),
            "correct": int(bool(r.get("total_match"))),
            "readable": r.get("readable", True),
        })
    return records


# --------------------------------------------------------------------------- #
# Reporting                                                                    #
# --------------------------------------------------------------------------- #

def _fmt(x, digits: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def build_report(records: list[dict], confidences: list[float], correct: list[int],
                 ece_before: float, ece_after: float,
                 curve: list[dict], sources: dict) -> str:
    n = len(confidences)
    n_correct = sum(correct)
    lines: list[str] = []
    lines.append("# Confidence Calibration Report")
    lines.append("")
    lines.append("Makes the extractor's confidence score statistically meaningful — so "
                 "that \"0.9 ≈ 90% correct\". We fit an **isotonic** calibrator on REAL "
                 "labelled outcomes from the production vision extractor, then measure "
                 "**Expected Calibration Error (ECE)** before and after.")
    lines.append("")
    lines.append("> **Wired OFF by default.** `settings.confidence_calibration_enabled = "
                 "False`, so `confidence.compute(...)` returns the raw composite exactly "
                 "as before and the 12 live cases' confidences / thresholds are unchanged. "
                 "Enabling it applies the map below to the final composite.")
    lines.append("")

    # Dataset ------------------------------------------------------------- #
    lines.append("## Dataset")
    lines.append("")
    lines.append(f"- **Total labelled (confidence, correct) pairs: {n}** "
                 f"({n_correct} correct, {n - n_correct} incorrect).")
    lines.append(f"- TC004 legibility-spectrum variants: **{sources['tc004']}** "
                 "(clean + rubber-stamp + phone-photo + multilingual + blur/low-contrast "
                 "ramps + combinations) on a bill whose ground-truth total is **1500**. "
                 "`correct = round(total_amount.value) == 1500`.")
    cord_n = sources["cord"]
    if cord_n:
        lines.append(f"- CORD v2 receipts (folded in, best-effort): **{cord_n}** "
                     "(`correct = total within 2% of labelled total`).")
    else:
        lines.append("- CORD v2 receipts: not folded in (dataset absent or unavailable).")
    if sources.get("errors"):
        lines.append(f"- Extraction errors (excluded): {sources['errors']}.")
    lines.append("")

    # ECE ----------------------------------------------------------------- #
    lines.append("## Calibration quality (ECE, 10 bins)")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---:|")
    lines.append(f"| ECE before calibration | **{_fmt(ece_before)}** |")
    lines.append(f"| ECE after isotonic fit | **{_fmt(ece_after)}** |")
    improvement = ece_before - ece_after
    lines.append(f"| absolute improvement | {_fmt(improvement)} |")
    lines.append("")
    lines.append("Lower ECE = better calibrated. ECE is the count-weighted average gap "
                 "between each confidence bin's mean confidence and its empirical "
                 "accuracy.")
    lines.append("")

    # Reliability table --------------------------------------------------- #
    lines.append("## Reliability table (pre-calibration)")
    lines.append("")
    if curve:
        lines.append("| confidence bin | mean confidence | accuracy | count |")
        lines.append("|---|---:|---:|---:|")
        for b in curve:
            lines.append(
                f"| [{b['bin_lower']:.1f}, {b['bin_upper']:.1f}] | "
                f"{b['mean_confidence']:.3f} | {b['accuracy']:.3f} | {b['count']} |")
    else:
        lines.append("_No bins (empty dataset)._")
    lines.append("")

    # Per-variant detail -------------------------------------------------- #
    lines.append("## Per-item outcomes")
    lines.append("")
    lines.append("| item | confidence | extracted total | correct |")
    lines.append("|---|---:|---:|:---:|")
    for r in records:
        if "error" in r:
            lines.append(f"| {r['variant']} | ERROR | {r['error']} | x |")
            continue
        et = r.get("extracted_total")
        lines.append(
            f"| {r['variant']} | {r['confidence']:.3f} | "
            f"{'n/a' if et is None else f'{et:.0f}'} | "
            f"{'Y' if r['correct'] else 'N'} |")
    lines.append("")

    # Honest limitations -------------------------------------------------- #
    lines.append("## Limitations (honest)")
    lines.append("")
    lines.append(f"- **Small sample (n = {n}).** This is a demonstration fit on a narrow, "
                 "synthetic legibility spectrum around a single known total, not a "
                 "production calibration. The isotonic map will overfit at this scale.")
    lines.append("- **Coarse labels.** `correct` is binary on the bill total only; it does "
                 "not capture partial-field correctness or other extracted fields.")
    lines.append("- **Production path:** calibrate on **logged real outcomes at volume** "
                 "(adjudicated claims where the final confidence can be checked against "
                 "whether the decision held), refit periodically, and monitor ECE drift. "
                 "Hold out a test split and report ECE on it (here ECE-after is in-sample).")
    lines.append("- The calibrator is committed at `backend/calibration_map.json` and stays "
                 "**inert unless explicitly enabled** in settings.")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #

def main() -> None:
    import tempfile

    print("Collecting REAL (confidence, correct) pairs from live extraction ...")
    with tempfile.TemporaryDirectory(prefix="calib_") as td:
        tc004 = collect_tc004_pairs(pathlib.Path(td))
    cord = collect_cord_pairs()
    records = tc004 + cord

    ok = [r for r in records if "error" not in r]
    errors = sum("error" in r for r in records)
    confidences = [r["confidence"] for r in ok]
    correct = [r["correct"] for r in ok]
    sources = {
        "tc004": sum(1 for r in tc004 if "error" not in r),
        "cord": len(cord),
        "errors": errors,
    }

    print(f"  collected {len(confidences)} pairs "
          f"(tc004={sources['tc004']}, cord={sources['cord']}, errors={errors})")

    ece_before = expected_calibration_error(confidences, correct, n_bins=10)
    curve = reliability_curve(confidences, correct, n_bins=10)
    calibrator = fit_isotonic(confidences, correct)
    recalibrated = [calibrator.apply(c) for c in confidences]
    ece_after = expected_calibration_error(recalibrated, correct, n_bins=10)

    save_calibrator(calibrator, CALIB_PATH)
    print(f"  ECE before = {ece_before:.4f}  ECE after = {ece_after:.4f}")
    print(f"  saved calibrator -> {CALIB_PATH}")

    report = build_report(records, confidences, correct, ece_before, ece_after,
                          curve, sources)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report)
    print(f"  wrote report -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
