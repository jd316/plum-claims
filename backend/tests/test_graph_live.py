import pytest
from app.fixtures.loader import load_cases, case_to_submission
from app.fixtures.renderer import render_case_documents
from app.graph.build import run_claim
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.live
CASES = {c["case_id"]: c for c in load_cases(str(REPO_ROOT / "test_cases.json"))}

def _run(case_id, tmp_path):
    case = CASES[case_id]
    paths = render_case_documents(case, str(tmp_path / case_id))
    return run_claim(case_to_submission(case, paths))

def test_tc001_blocked_wrong_documents(tmp_path):
    s = _run("TC001", tmp_path)
    assert s.get("problems") and s.get("decision") is None
    m = s["problems"][0].message
    assert "PRESCRIPTION" in m and "HOSPITAL_BILL" in m

def test_tc003_blocked_patient_mismatch(tmp_path):
    s = _run("TC003", tmp_path)
    assert s.get("problems")
    assert "Arjun Mehta" in s["problems"][0].message

def test_tc004_clean_approval(tmp_path):
    s = _run("TC004", tmp_path)
    d = s["decision"]
    assert d.status == "APPROVED" and d.approved_amount == 1350 and d.confidence > 0.85
    assert any(t.agent == "decision_verifier" for t in s["trace"])
    # Sub-feature A: LLM steps now record token usage on their trace entries.
    llm_entries = [t for t in s["trace"] if t.model]
    assert any(t.input_tokens is not None and t.input_tokens > 0 for t in llm_entries), \
        "expected at least one LLM trace entry with non-None input_tokens"

def test_tc005_waiting_period(tmp_path):
    d = _run("TC005", tmp_path)["decision"]
    assert d.status == "REJECTED"
    assert any(r.code == "WAITING_PERIOD" for r in d.reason_codes)
    assert "2024-11-30" in d.member_message

def test_tc010_network_discount_order(tmp_path):
    s = _run("TC010", tmp_path)
    d = s["decision"]
    assert d.status == "APPROVED" and d.approved_amount == 3240
    assert d.financial.network_discount_amount == 900 and d.financial.copay_amount == 360

def test_tc011_degradation(tmp_path):
    s = _run("TC011", tmp_path)
    d = s["decision"]
    assert d.status == "APPROVED"
    assert s["failures"] and any(t.degraded for t in s["trace"])
    assert d.confidence < 0.80
    assert any("manual review" in r.lower() for r in d.recommendations)
