"""Tests for the agentic self-correction loop in the extraction stage.

Deterministic tests (default, no Gemini) cover the PURE trigger logic
(`needs_correction`) and the merge logic (`merge_extractions`) — proving clean docs
never retry, weak load-bearing fields do, unreadable docs never loop, and the
higher-confidence field always wins on merge.

A live test (@pytest.mark.live, ~2 Gemini calls) feeds a phone-photo TC004 bill
through the real pipeline and asserts the total is still extracted correctly; if the
first pass came back low-confidence, it asserts the retry happened and the trace
entry appears.
"""
import pytest

from app.config import settings
from app.models.schemas import (ExtractionResult, DocumentQuality, StrField, NumField,
                                LineItem)
from app.agents.extraction import needs_correction, merge_extractions


# --------------------------------------------------------------------------- #
# Builders                                                                     #
# --------------------------------------------------------------------------- #

def _clean_bill(conf: float = 0.95) -> ExtractionResult:
    return ExtractionResult(
        file_id="F008", doc_type="HOSPITAL_BILL",
        quality=DocumentQuality(readable=True, overall_confidence=conf),
        patient_name=StrField(value="Arjun Mehta", confidence=conf),
        total_amount=NumField(value=1500.0, confidence=conf),
        line_items=[LineItem(description="Consultation", amount=1500.0)])


def _clean_prescription(conf: float = 0.95) -> ExtractionResult:
    return ExtractionResult(
        file_id="F001", doc_type="PRESCRIPTION",
        quality=DocumentQuality(readable=True, overall_confidence=conf),
        patient_name=StrField(value="Arjun Mehta", confidence=conf),
        diagnosis=StrField(value="Viral fever", confidence=conf))


# --------------------------------------------------------------------------- #
# Trigger logic (deterministic)                                               #
# --------------------------------------------------------------------------- #

def test_clean_bill_does_not_trigger():
    retry, weak = needs_correction(_clean_bill())
    assert retry is False and weak == []


def test_clean_prescription_does_not_trigger():
    retry, weak = needs_correction(_clean_prescription())
    assert retry is False and weak == []


def test_low_confidence_total_triggers_naming_field():
    bill = _clean_bill()
    bill.total_amount = NumField(value=1500.0, confidence=0.4)
    retry, weak = needs_correction(bill)
    assert retry is True
    assert "total_amount" in weak


def test_null_load_bearing_field_triggers():
    bill = _clean_bill()
    bill.patient_name = StrField(value=None, confidence=0.0)
    retry, weak = needs_correction(bill)
    assert retry is True and "patient_name" in weak


def test_empty_line_items_triggers():
    bill = _clean_bill()
    bill.line_items = []
    retry, weak = needs_correction(bill)
    assert retry is True and "line_items" in weak


def test_unreadable_doc_never_triggers():
    bill = _clean_bill()
    bill.quality = DocumentQuality(readable=False, quality_issues=["too blurry"],
                                   overall_confidence=0.1)
    bill.total_amount = NumField(value=None, confidence=0.0)
    retry, weak = needs_correction(bill)
    assert retry is False and weak == []


def test_threshold_boundary_just_above_does_not_trigger():
    bill = _clean_bill(conf=settings.extraction_lowconf_threshold + 0.01)
    assert needs_correction(bill)[0] is False


def test_prescription_low_diagnosis_triggers():
    rx = _clean_prescription()
    rx.diagnosis = StrField(value="?", confidence=0.3)
    retry, weak = needs_correction(rx)
    assert retry is True and "diagnosis" in weak


def test_lab_report_uses_patient_and_diagnosis():
    lab = ExtractionResult(
        file_id="L1", doc_type="LAB_REPORT",
        quality=DocumentQuality(readable=True),
        patient_name=StrField(value="A", confidence=0.95),
        diagnosis=StrField(value=None, confidence=0.0))
    retry, weak = needs_correction(lab)
    assert retry is True and "diagnosis" in weak


# --------------------------------------------------------------------------- #
# Merge logic (deterministic)                                                 #
# --------------------------------------------------------------------------- #

def test_merge_keeps_higher_confidence_field():
    first = _clean_bill()
    first.total_amount = NumField(value=1500.0, confidence=0.4)
    second = _clean_bill()
    second.total_amount = NumField(value=1500.0, confidence=0.92)
    merged, improved = merge_extractions(first, second)
    assert merged.total_amount.confidence == 0.92
    assert "total_amount" in improved


def test_merge_does_not_overwrite_when_first_is_better():
    first = _clean_bill(conf=0.95)
    second = _clean_bill(conf=0.50)
    merged, improved = merge_extractions(first, second)
    assert merged.patient_name.confidence == 0.95
    assert "patient_name" not in improved


def test_merge_picks_winner_per_field_independently():
    first = _clean_bill()
    first.total_amount = NumField(value=1500.0, confidence=0.3)   # second wins
    first.patient_name = StrField(value="Arjun Mehta", confidence=0.95)  # first wins
    second = _clean_bill()
    second.total_amount = NumField(value=1500.0, confidence=0.9)
    second.patient_name = StrField(value="Arjun", confidence=0.5)
    merged, improved = merge_extractions(first, second)
    assert merged.total_amount.confidence == 0.9
    assert merged.patient_name.value == "Arjun Mehta"
    assert improved == ["total_amount"]


def test_merge_line_items_filled_when_first_empty():
    first = _clean_bill()
    first.line_items = []
    second = _clean_bill()  # has one line item
    merged, improved = merge_extractions(first, second)
    assert len(merged.line_items) == 1 and "line_items" in improved


# --------------------------------------------------------------------------- #
# Live test (real Gemini) — kept to ~2 calls                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.live
def test_phone_photo_bill_self_corrects_or_extracts_clean(tmp_path):
    from app.models.schemas import DocumentInput
    from app.fixtures.loader import load_cases
    from app.fixtures.messy import phone_photo, render_tc004_bill
    from app.agents.extraction import extract_document_with_correction
    from tests.conftest import REPO_ROOT

    cases = {c["case_id"]: c for c in load_cases(str(REPO_ROOT / "test_cases.json"))}
    messy = phone_photo(render_tc004_bill(cases["TC004"]))
    path = str(tmp_path / "phone_bill.png")
    messy.save(path)

    result, info = extract_document_with_correction(
        DocumentInput(file_id="F008", stored_path=path))

    # The total must be read correctly regardless of whether a retry happened.
    assert result.total_amount.value == 1500, f"got {result.total_amount.value}"

    if info["corrected"]:
        # If the first pass was low-confidence, the escalation must be recorded.
        assert info["escalated_model"] == settings.gemini_pro_model
        assert info["weak_fields"], "corrected but no weak fields recorded"
        print("SELF-CORRECTION fired. weak=", info["weak_fields"],
              "improved=", info["improved_fields"])
    else:
        # First pass was clean enough — guarantee coverage of the trigger directly
        # by synthesising a low-confidence result and asserting it WOULD retry.
        synthetic = result.model_copy(deep=True)
        synthetic.total_amount = NumField(value=1500.0, confidence=0.3)
        retry, weak = needs_correction(synthetic)
        assert retry and "total_amount" in weak
        print("phone-photo extracted clean on first pass; trigger verified synthetically")
