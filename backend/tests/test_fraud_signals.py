"""Standardized fraud-signal vocabulary (app.services.fraud_signals)."""
from app.services import fraud_signals as fs


def test_classify_alteration_variants():
    for t in ["amount looks crossed out", "the total was overwritten",
              "figure appears whitened / white-out", "value tampered with"]:
        assert fs.classify(t) == fs.DOCUMENT_ALTERATION


def test_classify_stamp_and_font():
    assert fs.classify("both ORIGINAL and DUPLICATE stamps present") == fs.STAMP_ANOMALY
    assert fs.classify("mismatched fonts in the bill") == fs.FONT_MISMATCH


def test_classify_total_mismatch_and_fallback():
    assert fs.classify("line items sum to 1000 but total reads 5000") == fs.TOTAL_MISMATCH
    assert fs.classify("something odd but unspecified") == fs.VISION_ANOMALY


def test_tag_prefixes_code_and_is_idempotent():
    tagged = fs.tag("amount crossed out")
    assert tagged.startswith("[DOCUMENT_ALTERATION]")
    # Re-classifying a tagged string keeps the same code (idempotent).
    assert fs.classify(tagged) == fs.DOCUMENT_ALTERATION


def test_max_severity_alteration_dominates():
    assert fs.max_severity(["[FONT_MISMATCH] x", "[DOCUMENT_ALTERATION] y"]) == 0.9
    assert fs.max_severity([]) == 0.0
