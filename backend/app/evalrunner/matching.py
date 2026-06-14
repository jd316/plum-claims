import re
from app.models.schemas import ClaimResult

def match_case(case: dict, result: ClaimResult) -> tuple[bool, list[str]]:
    exp = case["expected"]; notes: list[str] = []
    ok = True
    if exp.get("decision") is None:
        if not result.blocked:
            ok = False; notes.append(f"expected early stop, got decision {result.decision and result.decision.status}")
    else:
        if not result.decision or result.decision.status != exp["decision"]:
            ok = False; notes.append(f"expected {exp['decision']}, got "
                                     f"{result.decision.status if result.decision else 'BLOCKED'}")
        if "approved_amount" in exp and result.decision and \
                abs(result.decision.approved_amount - exp["approved_amount"]) > 1:
            ok = False; notes.append(f"expected amount {exp['approved_amount']}, got {result.decision.approved_amount}")
        for r in exp.get("rejection_reasons", []):
            if result.decision and r not in [c.code for c in result.decision.reason_codes]:
                ok = False; notes.append(f"expected reason {r} missing")
        cs = exp.get("confidence_score")
        if cs and result.decision:
            m = re.search(r"([\d.]+)", cs)
            if m and result.decision.confidence <= float(m.group(1)):
                ok = False; notes.append(f"confidence {result.decision.confidence} not above {m.group(1)}")
    return ok, notes
