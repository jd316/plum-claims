"""Tests for the message-quality LLM-as-judge eval (app/evalrunner/message_quality.py).

Deterministic tests need no Gemini: they exercise the MessageGrade mean, message
selection, aggregation, and markdown rendering with hand-built objects. The live test
(@pytest.mark.live) makes a couple of judge calls and asserts a clearly-good message
out-scores a deliberately-bad one (a relative assertion, robust to judge variance)."""
import pytest

from app.evalrunner import message_quality as mq
from app.evalrunner.message_quality import (
    DIMENSIONS,
    MessageGrade,
    grade_claim_messages,
    run_message_quality_eval,
    to_markdown,
)
from app.models.schemas import ClaimResult, Decision, DocumentProblem, ReasonCode


# --------------------------------------------------------------------------- #
# Deterministic                                                               #
# --------------------------------------------------------------------------- #

def test_message_grade_overall_is_mean_of_dimensions():
    g = MessageGrade(specificity=5, actionability=4, correctness=3, tone=2,
                     jargon_free=1, rationale="x")
    assert g.overall == pytest.approx((5 + 4 + 3 + 2 + 1) / 5)  # 3.0


def test_message_grade_overall_recomputed_even_if_model_supplies_wrong_value():
    # A judge that returns an inconsistent overall must not be trusted.
    g = MessageGrade(specificity=4, actionability=4, correctness=4, tone=4,
                     jargon_free=4, overall=1.0)
    assert g.overall == pytest.approx(4.0)


def test_grade_claim_messages_blocked_selects_problem_message(monkeypatch):
    captured = {}

    def fake_grade(context, message):
        captured["context"] = context
        captured["message"] = message
        return MessageGrade(specificity=5, actionability=5, correctness=5, tone=5,
                            jargon_free=5, rationale="ok")

    monkeypatch.setattr(mq, "grade_message", fake_grade)
    cr = ClaimResult(
        claim_id="C1", blocked=True,
        problems=[DocumentProblem(kind="MISSING_DOCUMENT", file_id="F1",
                                  message="Missing HOSPITAL_BILL. Please upload it.")],
        decision=None)
    out = grade_claim_messages(cr)
    assert captured["message"] == "Missing HOSPITAL_BILL. Please upload it."
    assert "BLOCKED" in captured["context"]
    assert out["overall"] == pytest.approx(5.0)
    assert all(out[d] == 5 for d in DIMENSIONS)


def test_grade_claim_messages_decided_selects_member_message_plus_details(monkeypatch):
    captured = {}

    def fake_grade(context, message):
        captured["context"] = context
        captured["message"] = message
        return MessageGrade(specificity=4, actionability=3, correctness=5, tone=4,
                            jargon_free=4, rationale="ok")

    monkeypatch.setattr(mq, "grade_message", fake_grade)
    cr = ClaimResult(
        claim_id="C2", blocked=False,
        decision=Decision(status="REJECTED", approved_amount=0.0,
                          member_message="Your claim was rejected.",
                          reason_codes=[ReasonCode(code="WAITING_PERIOD",
                                                   detail="Treatment fell in the waiting period.")]))
    out = grade_claim_messages(cr)
    # member_message AND the reason-code detail are both part of the graded text.
    assert "Your claim was rejected." in captured["message"]
    assert "waiting period" in captured["message"]
    assert "REJECTED" in captured["context"]
    assert out["overall"] == pytest.approx((4 + 3 + 5 + 4 + 4) / 5)


def test_aggregate_computes_correct_means():
    per_case = [
        {"specificity": 5, "actionability": 5, "correctness": 5, "tone": 5,
         "jargon_free": 5, "overall": 5.0},
        {"specificity": 1, "actionability": 3, "correctness": 1, "tone": 3,
         "jargon_free": 1, "overall": 1.8},
    ]
    agg = mq._aggregate(per_case)
    assert agg["specificity"] == pytest.approx(3.0)
    assert agg["actionability"] == pytest.approx(4.0)
    assert agg["correctness"] == pytest.approx(3.0)
    assert agg["overall"] == pytest.approx((5.0 + 1.8) / 2)


def test_aggregate_empty_is_zero():
    agg = mq._aggregate([])
    assert all(agg[k] == 0.0 for k in [*DIMENSIONS, "overall"])


def test_run_message_quality_eval_with_sample_does_not_run_pipeline(monkeypatch):
    monkeypatch.setattr(mq, "grade_message", lambda context, message: MessageGrade(
        specificity=4, actionability=4, correctness=4, tone=4, jargon_free=4))
    sample = [
        ClaimResult(claim_id="C1", blocked=True,
                    problems=[DocumentProblem(kind="MISSING_DOCUMENT",
                                              message="Missing HOSPITAL_BILL.")]),
        ClaimResult(claim_id="C2", blocked=False,
                    decision=Decision(status="APPROVED", approved_amount=1350.0,
                                      member_message="Approved INR 1,350.00.")),
    ]
    result = run_message_quality_eval(sample=sample)
    assert result["n"] == 2 and result["n_total"] == 2
    assert result["aggregate"]["overall"] == pytest.approx(4.0)
    assert {c["case_id"] for c in result["per_case"]} == {"C1", "C2"}


def test_to_markdown_renders_without_error():
    result = {
        "n": 1, "n_total": 1,
        "aggregate": {d: 4.0 for d in DIMENSIONS} | {"overall": 4.0},
        "per_case": [{
            "case_id": "TC001", "case_name": "Wrong Document",
            **{d: 4 for d in DIMENSIONS}, "overall": 4.0,
            "rationale": "names the missing doc", "message": "Missing HOSPITAL_BILL.",
            "context": "BLOCKED",
        }],
    }
    md = to_markdown(result)
    assert "# Message-Quality Eval Report" in md
    assert "TC001" in md
    assert "specificity" in md
    assert "Overall mean" in md


def test_to_markdown_handles_errored_case():
    result = {
        "n": 0, "n_total": 1,
        "aggregate": {d: 0.0 for d in DIMENSIONS} | {"overall": 0.0},
        "per_case": [{"case_id": "TCX", "case_name": "Boom", "errored": True}],
    }
    md = to_markdown(result)
    assert "errored" in md.lower()


# --------------------------------------------------------------------------- #
# Live (LLM judge)                                                            #
# --------------------------------------------------------------------------- #

@pytest.mark.live
def test_good_message_outscores_bad_message():
    good = ("For a CONSULTATION claim we need PRESCRIPTION and HOSPITAL_BILL. You "
            "uploaded PRESCRIPTION. Missing HOSPITAL_BILL — please upload the "
            "HOSPITAL_BILL and resubmit.")
    bad = "Your claim could not be processed."
    context = ("Decision status: BLOCKED. Problem kind: MISSING_DOCUMENT. The member is "
               "missing the HOSPITAL_BILL document required for a CONSULTATION claim.")

    g_good = mq.grade_message(context, good)
    g_bad = mq.grade_message(context, bad)

    # The good message is specific and actionable; the bad one is neither.
    assert g_good.specificity >= 4
    assert g_good.actionability >= 4
    assert g_bad.specificity <= 3
    assert g_bad.actionability <= 3
    # Relative assertion — robust to judge variance.
    assert g_good.overall > g_bad.overall
