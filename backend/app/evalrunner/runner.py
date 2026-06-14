"""Runs all 12 cases through the REAL pipeline (live vision) and builds the eval report."""
import os, uuid
from app.config import settings
from app.fixtures.loader import load_cases, case_to_submission
from app.fixtures.renderer import render_case_documents
from app.graph.build import run_claim
from app.models.schemas import ClaimResult
from app.evalrunner.matching import match_case
from app.services.cost import estimate_cost_inr

def state_to_result(state: dict, claim_id: str) -> ClaimResult:
    tr = list(state.get("trace", []))
    for i, t in enumerate(tr): t.seq = i + 1
    # Sub-feature A: aggregate per-claim token usage, latency and estimated ₹ cost.
    total_in = sum(t.input_tokens or 0 for t in tr)
    total_out = sum(t.output_tokens or 0 for t in tr)
    total_latency = sum(t.duration_ms or 0 for t in tr)
    est_cost = round(sum(estimate_cost_inr(t.model, t.input_tokens, t.output_tokens)
                         for t in tr if t.model), 4)
    return ClaimResult(claim_id=claim_id, blocked=bool(state.get("problems")),
                       problems=state.get("problems", []), decision=state.get("decision"),
                       trace=tr, failures=state.get("failures", []),
                       total_input_tokens=total_in, total_output_tokens=total_out,
                       total_latency_ms=total_latency, estimated_cost_inr=est_cost,
                       # Sub-feature B: persist the facts the rules decided on (for replay).
                       extractions=state.get("extractions", []),
                       semantic=state.get("semantic"), member=state.get("member"))

def run_all(out_dir: str | None = None) -> list[dict]:
    out_dir = out_dir or os.path.join(settings.storage_dir, "eval")
    results = []
    for case in load_cases(settings.test_cases_path):
        # Defensive: one failing case must not abort the whole (expensive) run.
        try:
            paths = render_case_documents(case, os.path.join(out_dir, case["case_id"]))
            state = run_claim(case_to_submission(case, paths))
            result = state_to_result(state, f"EVAL-{case['case_id']}-{uuid.uuid4().hex[:6]}")
            ok, notes = match_case(case, result)
            results.append({"case_id": case["case_id"], "case_name": case["case_name"],
                            "matched": ok, "notes": notes, "result": result.model_dump(mode="json")})
        except Exception as e:
            results.append({"case_id": case["case_id"], "case_name": case["case_name"],
                            "matched": False, "notes": [f"case errored: {e}"], "result": None})
    return results

def to_markdown(results: list[dict]) -> str:
    lines = ["# Eval Report — 12 Test Cases (live pipeline)\n",
             f"**Matched: {sum(r['matched'] for r in results)}/12**\n"]
    for r in results:
        res = r["result"]
        lines.append(f"\n## {r['case_id']} — {r['case_name']} — {'✅ MATCH' if r['matched'] else '❌ MISMATCH'}")
        if r["notes"]: lines.append(f"Mismatch notes: {'; '.join(r['notes'])}")
        if res is None:
            continue  # case errored before producing a result; notes carry the reason
        d = res.get("decision")
        if res["blocked"]:
            lines.append(f"**Outcome:** BLOCKED — {res['problems'][0]['message']}")
        elif d:
            lines.append(f"**Decision:** {d['status']} · approved ₹{d['approved_amount']} · "
                         f"confidence {d['confidence']}\n**Message:** {d['member_message']}")
        lines.append("\n<details><summary>Full trace</summary>\n")
        for t in res["trace"]:
            lines.append(f"- `[{t['seq']:02d}] {t['step']}/{t['agent']}` **{t['status']}**"
                         f"{' ⚠ degraded' if t['degraded'] else ''} — {t['summary']}")
        lines.append("\n</details>")
    return "\n".join(lines)
