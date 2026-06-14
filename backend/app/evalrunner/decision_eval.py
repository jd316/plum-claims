"""Fast decision-layer eval harness.

Runs synthetic labeled cases through the REAL deterministic decision logic — the five
rule checks + financial calculator + aggregator — EXACTLY as the pipeline's decide
stage composes them (mirrored from `app.graph.nodes`), with NO Gemini, so the whole
suite runs in seconds. It then compares the produced decision to each case's
independently-derived expected outcome and reports real metrics.

PURE-ADDITIVE: it imports the production rules unchanged and does not touch the
pipeline or the 12 cases.

`decide_from_facts` is the single source of truth for "given these facts, what would
the pipeline decide?" — it replicates `financial_calc` + `decide` from nodes.py:
  * disallowed line items come from the coverage_exclusion verdict,
  * the line-item fallback (bill total / claimed amount) mirrors nodes.financial_calc,
  * network detection uses the submission hospital then the extracted hospital,
  * the aggregator's `auto_manual_review_above` comes from the policy.
"""
from __future__ import annotations

import pathlib
from collections import defaultdict

from app.models.schemas import Decision, FinancialBreakdown, LineItem, SemanticMapping
from app.services.policy_engine import PolicyEngine
from app.rules import waiting_period, coverage_exclusion, pre_auth, limits, fraud
from app.rules.base import RuleContext
from app.rules.financial import calculate
from app.rules.aggregator import aggregate
from app.evalrunner.synthetic import SyntheticCase, generate_cases

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2].parent
REPORT_PATH = _REPO_ROOT / "docs" / "decision_eval_report.md"

STATUSES = ["APPROVED", "PARTIAL", "REJECTED", "MANUAL_REVIEW"]

# Same rule set + order the pipeline runs (nodes.RULES).
_RULES = [waiting_period, coverage_exclusion, pre_auth, limits, fraud]


def decide_from_facts(case: SyntheticCase, pe: PolicyEngine) -> Decision:
    """Build a RuleContext, run the 5 rule checks + financial + aggregator EXACTLY as
    the pipeline's decide stage (nodes.financial_calc + nodes.decide) does, and return
    the Decision. No Gemini."""
    s = case.submission
    ctx = RuleContext(s, _member(pe, s.member_id), case.extractions,
                      case.semantic or SemanticMapping(confidence=0.3), pe)
    verdicts = [rule.check(ctx) for rule in _RULES]

    # --- mirror nodes.financial_calc -------------------------------------- #
    disallowed = [d for v in verdicts for d in v.disallowed_items]
    items = [i for e in case.extractions
             if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL") for i in e.line_items]
    if not items:
        total = next((e.total_amount.value for e in case.extractions
                      if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL")
                      and e.total_amount.value), None)
        fallback = total or (s.claimed_amount if s.claimed_amount and s.claimed_amount > 0 else None)
        if fallback:
            items = [LineItem(description="Claimed amount", amount=float(fallback))]
    hospital = s.hospital_name or next((e.hospital_name.value for e in case.extractions
                                        if e.hospital_name.value), None)
    financial: FinancialBreakdown = calculate(pe, s.claim_category,
                                              pe.is_network(hospital), items, disallowed)

    # --- mirror nodes.decide ---------------------------------------------- #
    return aggregate(verdicts, financial, pe.fraud_thresholds()["auto_manual_review_above"])


def _member(pe: PolicyEngine, member_id: str) -> dict:
    return pe.member(member_id)


# --------------------------------------------------------------------------- #
# Metrics                                                                      #
# --------------------------------------------------------------------------- #

def _case_matches(case: SyntheticCase, decision: Decision) -> tuple[bool, str]:
    """Compare a produced Decision against the case's expected outcome.

    Status must match. For rejects, the expected reason_code must appear among the
    decision's reason codes. For APPROVED/PARTIAL, the amount must be within ₹1."""
    exp = case.expected
    if decision.status != exp["status"]:
        return False, f"status {decision.status} != expected {exp['status']}"
    if exp["status"] == "REJECTED" and "reason_code" in exp:
        codes = [c.code for c in decision.reason_codes]
        if exp["reason_code"] not in codes:
            return False, f"reason {exp['reason_code']} not in {codes}"
    if exp["status"] in ("APPROVED", "PARTIAL") and "expected_amount" in exp:
        if abs(decision.approved_amount - exp["expected_amount"]) > 1:
            return False, (f"amount {decision.approved_amount} != "
                           f"expected {exp['expected_amount']}")
    return True, ""


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def run_decision_eval(cases: list[SyntheticCase], pe: PolicyEngine | None = None) -> dict:
    """Run every case through `decide_from_facts`, compare to expected, and compute
    metrics: overall + per-template accuracy, a confusion matrix with per-class
    precision/recall/F1, approved-amount MAE/max-error, and reason-code accuracy."""
    from app.config import settings
    pe = pe or PolicyEngine(settings.policy_path)

    total = len(cases)
    correct = 0
    per_template = defaultdict(lambda: {"n": 0, "correct": 0})
    # confusion[expected][predicted] = count
    confusion = {a: {b: 0 for b in STATUSES} for a in STATUSES}
    abs_errors: list[float] = []
    max_error = 0.0
    reason_total = 0
    reason_correct = 0
    mismatches: list[dict] = []

    for case in cases:
        decision = decide_from_facts(case, pe)
        ok, why = _case_matches(case, decision)
        exp_status = case.expected["status"]
        pred_status = decision.status

        per_template[case.template]["n"] += 1
        if exp_status in confusion and pred_status in confusion[exp_status]:
            confusion[exp_status][pred_status] += 1

        if ok:
            correct += 1
            per_template[case.template]["correct"] += 1
        else:
            mismatches.append({"case_id": case.case_id, "template": case.template,
                               "expected": case.expected, "got_status": pred_status,
                               "got_amount": decision.approved_amount,
                               "got_reasons": [c.code for c in decision.reason_codes],
                               "why": why, "note": case.note})

        # Amount error on APPROVED/PARTIAL with an expected amount (only when status matched).
        if (exp_status in ("APPROVED", "PARTIAL") and "expected_amount" in case.expected
                and pred_status == exp_status):
            err = abs(decision.approved_amount - case.expected["expected_amount"])
            abs_errors.append(err)
            max_error = max(max_error, err)

        # Reason-code accuracy on rejects.
        if exp_status == "REJECTED" and "reason_code" in case.expected:
            reason_total += 1
            if case.expected["reason_code"] in [c.code for c in decision.reason_codes]:
                reason_correct += 1

    per_class = {}
    for cls in STATUSES:
        tp = confusion[cls][cls]
        fp = sum(confusion[other][cls] for other in STATUSES if other != cls)
        fn = sum(confusion[cls][other] for other in STATUSES if other != cls)
        p, r, f1 = _prf(tp, fp, fn)
        per_class[cls] = {"precision": p, "recall": r, "f1": f1,
                          "support": sum(confusion[cls].values()), "tp": tp, "fp": fp, "fn": fn}

    return {
        "n": total,
        "overall_accuracy": correct / total if total else 0.0,
        "correct": correct,
        "per_template": {t: {"n": d["n"], "correct": d["correct"],
                             "accuracy": d["correct"] / d["n"] if d["n"] else 0.0}
                         for t, d in sorted(per_template.items())},
        "confusion": confusion,
        "per_class": per_class,
        "amount_mae": (sum(abs_errors) / len(abs_errors)) if abs_errors else 0.0,
        "amount_max_error": max_error,
        "amount_n": len(abs_errors),
        "reason_code_accuracy": (reason_correct / reason_total) if reason_total else 0.0,
        "reason_code_n": reason_total,
        "mismatches": mismatches,
    }


# --------------------------------------------------------------------------- #
# Reporting                                                                    #
# --------------------------------------------------------------------------- #

def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def to_markdown(result: dict) -> str:
    lines: list[str] = []
    lines.append("# Decision-Layer Eval Report (synthetic cases, real rules, no Gemini)")
    lines.append("")
    lines.append("Runs synthetic labeled claim scenarios through the REAL deterministic "
                 "decision logic (5 rule checks + financial calculator + aggregator), "
                 "composed exactly as the pipeline's decide stage. No Gemini, so the whole "
                 "suite runs in seconds. PURE-ADDITIVE: the pipeline and the 12 cases are "
                 "untouched.")
    lines.append("")
    lines.append(f"- **Cases:** {result['n']}")
    lines.append(f"- **Overall decision accuracy:** {_pct(result['overall_accuracy'])} "
                 f"({result['correct']}/{result['n']})")
    lines.append(f"- **Approved/partial amount MAE:** ₹{result['amount_mae']:.4f} "
                 f"(max error ₹{result['amount_max_error']:.4f}, n={result['amount_n']})")
    lines.append(f"- **Reason-code accuracy on rejects:** "
                 f"{_pct(result['reason_code_accuracy'])} (n={result['reason_code_n']})")
    lines.append("")

    lines.append("## Per-template accuracy")
    lines.append("")
    lines.append("| template | n | correct | accuracy |")
    lines.append("|---|---:|---:|---:|")
    for t, d in result["per_template"].items():
        lines.append(f"| {t} | {d['n']} | {d['correct']} | {_pct(d['accuracy'])} |")
    lines.append("")

    lines.append("## Confusion matrix (rows = expected, cols = predicted)")
    lines.append("")
    lines.append("| expected \\ predicted | " + " | ".join(STATUSES) + " |")
    lines.append("|---|" + "|".join(["---:"] * len(STATUSES)) + "|")
    for a in STATUSES:
        row = " | ".join(str(result["confusion"][a][b]) for b in STATUSES)
        lines.append(f"| **{a}** | {row} |")
    lines.append("")

    lines.append("## Per-class precision / recall / F1")
    lines.append("")
    lines.append("| class | support | precision | recall | F1 |")
    lines.append("|---|---:|---:|---:|---:|")
    for cls in STATUSES:
        c = result["per_class"][cls]
        lines.append(f"| {cls} | {c['support']} | {_pct(c['precision'])} | "
                     f"{_pct(c['recall'])} | {_pct(c['f1'])} |")
    lines.append("")

    if result["mismatches"]:
        lines.append("## Mismatches")
        lines.append("")
        lines.append(f"{len(result['mismatches'])} case(s) did not match the expected outcome:")
        lines.append("")
        lines.append("| case | template | why | note |")
        lines.append("|---|---|---|---|")
        for m in result["mismatches"][:50]:
            lines.append(f"| {m['case_id']} | {m['template']} | {m['why']} | {m['note']} |")
        if len(result["mismatches"]) > 50:
            lines.append(f"| ... | | ({len(result['mismatches']) - 50} more) | |")
    else:
        lines.append("## Mismatches")
        lines.append("")
        lines.append("None — every case matched its independently-derived expected outcome.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    from app.config import settings
    pe = PolicyEngine(settings.policy_path)
    cases = generate_cases(pe)
    print(f"Generated {len(cases)} synthetic cases. Running decision-layer eval ...")
    result = run_decision_eval(cases, pe)
    md = to_markdown(result)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(md)
    print(f"Wrote report -> {REPORT_PATH}")
    print(f"  n cases:           {result['n']}")
    print(f"  overall accuracy:  {_pct(result['overall_accuracy'])} "
          f"({result['correct']}/{result['n']})")
    print(f"  amount MAE:        ₹{result['amount_mae']:.4f} "
          f"(max ₹{result['amount_max_error']:.4f})")
    print(f"  reason-code acc:   {_pct(result['reason_code_accuracy'])}")
    for cls in STATUSES:
        c = result["per_class"][cls]
        print(f"  {cls:<14} P={_pct(c['precision'])} R={_pct(c['recall'])} "
              f"F1={_pct(c['f1'])} (support {c['support']})")
    if result["mismatches"]:
        print(f"  MISMATCHES: {len(result['mismatches'])}")
        for m in result["mismatches"][:10]:
            print(f"    - {m['case_id']} [{m['template']}] {m['why']} | {m['note']}")


if __name__ == "__main__":
    main()
