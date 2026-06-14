"""Deterministic tests for the advisory self-improvement loop.

No Gemini: the proposal core runs entirely against the metrics in-process. Verifies
the advisory-only guarantee, the honest "no change" path on clean findings, the
concrete-fix path on injected weaknesses, and the `auto_applicable` flag semantics.
"""
from __future__ import annotations

import pytest

from app.services.self_improve import (
    analyze, propose, to_markdown,
)


# --------------------------------------------------------------------------- #
# analyze() — real signals over a small slice (fast, no Gemini)               #
# --------------------------------------------------------------------------- #

def test_analyze_returns_structured_findings():
    findings = analyze(cases_limit=18)  # small slice for speed
    assert set(findings) >= {"decision", "extraction", "calibration", "confidence_config"}

    d = findings["decision"]
    assert 0.0 <= d["overall_accuracy"] <= 1.0
    assert d["n"] > 0
    assert d["per_template"]  # per-template block present
    for t, v in d["per_template"].items():
        assert 0.0 <= v["accuracy"] <= 1.0
    assert "mismatching_templates" in d
    assert "amount_mae" in d and "reason_code_accuracy" in d


def test_analyze_extraction_and_calibration_blocks():
    findings = analyze(cases_limit=18)
    e = findings["extraction"]
    assert 0.0 <= e["cord_total_match_rate"] <= 1.0
    assert e["cord_locale_misses"] >= 1  # the systematic locale pattern is recorded
    assert "locale" in e["locale_pattern"].lower()
    assert 0.0 <= e["handwriting_read_accuracy"] <= 1.0

    c = findings["calibration"]
    assert c["ece_before"] >= c["ece_after"]
    assert c["direction"] in ("over-confident", "under-confident")
    # Calibration is OFF by default — the analyzer reports it honestly.
    assert c["calibration_enabled"] is False

    cc = findings["confidence_config"]
    assert cc["weights_sum"] == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# propose() — injected weaknesses produce concrete, named proposals           #
# --------------------------------------------------------------------------- #

def _weak_findings() -> dict:
    """A synthetic findings object with deliberately-injected weaknesses:
    a template at 0.7 accuracy, an ECE of 0.2, and a CORD locale miss pattern."""
    return {
        "decision": {
            "n": 100, "overall_accuracy": 0.97,
            "per_template": {"clean_approval": {"n": 50, "correct": 50, "accuracy": 1.0},
                             "high_value": {"n": 50, "correct": 35, "accuracy": 0.7}},
            "mismatching_templates": {"high_value": {"n": 50, "correct": 35,
                                                     "accuracy": 0.7}},
            "amount_mae": 0.0, "amount_max_error": 0.0,
            "reason_code_accuracy": 1.0, "reason_code_n": 50, "n_mismatches": 15,
        },
        "extraction": {
            "cord_n": 15, "cord_total_match_rate": 0.733, "cord_mean_rel_error": 0.27,
            "cord_median_rel_error": 0.0, "cord_locale_misses": 4,
            "cord_locale_miss_rel_error": 0.999, "cord_mean_confidence": 0.96,
            "handwriting_n": 10, "handwriting_read_accuracy": 0.70,
            "locale_pattern": "IDR thousands separator misread as decimal.",
        },
        "calibration": {
            "ece_before": 0.2, "ece_after": 0.0, "ece_improvement": 0.2,
            "calibration_enabled": False, "calibrator_present": True,
            "calibrator_method": "isotonic",
            "high_bin": {"mean_confidence": 0.96, "accuracy": 0.83, "count": 29},
            "direction": "over-confident", "n_labelled": 31, "in_sample": True,
        },
        "confidence_config": {
            "weights": {"extraction": 0.3, "rules": 0.3, "completeness": 0.2,
                        "verifier": 0.2}, "weights_sum": 1.0, "degradation_penalty": 0.2,
        },
    }


def _clean_findings() -> dict:
    f = _weak_findings()
    f["decision"]["mismatching_templates"] = {}
    f["decision"]["overall_accuracy"] = 1.0
    f["decision"]["per_template"] = {
        "clean_approval": {"n": 50, "correct": 50, "accuracy": 1.0},
        "high_value": {"n": 50, "correct": 50, "accuracy": 1.0}}
    f["extraction"]["cord_total_match_rate"] = 1.0
    f["extraction"]["cord_locale_misses"] = 0
    f["extraction"]["handwriting_read_accuracy"] = 1.0
    f["calibration"]["ece_before"] = 0.0
    return f


def test_propose_on_weakness_names_area_with_concrete_change_and_risk():
    proposals = propose(_weak_findings())  # no Gemini
    assert proposals
    areas = {p.area for p in proposals}

    # Decision weakness -> a decision_rules proposal naming the failing template.
    dec = [p for p in proposals if p.area == "decision_rules"][0]
    assert "high_value" in dec.observation
    assert dec.risk in ("low", "medium", "high")
    assert dec.proposed_change  # concrete, non-empty

    # Locale extraction miss -> a prompt proposal with a number-normalization change.
    assert "extraction_prompt" in areas
    ext = [p for p in proposals if p.area == "extraction_prompt"][0]
    assert "normal" in ext.proposed_change.lower()  # number-normalization
    assert ext.risk == "low"

    # ECE 0.2 -> a calibration proposal that mentions enabling the map + medium risk.
    cal = [p for p in proposals if p.area == "confidence_calibration"][0]
    assert "enable" in cal.proposed_change.lower()
    assert cal.risk == "medium"


def test_propose_on_clean_findings_is_honest_no_change():
    proposals = propose(_clean_findings())
    dec = [p for p in proposals if p.area == "decision_rules"][0]
    # Honest: no rule/threshold change; instead "keep stressing it" via fixtures.
    assert "no rule" in dec.proposed_change.lower() or "expand" in dec.proposed_change.lower()
    assert "boundary" in dec.proposed_change.lower() or "adversarial" in dec.proposed_change.lower()
    assert dec.risk == "low"

    cal = [p for p in proposals if p.area == "confidence_calibration"][0]
    assert "no calibration change" in cal.proposed_change.lower()


# --------------------------------------------------------------------------- #
# Gemini is optional and behind a flag — deterministic set without it          #
# --------------------------------------------------------------------------- #

def test_proposals_produced_without_gemini():
    # The default path must not import or call Gemini.
    proposals = propose(_weak_findings(), use_gemini=False)
    assert proposals
    # Rationales are the deterministic ones (no synthesis marker appended).
    assert all("_Synthesis:_" not in p.rationale for p in proposals)


def test_gemini_flag_only_appends_narrative_same_proposal_set(monkeypatch):
    base = propose(_weak_findings(), use_gemini=False)

    calls = {"n": 0}

    def fake_generate_text(prompt, *a, **k):
        calls["n"] += 1
        return "synthesized narrative"

    monkeypatch.setattr("app.services.gemini.generate_text", fake_generate_text)
    withg = propose(_weak_findings(), use_gemini=True)

    # Same areas/risks/flags — Gemini only augments the rationale text.
    assert [p.area for p in withg] == [p.area for p in base]
    assert [p.risk for p in withg] == [p.risk for p in base]
    assert [p.auto_applicable for p in withg] == [p.auto_applicable for p in base]
    assert calls["n"] == len(withg)
    assert all("_Synthesis:_" in p.rationale for p in withg)


def test_gemini_failure_is_swallowed_deterministic_rationale_stands(monkeypatch):
    def boom(prompt, *a, **k):
        raise RuntimeError("gemini down")

    monkeypatch.setattr("app.services.gemini.generate_text", boom)
    proposals = propose(_weak_findings(), use_gemini=True)
    assert proposals  # still produced
    assert all("_Synthesis:_" not in p.rationale for p in proposals)


# --------------------------------------------------------------------------- #
# auto_applicable flag semantics — never auto-applies a decision/threshold edit #
# --------------------------------------------------------------------------- #

def test_auto_applicable_never_alters_a_decision_threshold():
    for findings in (_weak_findings(), _clean_findings()):
        for p in propose(findings):
            if p.auto_applicable:
                # Auto-applicable proposals must be additive/review-free only:
                # adding fixtures, advisory signals, or monitoring — never a change
                # to a rule, threshold, weight, prompt, or a confidence flag.
                change = p.proposed_change.lower()
                # Never proposes editing/lowering a rule threshold or weight.
                assert "lower the" not in change and "raise the" not in change
                assert "adjust the responsible rule" not in change
                # Never flips the calibration flag (that requires review).
                assert "enable the fitted" not in change
                assert "confidence_calibration_enabled=true" not in change
                # Prompt edits always require review, so are never auto-applicable.
                assert p.area != "extraction_prompt"


def test_decision_rule_and_calibration_proposals_require_human_review():
    proposals = propose(_weak_findings())
    by_area = {p.area: p for p in proposals}
    # The risky changes (rule edits, prompt edits, flipping calibration) are NOT
    # auto-applicable.
    assert by_area["decision_rules"].auto_applicable is False
    assert by_area["extraction_prompt"].auto_applicable is False
    assert by_area["confidence_calibration"].auto_applicable is False


# --------------------------------------------------------------------------- #
# to_markdown renders findings + proposals                                     #
# --------------------------------------------------------------------------- #

def test_to_markdown_renders_advisory_report():
    findings = _weak_findings()
    proposals = propose(findings)
    md = to_markdown(findings, proposals)
    assert "Self-Improvement Proposals" in md
    assert "ADVISORY ONLY" in md or "advisory only" in md.lower()
    assert "high_value" in md            # the failing template surfaces
    assert "auto_applicable" in md or "auto-applicable" in md or "human-review" in md
    for p in proposals:
        assert p.area in md
