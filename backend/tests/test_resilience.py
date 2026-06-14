"""Deterministic resilience tests (no live Gemini). Prove the hardening from the audit wave:
nodes never crash, degraded semantic mapping doesn't silently weaken enforcement, and an
empty-line-item bill doesn't get auto-approved for ₹0.
"""
from datetime import date

from app.graph import nodes
from app.rules.aggregator import aggregate
from app.models.schemas import (ClaimSubmission, DocumentInput, ExtractionResult, NumField, RuleVerdict, FinancialBreakdown,
                                 ComponentFailure, LineItem)


def _submission(category="CONSULTATION", claimed=1000.0):
    return ClaimSubmission(member_id="EMP001", policy_id="PLUM_GHI_2024", claim_category=category,
                           treatment_date=date(2025, 1, 15), claimed_amount=claimed,
                           documents=[DocumentInput(file_id="F001", stored_path="/tmp/x.png")])


def _passing_verdicts():
    return [RuleVerdict(rule=r, status="PASS") for r in
            ("waiting_period", "coverage_exclusion", "pre_auth", "limits", "fraud_anomaly")]


# ---------- FIX 1: nodes never raise ----------

def test_aggregate_tolerates_none_financial():
    # decide() passes state.get("financial") which can be None if financial_calc degraded.
    d = aggregate(_passing_verdicts(), None, auto_review_above=25000)
    assert d.status == "MANUAL_REVIEW" and d.approved_amount == 0.0


def test_financial_calc_degrades_instead_of_crashing(monkeypatch):
    # Force the pure calculator to blow up; the node must return a zeroed breakdown + failure,
    # not propagate the exception.
    def boom(*a, **k): raise RuntimeError("calc exploded")
    monkeypatch.setattr(nodes, "calculate", boom)
    state = {"submission": _submission(),
             "extractions": [ExtractionResult(file_id="F001", doc_type="HOSPITAL_BILL",
                                              line_items=[LineItem(description="X", amount=100)])],
             "rule_verdicts": _passing_verdicts()}
    out = nodes.financial_calc(state)
    assert isinstance(out["financial"], FinancialBreakdown)
    assert out["financial"].approved_amount == 0.0
    assert out["financial"].steps == ["financial calculation failed"]
    assert any(f.agent == "financial" for f in out["failures"])
    assert any(t.degraded for t in out["trace"])


def test_decide_degrades_instead_of_crashing(monkeypatch):
    # Force aggregate to raise; decide() must still produce a valid MANUAL_REVIEW decision.
    def boom(*a, **k): raise RuntimeError("aggregate exploded")
    monkeypatch.setattr(nodes, "aggregate", boom)
    out = nodes.decide({"rule_verdicts": _passing_verdicts(),
                        "financial": FinancialBreakdown(gross=0, approved_amount=0, steps=[])})
    assert out["decision"].status == "MANUAL_REVIEW"
    assert any(f.agent == "decide" for f in out["failures"])


def test_run_claim_never_raises(monkeypatch):
    # If the graph itself raises, run_claim returns a graceful degraded state — never 500.
    import app.graph.build as build
    def boom(*a, **k): raise RuntimeError("graph exploded")
    monkeypatch.setattr(build.GRAPH, "invoke", boom)
    state = build.run_claim(_submission())
    assert state["decision"].status == "MANUAL_REVIEW"
    assert state["decision"].approved_amount == 0.0
    assert any(f.agent == "pipeline" for f in state["failures"])
    assert any(t.status == "ERROR" and t.degraded for t in state["trace"])


# ---------- FIX 2: degraded semantic_map must not silently approve ----------

def test_semantic_failure_flags_waiting_period():
    # waiting_period would PASS, but with a recorded semantic_map failure it must FLAG.
    state = {"submission": _submission(), "member": {"name": "Rajesh Kumar",
                                                     "join_date": "2024-04-01"},
             "extractions": [ExtractionResult(file_id="F001")],
             "failures": [ComponentFailure(agent="semantic_map", failure_mode="llm down")]}
    out = nodes._RULE_NODES["waiting_period"](state)
    v = out["rule_verdicts"][0]
    assert v.status == "FLAG" and v.reason_code == "SEMANTIC_MAPPING_UNAVAILABLE"


def test_semantic_failure_routes_manual_review():
    # End-to-end at the aggregator: a FLAG from the semantic-unavailable conversion routes
    # to MANUAL_REVIEW rather than APPROVED.
    verdicts = [RuleVerdict(rule="waiting_period", status="FLAG",
                            reason_code="SEMANTIC_MAPPING_UNAVAILABLE", detail="mapping failed"),
                RuleVerdict(rule="coverage_exclusion", status="PASS"),
                RuleVerdict(rule="pre_auth", status="PASS"),
                RuleVerdict(rule="limits", status="PASS"),
                RuleVerdict(rule="fraud_anomaly", status="PASS")]
    fb = FinancialBreakdown(gross=1000, approved_amount=900,
                            line_items=[], steps=["x"])
    d = aggregate(verdicts, fb, auto_review_above=25000)
    assert d.status == "MANUAL_REVIEW"


def test_semantic_present_still_passes():
    # Normal path (no semantic failure) must be unchanged — waiting_period PASSes.
    state = {"submission": _submission(), "member": {"name": "Rajesh Kumar",
                                                     "join_date": "2024-04-01"},
             "extractions": [ExtractionResult(file_id="F001")]}
    out = nodes._RULE_NODES["waiting_period"](state)
    assert out["rule_verdicts"][0].status == "PASS"


# ---------- FIX 3: empty line items must not yield APPROVED ₹0 ----------

def test_empty_items_falls_back_to_bill_total():
    # A bill with only a total (no line items) should compute payout on that total.
    state = {"submission": _submission(claimed=2000.0),
             "extractions": [ExtractionResult(file_id="F001", doc_type="HOSPITAL_BILL",
                                              line_items=[], total_amount=NumField(value=2000.0))],
             "rule_verdicts": _passing_verdicts()}
    out = nodes.financial_calc(state)
    fb = out["financial"]
    assert fb.line_items and fb.line_items[0].description == "Claimed amount"
    assert fb.approved_amount > 0


def test_empty_items_falls_back_to_claimed_amount():
    # No line items and no bill total → fall back to the submitted claimed_amount.
    state = {"submission": _submission(claimed=1500.0),
             "extractions": [ExtractionResult(file_id="F001", doc_type="HOSPITAL_BILL",
                                              line_items=[], total_amount=NumField(value=None))],
             "rule_verdicts": _passing_verdicts()}
    out = nodes.financial_calc(state)
    assert out["financial"].approved_amount > 0


def test_no_amount_routes_manual_review_not_approved_zero():
    # No line items, no total, no claimed amount: aggregator routes to MANUAL_REVIEW, never APPROVED ₹0.
    fb = FinancialBreakdown(gross=0.0, approved_amount=0.0, line_items=[], steps=["x"])
    d = aggregate(_passing_verdicts(), fb, auto_review_above=25000)
    assert d.status == "MANUAL_REVIEW" and d.approved_amount == 0.0
