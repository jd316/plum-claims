"""Composite confidence: C_raw = .30·extraction + .30·rules + .20·completeness + .20·verifier;
C_final = C_raw · (1 − penalty)^failures. Components stored so the score is explainable.

OPTIONAL calibration (OFF by default): when settings.confidence_calibration_enabled
is True and a fitted calibrator file loads, the final composite is mapped through it
so the score is statistically meaningful ("0.9 ≈ 90% correct"). Default OFF -> the
raw composite is returned EXACTLY as before, so the 12 cases' confidences and the
existing thresholds are byte-identical. The weights and degradation penalty are
never changed by calibration."""
from dataclasses import dataclass
from functools import lru_cache
from app.config import settings
from app.models.schemas import ConfidenceComponents
from app.services.calibration import load_calibrator

W_EXTRACTION, W_RULES, W_COMPLETENESS, W_VERIFIER = 0.30, 0.30, 0.20, 0.20

@dataclass
class ConfidenceScore:
    final: float
    components: ConfidenceComponents


@lru_cache(maxsize=4)
def _calibrator(path: str):
    """Cached load of the fitted calibrator (None if missing/invalid)."""
    return load_calibrator(path)


def _maybe_calibrate(final: float) -> float:
    """Apply the fitted calibrator to `final` IFF calibration is enabled and a
    calibrator loads; otherwise return `final` unchanged. Never raises."""
    if not settings.confidence_calibration_enabled:
        return final
    cal = _calibrator(settings.calibration_map_path)
    if cal is None:
        return final
    return round(cal.apply(final), 3)


def compute(extraction_quality: float, rule_certainty: float, completeness: float,
            verifier_agreement: float, failures: int) -> ConfidenceScore:
    raw = (W_EXTRACTION * extraction_quality + W_RULES * rule_certainty
           + W_COMPLETENESS * completeness + W_VERIFIER * verifier_agreement)
    penalty = 1 - (1 - settings.degradation_penalty) ** failures if failures else 0.0
    final = round(raw * (1 - penalty), 3)
    final = _maybe_calibrate(final)
    return ConfidenceScore(final=final, components=ConfidenceComponents(
        extraction_quality=round(extraction_quality, 3), rule_certainty=round(rule_certainty, 3),
        completeness=round(completeness, 3), verifier_agreement=round(verifier_agreement, 3),
        degradation_penalty=round(penalty, 3)))
