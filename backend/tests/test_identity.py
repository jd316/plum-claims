from app.services.identity import names_match, check_patient_consistency
from app.models.schemas import ExtractionResult, StrField

def _doc(fid, name, conf=0.95):
    return ExtractionResult(file_id=fid, patient_name=StrField(value=name, confidence=conf))

def test_exact_and_fuzzy_match():
    assert names_match("Rajesh Kumar", "Rajesh Kumar")
    assert names_match("Rajesh Kumar", "RAJESH KUMAR")
    assert names_match("Rajesh Kumar", "Rajesh Kumarr")   # OCR slip tolerated

def test_clear_mismatch():
    assert not names_match("Rajesh Kumar", "Arjun Mehta")

def test_consistency_flags_mismatch_with_names():
    issues = check_patient_consistency([_doc("F1","Rajesh Kumar"), _doc("F2","Arjun Mehta")], "Rajesh Kumar")
    assert len(issues) == 1
    assert "Rajesh Kumar" in issues[0].message and "Arjun Mehta" in issues[0].message

def test_missing_names_do_not_block():
    docs = [_doc("F1","Rajesh Kumar"), ExtractionResult(file_id="F2")]
    assert check_patient_consistency(docs, "Rajesh Kumar") == []

def test_low_confidence_names_do_not_block():
    docs = [_doc("F1","Rajesh Kumar"), _doc("F2","Arjun Mehta", conf=0.2)]
    assert check_patient_consistency(docs, "Rajesh Kumar") == []
