import json
from app.models.schemas import ClaimSubmission, DocumentInput, ClaimHistoryItem

def load_cases(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)["test_cases"]

def case_to_submission(case: dict, rendered: dict[str, str]) -> ClaimSubmission:
    inp = case["input"]
    return ClaimSubmission(
        member_id=inp["member_id"], policy_id=inp["policy_id"],
        claim_category=inp["claim_category"], treatment_date=inp["treatment_date"],
        claimed_amount=inp["claimed_amount"], hospital_name=inp.get("hospital_name"),
        ytd_claims_amount=inp.get("ytd_claims_amount"),
        claims_history=[ClaimHistoryItem(**h) for h in inp.get("claims_history", [])],
        simulate_component_failure=inp.get("simulate_component_failure", False),
        documents=[DocumentInput(file_id=d["file_id"], file_name=d.get("file_name"),
                                 stored_path=rendered[d["file_id"]])
                   for d in inp["documents"]])
