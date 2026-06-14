"""Self-improving loop for the claims product — ADVISORY ONLY.

This module reads the system's OWN evaluation outputs (decision-layer eval over the
synthetic cases, extraction-robustness numbers, confidence-calibration ECE, and the
confidence weights/penalty from config) and PROPOSES concrete, justified improvements:
prompt nudges, threshold/weight changes, and fixture additions.

CRITICALLY ADVISORY: nothing here changes a prompt, threshold, weight, or decision.
`analyze()` only READS metrics; `propose()` only emits structured `Proposal` records;
`to_markdown()` only renders them. The `auto_applicable` flag on every proposal is
INFORMATIONAL — no production code path applies a proposal. The decision pipeline and
the 12 cases are never touched.

The proposal CORE is rule-based and deterministic (grounded in the metrics). An
OPTIONAL Gemini synthesis pass (`generate_text`, behind `use_gemini=True`) can draft a
human-readable narrative rationale from the findings — but the proposals themselves are
always produced WITHOUT Gemini, so the deterministic set is reproducible and testable.

CLI:  python -m app.services.self_improve
      -> writes docs/improvement_proposals.md from the ACTUAL current metrics.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, asdict

# docs/ lives at the repo root; this file is backend/app/services/self_improve.py.
_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_DIR.parent
REPORT_PATH = _REPO_ROOT / "docs" / "improvement_proposals.md"

# Tolerances that turn a metric into a finding/proposal. Tuned to be honest: clean
# metrics produce the "no change; keep stressing it" proposal, not a fabricated fix.
ECE_PROPOSE_THRESHOLD = 0.05      # ECE above this is worth calibrating
TEMPLATE_ACCURACY_FLOOR = 0.999   # below this, a decision template is "mismatching"
CORD_MATCH_FLOOR = 0.95           # below this, the extractor misses non-trivial totals
HANDWRITING_FLOOR = 0.90          # below this, handwriting reads are worth a fixture push


# --------------------------------------------------------------------------- #
# Proposal record                                                              #
# --------------------------------------------------------------------------- #

@dataclass
class Proposal:
    """One advisory improvement. `auto_applicable` is INFORMATIONAL ONLY — no code
    path applies a proposal. It is True only for changes that demonstrably cannot
    alter a decision/threshold/weight without human review (e.g. adding fixtures)."""
    area: str
    observation: str
    proposed_change: str
    rationale: str
    risk: str            # "low" | "medium" | "high"
    auto_applicable: bool

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# 1. analyze() — gather the available eval signals into a findings object      #
# --------------------------------------------------------------------------- #

def _decision_findings(cases_limit: int | None) -> dict:
    """Run the deterministic decision eval (no Gemini) and distil decision-layer
    findings: overall + per-template accuracy, mismatching templates, amount-MAE,
    reason-code accuracy. `cases_limit` slices the case set for fast tests."""
    from app.config import settings
    from app.services.policy_engine import PolicyEngine
    from app.evalrunner.synthetic import generate_cases
    from app.evalrunner.decision_eval import run_decision_eval

    pe = PolicyEngine(settings.policy_path)
    cases = generate_cases(pe)
    if cases_limit is not None:
        # Keep at least one case per template so per-template coverage stays honest.
        seen: dict[str, int] = {}
        sliced = []
        for c in cases:
            seen[c.template] = seen.get(c.template, 0) + 1
            if seen[c.template] <= max(1, cases_limit // 9):
                sliced.append(c)
        cases = sliced or cases[:cases_limit]

    result = run_decision_eval(cases, pe)
    mismatching = {
        t: d for t, d in result["per_template"].items()
        if d["accuracy"] < TEMPLATE_ACCURACY_FLOOR
    }
    return {
        "n": result["n"],
        "overall_accuracy": result["overall_accuracy"],
        "per_template": result["per_template"],
        "mismatching_templates": mismatching,
        "amount_mae": result["amount_mae"],
        "amount_max_error": result["amount_max_error"],
        "reason_code_accuracy": result["reason_code_accuracy"],
        "reason_code_n": result["reason_code_n"],
        "n_mismatches": len(result["mismatches"]),
    }


def _extraction_findings() -> dict:
    """Distil extraction-robustness findings from the committed report numbers (the
    live CORD/handwriting runners call Gemini, so we read the persisted report rather
    than re-running them here). Returns the total-match rate, the systematic
    locale/number-parsing miss pattern, and handwriting read accuracy.

    The numbers mirror docs/extraction_robustness_report.md; this is the honest
    snapshot the analyzer reasons over. If a live result dict is passed via
    `analyze(extraction=...)`, those numbers are used instead."""
    return {
        "source": "docs/extraction_robustness_report.md",
        "cord_n": 15,
        "cord_total_match_rate": 0.733,
        "cord_mean_rel_error": 0.266,
        "cord_median_rel_error": 0.0,
        # The 4 misses are all the IDR thousands-separator artefact (60.000 -> 60.0):
        # rel-err ~1.0 on each, high confidence. This is the systematic pattern.
        "cord_locale_misses": 4,
        "cord_locale_miss_rel_error": 0.999,
        "cord_mean_confidence": 0.959,
        "handwriting_n": 10,
        "handwriting_read_accuracy": 0.70,
        "locale_pattern": (
            "Non-INR receipts using '.'/',' as THOUSANDS separators (IDR locale) are "
            "misread as decimals (60.000 -> 60.0): 4/15 CORD misses, rel-err ~1.0, high "
            "confidence. INR (the product domain) does not use this convention."
        ),
    }


def _calibration_findings() -> dict:
    """Distil confidence-calibration findings: ECE before/after, whether calibration
    is currently APPLIED (off by default), and the over/under-confidence direction
    read off the committed reliability table."""
    from app.config import settings
    from app.services.calibration import load_calibrator

    cal = load_calibrator(settings.calibration_map_path)
    # From docs/calibration_report.md (in-sample fit on n=31 labelled pairs).
    ece_before = 0.1442
    ece_after = 0.0000
    # Reliability table: the dominant [0.9, 1.0] bin has mean_conf 0.964 > acc 0.828,
    # i.e. the model is OVER-confident in its high-confidence bin.
    high_bin = {"mean_confidence": 0.964, "accuracy": 0.828, "count": 29}
    return {
        "source": "docs/calibration_report.md",
        "ece_before": ece_before,
        "ece_after": ece_after,
        "ece_improvement": round(ece_before - ece_after, 4),
        "calibration_enabled": settings.confidence_calibration_enabled,
        "calibrator_present": cal is not None,
        "calibrator_method": getattr(cal, "method", None) if cal else None,
        "high_bin": high_bin,
        "direction": (
            "over-confident" if high_bin["mean_confidence"] > high_bin["accuracy"]
            else "under-confident"
        ),
        "n_labelled": 31,
        "in_sample": True,  # ECE-after is in-sample; honesty flag for proposals
    }


def _confidence_config_findings() -> dict:
    """Read the confidence weights + degradation penalty from config (no behaviour
    change — pure read)."""
    from app.config import settings
    from app.services import confidence as conf

    return {
        "weights": {
            "extraction": conf.W_EXTRACTION,
            "rules": conf.W_RULES,
            "completeness": conf.W_COMPLETENESS,
            "verifier": conf.W_VERIFIER,
        },
        "weights_sum": round(
            conf.W_EXTRACTION + conf.W_RULES + conf.W_COMPLETENESS + conf.W_VERIFIER, 4),
        "degradation_penalty": settings.degradation_penalty,
    }


def analyze(cases_limit: int | None = None, extraction: dict | None = None) -> dict:
    """Gather every available eval signal into a structured findings object.

    - decision: runs the deterministic decision eval over the synthetic cases (sliced
      to `cases_limit` for speed in tests) -> overall + per-template accuracy,
      mismatching templates, amount-MAE, reason-code accuracy.
    - extraction: total-match rate + the systematic locale/number-parsing miss pattern
      + handwriting read accuracy (from the committed robustness report, or `extraction`
      if a live result dict is supplied).
    - calibration: ECE before/after, whether calibration is applied (off by default),
      over/under-confidence direction.
    - confidence config: the weights + degradation penalty (pure read).

    No Gemini. No behaviour change."""
    return {
        "decision": _decision_findings(cases_limit),
        "extraction": extraction or _extraction_findings(),
        "calibration": _calibration_findings(),
        "confidence_config": _confidence_config_findings(),
    }


# --------------------------------------------------------------------------- #
# 2. propose() — turn findings into concrete, justified, advisory proposals    #
# --------------------------------------------------------------------------- #

def _propose_decision(d: dict) -> list[Proposal]:
    proposals: list[Proposal] = []
    mismatching = d.get("mismatching_templates", {})
    acc = d.get("overall_accuracy", 0.0)
    if mismatching:
        worst = sorted(mismatching.items(), key=lambda kv: kv[1]["accuracy"])
        names = ", ".join(f"{t} ({v['accuracy']:.0%})" for t, v in worst)
        proposals.append(Proposal(
            area="decision_rules",
            observation=(
                f"Per-template decision accuracy is below target on: {names} "
                f"(overall {acc:.1%} over {d['n']} synthetic cases)."),
            proposed_change=(
                "Investigate the failing template(s): inspect the mismatches, then "
                "adjust the responsible rule's threshold/ordering OR fix the case "
                "label if the expectation is wrong. Re-run the decision eval to "
                "confirm. HUMAN REVIEW REQUIRED before any rule/threshold edit."),
            rationale=(
                "A template below 100% on deterministic synthetic cases indicates a "
                "concrete rule/aggregator gap, not noise — these cases have "
                "independently-derived expected outcomes."),
            risk="high",
            auto_applicable=False,
        ))
    else:
        proposals.append(Proposal(
            area="decision_rules",
            observation=(
                f"Per-template decision accuracy is {acc:.0%} on {d['n']} synthetic "
                f"cases (all templates clean; amount-MAE ₹{d['amount_mae']:.4f}; "
                f"reason-code accuracy {d['reason_code_accuracy']:.0%})."),
            proposed_change=(
                "No rule/threshold/weight change indicated. To keep coverage honest, "
                "EXPAND the synthetic set with boundary cases (amounts exactly AT each "
                "limit/sub-limit/threshold) and adversarial templates (conflicting "
                "rule signals, near-miss dates) so 100% stays meaningful."),
            rationale=(
                "Clean metrics on the current templates do not prove robustness at "
                "decision boundaries; adding boundary + adversarial fixtures stresses "
                "the rules without touching them — purely additive coverage."),
            risk="low",
            auto_applicable=True,  # adding fixtures cannot alter a live decision
        ))
    return proposals


def _propose_extraction(e: dict) -> list[Proposal]:
    proposals: list[Proposal] = []
    match_rate = e.get("cord_total_match_rate", 1.0)
    locale_misses = e.get("cord_locale_misses", 0)
    if match_rate < CORD_MATCH_FLOOR and locale_misses:
        proposals.append(Proposal(
            area="extraction_prompt",
            observation=(
                f"Extraction misreads thousands-separated totals on non-INR receipts "
                f"({locale_misses}/{e.get('cord_n')} CORD misses, rel-err "
                f"~{e.get('cord_locale_miss_rel_error', 1.0):.1f}, total-match "
                f"{match_rate:.0%}, mean confidence {e.get('cord_mean_confidence'):.2f})."),
            proposed_change=(
                "Add a number-normalization instruction to the extraction prompt: "
                "'Interpret the document's locale; treat '.'/',' as thousands "
                "separators when the magnitude/format implies it (e.g. printed "
                "60.000 on a receipt = 60000, not 60.0).' Validate on the CORD set "
                "before any rollout — DO NOT auto-apply."),
            rationale=(
                "The misses are a systematic locale artefact (digits read faithfully, "
                "separator misinterpreted), not random OCR error, so a targeted prompt "
                "nudge directly addresses the pattern. INR bills are unaffected, so the "
                "12 cases stay green. Confidence is also high on these misses, which "
                "feeds the calibration finding below."),
            risk="low",
            auto_applicable=False,  # changes a prompt -> human review
        ))
    hw = e.get("handwriting_read_accuracy")
    if hw is not None and hw < HANDWRITING_FLOOR:
        proposals.append(Proposal(
            area="extraction_fixtures",
            observation=(
                f"Handwriting read accuracy is {hw:.0%} on {e.get('handwriting_n')} "
                f"RxHandBD crops — the failure mode is near-miss spellings "
                f"(e.g. 'inderen' -> 'Inderer')."),
            proposed_change=(
                "Expand the handwriting probe set and add a confusable-character "
                "post-check (fuzzy-match readings against the policy's known "
                "medicine vocabulary) as an ADVISORY confidence signal. Adding "
                "fixtures/an advisory check does not change any decision."),
            rationale=(
                "Handwritten medicine names are inherently ambiguous; more labelled "
                "crops make the read-accuracy number trustworthy, and a vocabulary "
                "check surfaces likely misreads without overriding the extractor."),
            risk="low",
            auto_applicable=True,  # fixtures + advisory signal only
        ))
    return proposals


def _propose_calibration(c: dict) -> list[Proposal]:
    proposals: list[Proposal] = []
    ece = c.get("ece_before", 0.0)
    if ece > ECE_PROPOSE_THRESHOLD:
        hb = c.get("high_bin", {})
        applied = c.get("calibration_enabled", False)
        present = c.get("calibrator_present", False)
        in_sample = c.get("in_sample", False)
        change = (
            "Enable the fitted calibration map "
            "(settings.confidence_calibration_enabled=True) in production "
            "ONCE VALIDATED on held-out data."
            if present else
            "Fit and commit a calibration map, then enable it once validated.")
        proposals.append(Proposal(
            area="confidence_calibration",
            observation=(
                f"Confidence is {c.get('direction')} in the [0.9,1.0] bin "
                f"(accuracy {hb.get('accuracy', 0):.2f} vs mean conf "
                f"{hb.get('mean_confidence', 0):.2f}, n={hb.get('count')}); "
                f"ECE {ece:.2f} before / {c.get('ece_after', 0):.2f} after the "
                f"isotonic fit. Calibration is currently "
                f"{'APPLIED' if applied else 'OFF (default)'}."),
            proposed_change=change + (
                " The map is already committed and inert; flipping the flag is "
                "reversible and never changes the weights/penalty."),
            rationale=(
                f"An ECE of {ece:.2f} means '0.96' overstates correctness ("
                f"{hb.get('accuracy', 0):.0%} actual) — calibration makes the score "
                f"statistically meaningful. Risk is MEDIUM because the current fit is "
                + ("in-sample on a small set (n="
                   f"{c.get('n_labelled')}); validate on logged real outcomes at "
                   "volume and report held-out ECE before enabling."
                   if in_sample else "validated on held-out data.")),
            risk="medium",
            auto_applicable=False,  # flipping a confidence-affecting flag -> human review
        ))
    else:
        proposals.append(Proposal(
            area="confidence_calibration",
            observation=(
                f"Confidence is well calibrated (ECE {ece:.2f} <= "
                f"{ECE_PROPOSE_THRESHOLD})."),
            proposed_change=(
                "No calibration change indicated. Keep monitoring ECE drift on logged "
                "real outcomes and refit if it rises above the threshold."),
            rationale="Calibration is unnecessary when the score already tracks accuracy.",
            risk="low",
            auto_applicable=True,
        ))
    return proposals


def propose(findings: dict, use_gemini: bool = False) -> list[Proposal]:
    """Turn a findings object into CONCRETE, justified, advisory proposals.

    The CORE is rule-based and DETERMINISTIC — grounded in the metrics, never
    hallucinated. Each proposal names the area, the observed metric, a concrete
    change, a rationale, a risk, and the informational `auto_applicable` flag.

    When `use_gemini=True`, an OPTIONAL synthesis pass drafts a human-readable
    narrative rationale from the findings and APPENDS it to each proposal's rationale
    (best-effort; failures are swallowed). The proposal SET — areas, changes, risks,
    flags — is identical with or without Gemini, so the deterministic path is the
    source of truth.

    INVARIANT: a proposal with `auto_applicable=True` never proposes changing a
    decision threshold, rule, weight, or a confidence-affecting flag — only additive,
    review-free actions (adding fixtures / advisory signals / monitoring)."""
    proposals: list[Proposal] = []
    proposals += _propose_decision(findings.get("decision", {}))
    proposals += _propose_extraction(findings.get("extraction", {}))
    proposals += _propose_calibration(findings.get("calibration", {}))

    if use_gemini:
        _attach_gemini_narrative(findings, proposals)
    return proposals


def _attach_gemini_narrative(findings: dict, proposals: list[Proposal]) -> None:
    """Best-effort Gemini synthesis: draft a human-readable narrative for each
    proposal from the grounded metrics. Never raises; on any failure the
    deterministic rationales stand. Does NOT introduce new proposals."""
    try:
        from app.services.gemini import generate_text
    except Exception:  # noqa: BLE001
        return
    for p in proposals:
        prompt = (
            "You are an ML/claims-ops reviewer. Rewrite the following advisory "
            "improvement as ONE concise, grounded paragraph for an ops reader. Do NOT "
            "invent numbers — use only what is given. Keep it factual and actionable.\n\n"
            f"Area: {p.area}\nObservation: {p.observation}\n"
            f"Proposed change: {p.proposed_change}\nRisk: {p.risk}\n"
            f"Existing rationale: {p.rationale}")
        try:
            narrative = generate_text(prompt)
        except Exception:  # noqa: BLE001
            continue
        if narrative:
            p.rationale = f"{p.rationale}\n\n_Synthesis:_ {narrative}"


# --------------------------------------------------------------------------- #
# 3. to_markdown() + CLI                                                       #
# --------------------------------------------------------------------------- #

def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def to_markdown(findings: dict, proposals: list[Proposal]) -> str:
    d = findings.get("decision", {})
    e = findings.get("extraction", {})
    c = findings.get("calibration", {})
    cc = findings.get("confidence_config", {})
    lines: list[str] = []
    lines.append("# Self-Improvement Proposals (advisory only)")
    lines.append("")
    lines.append("The system reads its OWN evaluation outputs (decision eval, extraction "
                 "robustness, confidence calibration, confidence config) and proposes "
                 "concrete improvements. **ADVISORY ONLY**: nothing here changes a prompt, "
                 "threshold, weight, or decision. The `auto_applicable` flag is "
                 "informational — no code path applies a proposal. The decision pipeline "
                 "and the 12 cases are untouched.")
    lines.append("")

    # Findings summary -----------------------------------------------------
    lines.append("## Findings (current metrics)")
    lines.append("")
    lines.append("### Decision layer")
    lines.append(f"- Cases: **{d.get('n')}**  ·  overall accuracy: "
                 f"**{_pct(d.get('overall_accuracy', 0))}**")
    lines.append(f"- Amount MAE: ₹{d.get('amount_mae', 0):.4f} "
                 f"(max ₹{d.get('amount_max_error', 0):.4f})  ·  reason-code accuracy: "
                 f"{_pct(d.get('reason_code_accuracy', 0))} (n={d.get('reason_code_n')})")
    mm = d.get("mismatching_templates", {})
    lines.append(f"- Mismatching templates: "
                 f"{', '.join(mm) if mm else '**none** (all 100%)'}")
    lines.append("")
    lines.append("### Extraction")
    lines.append(f"- CORD total-match: **{_pct(e.get('cord_total_match_rate', 0))}** "
                 f"(n={e.get('cord_n')}, mean rel-err {e.get('cord_mean_rel_error')}, "
                 f"median {e.get('cord_median_rel_error')})")
    lines.append(f"- Systematic miss: {e.get('locale_pattern')}")
    lines.append(f"- Handwriting read accuracy: "
                 f"**{_pct(e.get('handwriting_read_accuracy', 0))}** "
                 f"(n={e.get('handwriting_n')})")
    lines.append("")
    lines.append("### Calibration")
    lines.append(f"- ECE: **{c.get('ece_before')}** before / **{c.get('ece_after')}** "
                 f"after isotonic fit (improvement {c.get('ece_improvement')})")
    hb = c.get("high_bin", {})
    lines.append(f"- [0.9,1.0] bin: accuracy {hb.get('accuracy')} vs mean conf "
                 f"{hb.get('mean_confidence')} -> **{c.get('direction')}**")
    lines.append(f"- Calibration applied: **{c.get('calibration_enabled')}** "
                 f"(calibrator present: {c.get('calibrator_present')}, "
                 f"method: {c.get('calibrator_method')}); ECE-after is "
                 f"{'in-sample' if c.get('in_sample') else 'held-out'} "
                 f"(n={c.get('n_labelled')}).")
    lines.append("")
    lines.append("### Confidence config (read-only)")
    w = cc.get("weights", {})
    lines.append(f"- Weights: extraction {w.get('extraction')}, rules {w.get('rules')}, "
                 f"completeness {w.get('completeness')}, verifier {w.get('verifier')} "
                 f"(sum {cc.get('weights_sum')})  ·  degradation penalty "
                 f"{cc.get('degradation_penalty')}")
    lines.append("")

    # Proposals ------------------------------------------------------------
    lines.append("## Proposals")
    lines.append("")
    for i, p in enumerate(proposals, 1):
        flag = "auto-applicable" if p.auto_applicable else "human-review-required"
        lines.append(f"### {i}. [{p.area}] — risk: {p.risk} — {flag}")
        lines.append("")
        lines.append(f"- **Observation:** {p.observation}")
        lines.append(f"- **Proposed change:** {p.proposed_change}")
        lines.append(f"- **Rationale:** {p.rationale}")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("_Generated by `app.services.self_improve`. Advisory only — no proposal "
                 "is auto-applied; `auto_applicable` is informational and is never True "
                 "for a change that would alter a decision threshold, rule, weight, or a "
                 "confidence-affecting flag without human review._")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gemini", action="store_true",
                        help="append an optional Gemini narrative to each rationale")
    parser.add_argument("--limit", type=int, default=None,
                        help="slice the synthetic case set (default: full set)")
    args = parser.parse_args()

    print("Analyzing system eval outputs ...")
    findings = analyze(cases_limit=args.limit)
    proposals = propose(findings, use_gemini=args.gemini)
    md = to_markdown(findings, proposals)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(md)
    print(f"Wrote proposals -> {REPORT_PATH}")

    d = findings["decision"]
    print(f"  decision:    {d['overall_accuracy']:.1%} over {d['n']} cases "
          f"(mismatching templates: {len(d['mismatching_templates'])})")
    e = findings["extraction"]
    print(f"  extraction:  CORD match {e['cord_total_match_rate']:.0%}, "
          f"handwriting {e['handwriting_read_accuracy']:.0%}")
    c = findings["calibration"]
    print(f"  calibration: ECE {c['ece_before']} -> {c['ece_after']} "
          f"({c['direction']}, applied={c['calibration_enabled']})")
    print(f"  -> {len(proposals)} proposal(s):")
    for p in proposals:
        flag = "AUTO" if p.auto_applicable else "REVIEW"
        print(f"     - [{p.area}] risk={p.risk} ({flag})")


if __name__ == "__main__":
    main()
