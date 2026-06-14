from app.rules.docgate import check_documents
from app.models.schemas import ExtractionResult, StrField, NumField, DocumentQuality
from app.services.policy_engine import PolicyEngine
from tests.conftest import REPO_ROOT

pe = PolicyEngine(str(REPO_ROOT / "policy_terms.json"))

def _doc(fid, dtype, name=None, readable=True):
    # Clean docs carry their decision-critical facts (prescriptions a diagnosis,
    # bills a total) so the missing-info follow-up does not trigger on them.
    is_bill = dtype in ("HOSPITAL_BILL", "PHARMACY_BILL")
    return ExtractionResult(file_id=fid, doc_type=dtype,
        quality=DocumentQuality(readable=readable, quality_issues=[] if readable else ["blurry"]),
        patient_name=StrField(value=name, confidence=.95 if name else 0),
        diagnosis=StrField(value="Hypertension" if dtype == "PRESCRIPTION" else None,
                           confidence=.9 if dtype == "PRESCRIPTION" else 0),
        total_amount=NumField(value=4800 if is_bill else None, confidence=.9 if is_bill else 0))

def test_tc001_two_prescriptions_blocked_with_specific_message():
    probs = check_documents([_doc("F001","PRESCRIPTION","Rajesh Kumar"),
                             _doc("F002","PRESCRIPTION","Rajesh Kumar")], "CONSULTATION", "Rajesh Kumar", pe)
    assert len(probs) == 1 and probs[0].kind == "MISSING_DOCUMENT"
    m = probs[0].message
    assert "PRESCRIPTION" in m and "HOSPITAL_BILL" in m

def test_tc002_unreadable_asks_reupload_of_specific_doc():
    probs = check_documents([_doc("F003","PRESCRIPTION","Sneha Reddy"),
                             _doc("F004","PHARMACY_BILL", readable=False)], "PHARMACY", "Sneha Reddy", pe)
    assert probs and probs[0].kind == "UNREADABLE_DOCUMENT" and probs[0].file_id == "F004"
    assert "re-upload" in probs[0].message.lower()

def test_tc003_patient_mismatch_blocked_with_names():
    probs = check_documents([_doc("F005","PRESCRIPTION","Rajesh Kumar"),
                             _doc("F006","HOSPITAL_BILL","Arjun Mehta")], "CONSULTATION", "Rajesh Kumar", pe)
    assert probs and probs[0].kind == "PATIENT_MISMATCH"
    assert "Arjun Mehta" in probs[0].message and "Rajesh Kumar" in probs[0].message

def test_clean_consultation_passes():
    assert check_documents([_doc("F1","PRESCRIPTION","Rajesh Kumar"),
                            _doc("F2","HOSPITAL_BILL","Rajesh Kumar")], "CONSULTATION", "Rajesh Kumar", pe) == []

def test_wrong_document_out_of_category_is_named_specifically():
    # DENTAL_REPORT is neither required nor optional for a CONSULTATION claim, and the
    # required docs are missing → this is a genuine wrong-document upload, not just an
    # incomplete set. The message must name the offending type AND what is needed instead.
    probs = check_documents([_doc("F1","DENTAL_REPORT","Rajesh Kumar")],
                            "CONSULTATION", "Rajesh Kumar", pe)
    assert len(probs) == 1 and probs[0].kind == "WRONG_DOCUMENT"
    m = probs[0].message
    assert "DENTAL_REPORT" in m and "PRESCRIPTION" in m and "HOSPITAL_BILL" in m

def test_wrong_document_alongside_a_correct_one():
    # PHARMACY needs PRESCRIPTION + PHARMACY_BILL. The member uploaded a PRESCRIPTION (correct)
    # plus a LAB_REPORT (out-of-category) instead of the bill → WRONG_DOCUMENT names LAB_REPORT
    # and the still-missing PHARMACY_BILL.
    probs = check_documents([_doc("F1","PRESCRIPTION","Sneha Reddy"),
                             _doc("F2","LAB_REPORT","Sneha Reddy")], "PHARMACY", "Sneha Reddy", pe)
    assert len(probs) == 1 and probs[0].kind == "WRONG_DOCUMENT"
    assert "LAB_REPORT" in probs[0].message and "PHARMACY_BILL" in probs[0].message

def test_incomplete_but_correct_types_stay_missing_not_wrong():
    # TC001 shape: only required-typed docs uploaded, just not all of them. This is a
    # MISSING_DOCUMENT (incomplete), NOT a WRONG_DOCUMENT (nothing out-of-category).
    probs = check_documents([_doc("F1","PRESCRIPTION","Rajesh Kumar")],
                            "CONSULTATION", "Rajesh Kumar", pe)
    assert len(probs) == 1 and probs[0].kind == "MISSING_DOCUMENT"

def test_extra_out_of_category_doc_does_not_block_a_complete_claim():
    # All required present + a stray out-of-category doc → do NOT block a processable claim.
    assert check_documents([_doc("F1","PRESCRIPTION","Sneha Reddy"),
                            _doc("F2","PHARMACY_BILL","Sneha Reddy"),
                            _doc("F3","DENTAL_REPORT","Sneha Reddy")],
                           "PHARMACY", "Sneha Reddy", pe) == []

def test_diagnostic_report_satisfies_lab_report_requirement():
    # TC007 robustness: an MRI/imaging report is legitimately classifiable as DIAGNOSTIC_REPORT
    # OR LAB_REPORT by the vision model. For a DIAGNOSTIC claim, a DIAGNOSTIC_REPORT must satisfy
    # the LAB_REPORT requirement and NOT block — an ambiguous label-flip can't fail a complete set.
    probs = check_documents([_doc("F1","PRESCRIPTION","Suresh Patil"),
                             _doc("F2","DIAGNOSTIC_REPORT","Suresh Patil"),
                             _doc("F3","HOSPITAL_BILL","Suresh Patil")],
                            "DIAGNOSTIC", "Suresh Patil", pe)
    assert probs == []

def test_optional_doc_is_not_treated_as_wrong():
    # LAB_REPORT is OPTIONAL for CONSULTATION → never a wrong document even if required missing.
    probs = check_documents([_doc("F1","PRESCRIPTION","Rajesh Kumar"),
                             _doc("F2","LAB_REPORT","Rajesh Kumar")],
                            "CONSULTATION", "Rajesh Kumar", pe)
    assert len(probs) == 1 and probs[0].kind == "MISSING_DOCUMENT"  # only HOSPITAL_BILL missing
    assert "LAB_REPORT" not in probs[0].message.split("You uploaded")[0]
