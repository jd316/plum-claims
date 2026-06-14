"""Deterministic patient-identity consistency. The LLM extracts names; THIS decides."""
from rapidfuzz.distance import JaroWinkler
from app.models.schemas import ExtractionResult, DocumentProblem

MATCH_THRESHOLD = 0.85      # Jaro-Winkler similarity
MIN_NAME_CONFIDENCE = 0.5   # ignore names the extractor itself doubts

def names_match(a: str, b: str) -> bool:
    return JaroWinkler.normalized_similarity(a.strip().lower(), b.strip().lower()) >= MATCH_THRESHOLD

def check_patient_consistency(docs: list[ExtractionResult], member_name: str) -> list[DocumentProblem]:
    named = [(d.file_id, d.patient_name.value) for d in docs
             if d.patient_name.value and d.patient_name.confidence >= MIN_NAME_CONFIDENCE]
    problems: list[DocumentProblem] = []
    for fid, name in named:
        if not names_match(name, member_name):
            others = [(f, n) for f, n in named if f != fid and names_match(n, member_name)]
            ref = f" while other documents are for '{others[0][1]}'" if others else ""
            problems.append(DocumentProblem(
                kind="PATIENT_MISMATCH", file_id=fid,
                message=(f"Document {fid} is for patient '{name}'{ref}, but this claim was submitted "
                         f"for member '{member_name}'. All documents must belong to the same patient. "
                         f"Please upload documents for '{member_name}'.")))
            break  # one clear, specific message
    return problems
