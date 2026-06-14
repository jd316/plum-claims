"""Standardized fraud-signal vocabulary.

The vision extractor reports document anomalies as free text (e.g. "amount looks
crossed out", "both ORIGINAL and DUPLICATE stamps"). sample_documents_guide.md asks
specifically for a `DOCUMENT_ALTERATION` flag, and operators need to *query* by issue
type. Rather than depend on the model emitting exact tokens, this module deterministically
classifies each free-text signal into a fixed code and assigns a severity used to weight
the fraud verdict's certainty. The classifier is idempotent: a signal that already names a
known code keeps it.
"""
DOCUMENT_ALTERATION = "DOCUMENT_ALTERATION"
STAMP_ANOMALY = "STAMP_ANOMALY"
FONT_MISMATCH = "FONT_MISMATCH"
TOTAL_MISMATCH = "TOTAL_MISMATCH"
VISION_ANOMALY = "VISION_ANOMALY"  # generic fallback

CODES = (DOCUMENT_ALTERATION, STAMP_ANOMALY, FONT_MISMATCH, TOTAL_MISMATCH, VISION_ANOMALY)

# How strongly each code pushes the fraud verdict's certainty (0-1).
SEVERITY = {DOCUMENT_ALTERATION: 0.9, TOTAL_MISMATCH: 0.85, STAMP_ANOMALY: 0.75,
            FONT_MISMATCH: 0.6, VISION_ANOMALY: 0.5}

# Keyword groups checked in priority order (alteration first — it's the most serious).
_KEYWORDS = [
    (DOCUMENT_ALTERATION, ("alter", "crossed", "cross out", "overwrit", "whiten", "white-out",
                           "white out", "correction", "rewritten", "tamper", "erased", "scratch")),
    (TOTAL_MISMATCH, ("sum to", "does not match", "doesn't match", "total reads", "not summing",
                      "mismatched total")),
    (STAMP_ANOMALY, ("duplicate", "conflicting stamp", "multiple stamp", "two stamp", "stamp")),
    (FONT_MISMATCH, ("font",)),
]


def classify(text: str) -> str:
    """Map a free-text fraud signal to a standardized code."""
    t = (text or "").lower()
    for code in CODES[:-1]:
        if code.lower() in t:  # honor an explicit code already present
            return code
    for code, kws in _KEYWORDS:
        if any(k in t for k in kws):
            return code
    return VISION_ANOMALY


def tag(text: str) -> str:
    """Prefix a signal with its standardized code, e.g. '[DOCUMENT_ALTERATION] …'."""
    return f"[{classify(text)}] {text}"


def max_severity(signals: list[str]) -> float:
    """Highest severity across already-tagged/free-text signals (0.0 if none)."""
    return max((SEVERITY.get(classify(s), 0.5) for s in signals), default=0.0)
