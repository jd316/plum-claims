import pytest
from app.fixtures.loader import load_cases
from app.fixtures.renderer import render_case_documents
from app.models.schemas import DocumentInput
from app.agents.extraction import extract_document
from tests.conftest import REPO_ROOT

CASES = {c["case_id"]: c for c in load_cases(str(REPO_ROOT / "test_cases.json"))}
pytestmark = pytest.mark.live

def _extract(case_id, file_id, tmp_path):
    case = CASES[case_id]
    paths = render_case_documents(case, str(tmp_path / case_id))
    return extract_document(DocumentInput(file_id=file_id, stored_path=paths[file_id]))

def test_classifies_prescription_and_reads_fields(tmp_path):
    r = _extract("TC004", "F007", tmp_path)
    assert r.doc_type == "PRESCRIPTION" and r.quality.readable
    assert r.patient_name.value and "rajesh" in r.patient_name.value.lower()
    assert r.diagnosis.value and "fever" in r.diagnosis.value.lower()
    assert r.patient_name.confidence > 0.5 and r.patient_name.source_text

def test_classifies_bill_with_line_items_and_total(tmp_path):
    r = _extract("TC004", "F008", tmp_path)
    assert r.doc_type == "HOSPITAL_BILL"
    assert r.total_amount.value == 1500
    assert len(r.line_items) == 3

def test_unreadable_document_flagged_not_hallucinated(tmp_path):
    r = _extract("TC002", "F004", tmp_path)
    assert r.quality.readable is False
    assert r.quality.quality_issues
