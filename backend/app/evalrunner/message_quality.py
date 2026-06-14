"""LLM-as-judge eval for MEMBER-FACING message quality.

This is the message-quality leg of the eval framework. The other legs measure the
*decision* (P/R/F1, amount-MAE, extraction field-F1); this one measures the WORDING
the member actually sees — the blocked-claim problem messages (TC001-003) and the
decided-claim member_message + reason-code details.

It is PURE-ADDITIVE: it grades text the pipeline already produced, never changing a
decision. An LLM judge (Gemini Pro, temperature 0, structured output) scores each
message 1-5 on five dimensions plus a one-line rationale; the overall is the mean of
the five dimensions (computed, not judged, so it is reproducible from the scores).

`run_message_quality_eval` grades the canonical 12 eval cases (12 judge calls) and
aggregates per-dimension + overall means and a per-case table. `to_markdown` renders
the report; the CLI (`python -m app.evalrunner.message_quality`) writes
`docs/message_quality_report.md`.
"""
from __future__ import annotations

import pathlib

from pydantic import BaseModel, Field

from app.config import settings
from app.models.schemas import ClaimResult
from app.services.gemini import generate_structured

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2].parent
REPORT_PATH = _REPO_ROOT / "docs" / "message_quality_report.md"

# The five scored dimensions, in report order. `overall` is computed (mean), not judged.
DIMENSIONS = ["specificity", "actionability", "correctness", "tone", "jargon_free"]


class MessageGrade(BaseModel):
    """Structured output of the LLM judge for one member-facing message.

    Each dimension is an integer 1-5 (1 = poor, 5 = excellent). `overall` is the mean
    of the five dimensions and is recomputed on construction so it is always consistent
    with the scores regardless of what the model returns for it."""

    specificity: int = Field(..., ge=1, le=5,
                             description="Does it name the EXACT problem/amount/date?")
    actionability: int = Field(..., ge=1, le=5,
                               description="Does the member know what to do next?")
    correctness: int = Field(..., ge=1, le=5,
                             description="Consistent with the decision/policy facts given?")
    tone: int = Field(..., ge=1, le=5,
                      description="Clear, non-blaming, professional?")
    jargon_free: int = Field(..., ge=1, le=5,
                             description="Plain language, no internal codes/jargon?")
    rationale: str = Field("", description="One-line justification for the scores.")
    overall: float = Field(0.0, description="Mean of the five dimensions (computed).")

    def model_post_init(self, __context) -> None:
        # Always recompute overall from the dimensions so it cannot drift from the
        # scores (the judge may omit it or return an inconsistent value).
        object.__setattr__(self, "overall",
                           round(sum(getattr(self, d) for d in DIMENSIONS) / len(DIMENSIONS), 3))


_RUBRIC = (
    "You are a meticulous quality reviewer for a health-insurance claims product. You "
    "grade the MEMBER-FACING message that a member sees about their claim. Score each "
    "of these five dimensions as an INTEGER from 1 (poor) to 5 (excellent):\n"
    "- specificity: Does the message name the EXACT problem, amount, date, document or "
    "policy fact? Generic messages ('your claim could not be processed') score low; "
    "messages that name the missing document / exact rupee amount / specific date score high.\n"
    "- actionability: After reading it, does the member clearly know what to do NEXT "
    "(or that no action is needed)? Concrete next steps score high; dead ends score low.\n"
    "- correctness: Is the message CONSISTENT with the claim CONTEXT (decision status and "
    "key facts) given below? A message that contradicts or misstates the facts scores low.\n"
    "- tone: Is it clear, professional, and non-blaming toward the member?\n"
    "- jargon_free: Is it plain language a layperson understands, with no leaked internal "
    "codes, rule names, or system jargon? (A human-readable reason like 'waiting period' is "
    "fine; a raw code like 'WAITING_PERIOD' or 'reason_code=...' is jargon.)\n"
    "Judge ONLY the member-facing message text, using the CONTEXT to check correctness. "
    "Also give a one-line rationale. Do not invent facts not present in the context."
)


def grade_message(context: str, message: str) -> MessageGrade:
    """Grade one member-facing message with the LLM judge (Gemini Pro, temp 0, structured).

    `context` describes the claim (decision status + key facts) so the judge can score
    correctness; `message` is the exact member-facing text. Returns a MessageGrade with
    per-dimension integer scores, a rationale, and a computed overall mean."""
    prompt = (
        f"{_RUBRIC}\n\n"
        f"=== CLAIM CONTEXT (ground truth for correctness) ===\n{context}\n\n"
        f"=== MEMBER-FACING MESSAGE TO GRADE ===\n{message}\n\n"
        "Return the structured grade."
    )
    return generate_structured([prompt], MessageGrade, model=settings.gemini_pro_model)


def _claim_context_and_message(claim_result: ClaimResult) -> tuple[str, str]:
    """Derive the (context, member-facing message) pair for one claim.

    Blocked claims → the first problem's message (with the problem kind/list as context).
    Decided claims → the decision's member_message, with reason-code details appended,
    and the status/amount/reasons as context."""
    if claim_result.blocked and claim_result.problems:
        problems = claim_result.problems
        kinds = ", ".join(p.kind for p in problems)
        context = (
            "Decision status: BLOCKED (claim stopped before adjudication).\n"
            f"Problem kind(s): {kinds}.\n"
            "The member-facing message should clearly state what is wrong (which "
            "document / patient / requirement) and what the member must do to proceed."
        )
        # Grade the primary member-facing problem message (the one shown first).
        return context, problems[0].message

    d = claim_result.decision
    if d is None:
        # Defensive: neither blocked-with-problems nor decided. Grade whatever text exists.
        return ("Decision status: UNKNOWN (no decision and no blocking problem).", "")

    reasons = "; ".join(f"{c.code}: {c.detail}" for c in d.reason_codes) or "(none)"
    context = (
        f"Decision status: {d.status}.\n"
        f"Approved amount: INR {d.approved_amount}.\n"
        f"Reason codes (internal): {reasons}.\n"
        "The member-facing message should explain this outcome in plain language and, "
        "where relevant, what the member can do next."
    )
    # Member sees member_message; the reason-code details are part of the member-facing
    # explanation too. The aggregator often BUILDS member_message FROM those details, so
    # appending a detail already contained in the message would create an artificial
    # duplicate the member never sees and unfairly penalise tone. Only append details
    # not already present, and grade the exact text the member would actually read.
    message = d.member_message or ""
    extra = [c.detail for c in d.reason_codes if c.detail and c.detail not in message]
    if extra:
        joined = "\n".join(extra)
        message = f"{message}\n{joined}" if message else joined
    return context, message


def grade_claim_messages(claim_result: ClaimResult) -> dict:
    """Grade the member-facing text of ONE claim. Returns per-dimension scores, overall,
    rationale, plus the context/message that were graded (for the report)."""
    context, message = _claim_context_and_message(claim_result)
    grade = grade_message(context, message)
    out = {d: getattr(grade, d) for d in DIMENSIONS}
    out["overall"] = grade.overall
    out["rationale"] = grade.rationale
    out["message"] = message
    out["context"] = context
    return out


def _aggregate(per_case: list[dict]) -> dict:
    """Mean of each dimension + overall across graded cases (0.0 on an empty list)."""
    n = len(per_case)
    means: dict[str, float] = {}
    for key in [*DIMENSIONS, "overall"]:
        means[key] = round(sum(c[key] for c in per_case) / n, 3) if n else 0.0
    return means


def run_message_quality_eval(sample: list[ClaimResult] | None = None) -> dict:
    """Grade each eval case's member-facing message and aggregate.

    `sample`: pre-computed ClaimResults to grade (avoids re-running the live pipeline).
    When None, runs the canonical 12 cases through `run_all` once and grades those —
    exactly 12 judge calls, bounding cost. Returns aggregate means, n, and a per-case
    table (case_id/name + scores + the graded message)."""
    if sample is None:
        # Re-run the live pipeline once to obtain the 12 cases' real messages.
        from app.evalrunner.runner import run_all
        raw = run_all()
        cases: list[dict] = []
        for r in raw:
            res = r.get("result")
            if res is None:
                # Case errored before producing a message; record it, skip grading.
                cases.append({"case_id": r["case_id"], "case_name": r.get("case_name", ""),
                              "errored": True})
                continue
            cr = ClaimResult.model_validate(res)
            graded = grade_claim_messages(cr)
            cases.append({"case_id": r["case_id"], "case_name": r.get("case_name", ""),
                          **graded})
    else:
        # Grade caller-supplied results. No case metadata, so derive ids from claim_id.
        cases = []
        for cr in sample:
            graded = grade_claim_messages(cr)
            cases.append({"case_id": cr.claim_id, "case_name": "", **graded})

    graded_cases = [c for c in cases if not c.get("errored")]
    return {
        "n": len(graded_cases),
        "n_total": len(cases),
        "aggregate": _aggregate(graded_cases),
        "per_case": cases,
    }


# --------------------------------------------------------------------------- #
# Reporting                                                                    #
# --------------------------------------------------------------------------- #

def to_markdown(result: dict) -> str:
    agg = result["aggregate"]
    lines: list[str] = []
    lines.append("# Message-Quality Eval Report (LLM-as-judge on member-facing messages)")
    lines.append("")
    lines.append(
        "An LLM judge (Gemini Pro, temperature 0, structured output) scores the "
        "MEMBER-FACING message of each of the 12 eval cases on a 1-5 rubric. This is the "
        "message-quality leg of the eval framework — the decision legs measure accuracy / "
        "P-R-F1 / amount-MAE / extraction field-F1; this measures the wording members see "
        "(the TC001-003 blocking messages and the rejection / decision messages). "
        "PURE-ADDITIVE: no decision is changed; existing text is graded. `overall` is the "
        "mean of the five dimensions.")
    lines.append("")
    lines.append(f"- **Cases graded:** {result['n']} / {result['n_total']}")
    lines.append(f"- **Overall mean:** {agg['overall']:.2f} / 5")
    lines.append("")
    lines.append("## Aggregate scores per dimension (mean over graded cases)")
    lines.append("")
    lines.append("| dimension | mean (1-5) |")
    lines.append("|---|---:|")
    for d in DIMENSIONS:
        lines.append(f"| {d} | {agg[d]:.2f} |")
    lines.append(f"| **overall** | **{agg['overall']:.2f}** |")
    lines.append("")
    lines.append("## Per-case scores")
    lines.append("")
    header = "| case | " + " | ".join(DIMENSIONS) + " | overall |"
    lines.append(header)
    lines.append("|---|" + "|".join(["---:"] * (len(DIMENSIONS) + 1)) + "|")
    for c in result["per_case"]:
        label = f"{c['case_id']} {c.get('case_name', '')}".strip()
        if c.get("errored"):
            lines.append(f"| {label} | — | — | — | — | — | (errored) |")
            continue
        cells = " | ".join(str(c[d]) for d in DIMENSIONS)
        lines.append(f"| {label} | {cells} | {c['overall']:.2f} |")
    lines.append("")
    lines.append("## Graded messages + rationale")
    lines.append("")
    for c in result["per_case"]:
        label = f"{c['case_id']} — {c.get('case_name', '')}".strip(" —")
        lines.append(f"### {label}")
        if c.get("errored"):
            lines.append("_Case errored before producing a message._")
            lines.append("")
            continue
        msg = (c.get("message") or "").replace("\n", " ")
        lines.append(f"- **Message:** {msg}")
        lines.append("- **Scores:** " + ", ".join(f"{d}={c[d]}" for d in DIMENSIONS)
                     + f", overall={c['overall']:.2f}")
        if c.get("rationale"):
            lines.append(f"- **Judge rationale:** {c['rationale']}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    print("Running message-quality eval (live pipeline + LLM judge on the 12 cases) ...")
    result = run_message_quality_eval()
    md = to_markdown(result)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(md)
    agg = result["aggregate"]
    print(f"Wrote report -> {REPORT_PATH}")
    print(f"  cases graded:  {result['n']}/{result['n_total']}")
    print(f"  overall mean:  {agg['overall']:.2f} / 5")
    for d in DIMENSIONS:
        print(f"  {d:<14} {agg[d]:.2f}")


if __name__ == "__main__":
    main()
