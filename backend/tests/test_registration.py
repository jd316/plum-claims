"""Deterministic doctor-registration validation (no mocks — pure functions + real models)."""

import pytest

from app.agents.extraction import _annotate_registration
from app.models.schemas import ExtractionResult, StrField
from app.services.registration import (
    is_valid_registration,
    MALFORMED_CONFIDENCE_CAP,
)


# Every doctor_registration that appears in the 12 official test cases — these MUST validate
# so the deterministic check never penalises a legitimate claim.
@pytest.mark.parametrize("reg", [
    "KA/45678/2015",   # TC004
    "GJ/56789/2014",   # TC005
    "AP/67890/2017",   # TC007
    "DL/34567/2016",   # TC008
    "TN/56789/2013",   # TC010
    "WB/34567/2015",   # TC012
    "AYUR/KL/2345/2019",  # TC011 (Ayurveda national format)
    "MH/23456/2018",   # guide example
    "kl/78901/2012",   # case-insensitive
])
def test_valid_registrations(reg):
    assert is_valid_registration(reg) is True


@pytest.mark.parametrize("reg", [
    "",                # empty
    None,              # absent
    "45678/2015",      # missing state code
    "KA-45678-2015",   # wrong separators
    "KA/45678",        # missing year
    "KARNATAKA/45678/2015",  # state not two-letter
    "KA/45678/15",     # two-digit year
    "Dr. Arun Sharma",     # not a registration at all
    "AYUR/45678/2019",     # Ayurveda missing the state token
    "KA/abc/2015",     # non-numeric serial
])
def test_invalid_registrations(reg):
    assert is_valid_registration(reg) is False


def _result_with_reg(value, confidence=0.95):
    r = ExtractionResult(file_id="F1", doc_type="PRESCRIPTION")
    r.doctor_registration = StrField(value=value, confidence=confidence, source_text=value)
    return r


def test_annotate_caps_confidence_and_flags_malformed():
    r = _result_with_reg("KA-45678-2015", confidence=0.95)
    _annotate_registration(r)
    assert r.doctor_registration.confidence <= MALFORMED_CONFIDENCE_CAP
    assert any("does not match a valid Indian" in q for q in r.quality.quality_issues)


def test_annotate_leaves_valid_registration_untouched():
    r = _result_with_reg("KA/45678/2015", confidence=0.95)
    _annotate_registration(r)
    assert r.doctor_registration.confidence == 0.95
    assert r.quality.quality_issues == []


def test_annotate_ignores_absent_registration():
    r = _result_with_reg(None, confidence=0.0)
    _annotate_registration(r)
    assert r.quality.quality_issues == []


def test_annotate_is_idempotent():
    r = _result_with_reg("BADREG", confidence=0.9)
    _annotate_registration(r)
    _annotate_registration(r)
    # Only one quality_issue appended despite two calls.
    matching = [q for q in r.quality.quality_issues if "does not match a valid Indian" in q]
    assert len(matching) == 1
