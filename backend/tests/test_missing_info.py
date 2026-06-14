"""Deterministic tests for the narrowly-scoped missing-info follow-up
(check_missing_critical_fields). Mirrors the docgate test fixture patterns."""
from app.rules.docgate import check_documents, check_missing_critical_fields
from app.models.schemas import (
    ExtractionResult, StrField, NumField, LineItem, DocumentQuality,
)
from app.services.policy_engine import PolicyEngine
from tests.conftest import REPO_ROOT

pe = PolicyEngine(str(REPO_ROOT / "policy_terms.json"))


def _bill(fid="B1", dtype="HOSPITAL_BILL", name="Rajesh Kumar",
          total=None, line_items=None, readable=True):
    return ExtractionResult(
        file_id=fid, doc_type=dtype,
        quality=DocumentQuality(readable=readable,
                                quality_issues=[] if readable else ["blurry"]),
        patient_name=StrField(value=name, confidence=.95 if name else 0),
        total_amount=NumField(value=total, confidence=.9 if total is not None else 0),
        line_items=line_items or [])


def _rx(fid="P1", name="Rajesh Kumar", diagnosis=None, readable=True):
    return ExtractionResult(
        file_id=fid, doc_type="PRESCRIPTION",
        quality=DocumentQuality(readable=readable,
                                quality_issues=[] if readable else ["blurry"]),
        patient_name=StrField(value=name, confidence=.95 if name else 0),
        diagnosis=StrField(value=diagnosis, confidence=.9 if diagnosis else 0))


# --- bill total -------------------------------------------------------------

def test_bill_total_missing_no_line_items_asks_for_total():
    probs = check_missing_critical_fields([_bill(total=None, line_items=[])],
                                          "PHARMACY")
    assert len(probs) == 1
    assert probs[0].kind == "NEEDS_MEMBER_INPUT"
    assert probs[0].file_id == "B1"
    assert "total" in probs[0].message.lower()


def test_bill_total_missing_but_line_items_present_no_problem():
    # We can sum line items, so the amount is determinable → no follow-up.
    probs = check_missing_critical_fields(
        [_bill(total=None, line_items=[LineItem(description="Room", amount=4800)])],
        "PHARMACY")
    assert probs == []


def test_bill_with_total_present_no_problem():
    probs = check_missing_critical_fields([_bill(total=4800)], "PHARMACY")
    assert probs == []


# --- prescription diagnosis -------------------------------------------------

def test_prescription_diagnosis_missing_asks_for_condition():
    probs = check_missing_critical_fields([_rx(diagnosis=None)], "CONSULTATION")
    assert len(probs) == 1
    assert probs[0].kind == "NEEDS_MEMBER_INPUT"
    assert probs[0].file_id == "P1"
    assert "condition" in probs[0].message.lower()


def test_prescription_with_diagnosis_no_problem():
    probs = check_missing_critical_fields([_rx(diagnosis="Hypertension")],
                                          "CONSULTATION")
    assert probs == []


# --- TC009-shape guard (bill with no patient name but a total) --------------

def test_bill_without_patient_name_but_with_total_no_problem():
    # Guards the TC009 shape: {total: 4800} with no printed name must NOT be blocked.
    probs = check_missing_critical_fields([_bill(name=None, total=4800)], "PHARMACY")
    assert probs == []


# --- end-to-end through check_documents (early-exit blocked) -----------------

def test_check_documents_blocks_on_missing_bill_total():
    probs = check_documents(
        [_rx(name="Rajesh Kumar", diagnosis="Hypertension"),
         _bill(name="Rajesh Kumar", total=None, line_items=[])],
        "CONSULTATION", "Rajesh Kumar", pe)
    assert len(probs) == 1
    assert probs[0].kind == "NEEDS_MEMBER_INPUT"
    assert "total" in probs[0].message.lower()


def test_check_documents_clean_consultation_with_facts_passes():
    probs = check_documents(
        [_rx(name="Rajesh Kumar", diagnosis="Hypertension"),
         _bill(name="Rajesh Kumar", total=4800)],
        "CONSULTATION", "Rajesh Kumar", pe)
    assert probs == []
