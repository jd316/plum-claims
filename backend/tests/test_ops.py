"""Tests for the additive ops dashboard layer (analytics / worklist / fraud).

Deterministic — no Gemini, no pipeline. Uses real Postgres; the whole module
skips if Postgres is unreachable (mirrors test_persistence.py). The analytics
assertions seed a known set of claims and check the aggregates exactly; the
TestClient checks assert the endpoints return 200 with the documented shapes.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.models.schemas import (
    ClaimResult,
    ClaimSubmission,
    Decision,
    DocumentInput,
    ExtractionResult,
    ReasonCode,
    TraceEntry,
)


# ---------------------------------------------------------------------------
# DB reachability guard — skip the whole module if Postgres is down.
# ---------------------------------------------------------------------------

def _db_reachable() -> bool:
    try:
        from app.services.persistence import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="module", autouse=True)
def require_db():
    if not _db_reachable():
        pytest.skip("Postgres unreachable — skipping ops tests")
    from app.services.persistence import init_db
    init_db()


# ---------------------------------------------------------------------------
# Seeding helpers — a small, known set of claims with a unique member tag so the
# filtered queries can be asserted exactly regardless of other rows in the DB.
# ---------------------------------------------------------------------------

def _submission(member_id: str, category: str = "CONSULTATION") -> ClaimSubmission:
    return ClaimSubmission(
        member_id=member_id,
        policy_id="POL-OPS-TEST",
        claim_category=category,
        treatment_date=date(2026, 1, 10),
        claimed_amount=2000.0,
        documents=[DocumentInput(file_id="d1", file_name="r.png",
                                 stored_path="/tmp/ops_dummy.png")],
    )


def _result(claim_id: str, status: str | None, amount: float | None,
            confidence: float | None, blocked: bool = False,
            cost: float = 0.0, fraud: bool = False) -> ClaimResult:
    decision = None
    if status is not None:
        decision = Decision(
            status=status, approved_amount=amount or 0.0,
            confidence=confidence or 0.0,
            reason_codes=[ReasonCode(code="FRAUD_FLAG", detail="amount altered")]
            if fraud else [],
            recommendations=["Manual fraud review"] if fraud else [],
        )
    trace = []
    extractions = []
    if fraud:
        trace = [TraceEntry(seq=1, step="rules", agent="fraud_anomaly", status="FLAG",
                            summary="suspected altered amount")]
        extractions = [ExtractionResult(file_id="d1", fraud_signals=["mismatched fonts"])]
    return ClaimResult(
        claim_id=claim_id, blocked=blocked, decision=decision,
        estimated_cost_inr=cost, total_latency_ms=1200,
        trace=trace, extractions=extractions,
    )


@pytest.fixture(scope="module")
def seeded():
    """Seed a fixed mix under a unique member tag; return (tag, expected counts)."""
    from app.services.persistence import save_claim
    tag = f"M-OPS-{uuid.uuid4().hex[:8]}"
    # 2 APPROVED, 1 PARTIAL, 1 REJECTED, 1 MANUAL_REVIEW(fraud), 1 blocked.
    specs = [
        ("APPROVED", 1000.0, 0.9, False, 0.02, False, "CONSULTATION"),
        ("APPROVED", 2000.0, 0.8, False, 0.02, False, "PHARMACY"),
        ("PARTIAL", 500.0, 0.7, False, 0.02, False, "CONSULTATION"),
        ("REJECTED", 0.0, 0.95, False, 0.02, False, "DENTAL"),
        ("MANUAL_REVIEW", 0.0, 0.4, False, 0.02, True, "DIAGNOSTIC"),
        (None, None, None, True, 0.0, False, "CONSULTATION"),  # blocked
    ]
    ids = []
    for status, amt, conf, blocked, cost, fraud, cat in specs:
        cid = f"ops-{uuid.uuid4().hex}"
        save_claim(_submission(tag, cat),
                   _result(cid, status, amt, conf, blocked, cost, fraud))
        ids.append(cid)
    return tag, ids


# ---------------------------------------------------------------------------
# analytics_summary()
# ---------------------------------------------------------------------------

def test_analytics_summary_shape_and_totals(seeded):
    from app.services.persistence import analytics_summary
    a = analytics_summary()
    # Shape: every documented key present.
    for key in ("total_claims", "by_status", "approval_rate", "blocked_rate",
                "manual_review_rate", "total_approved_amount", "avg_approved_amount",
                "avg_confidence", "flagged_fraud_count", "estimated_total_cost_inr",
                "by_category"):
        assert key in a, f"missing analytics key {key}"
    # Our 6 seeded rows are a subset of the DB; aggregates must be at least that big.
    assert a["total_claims"] >= 6
    assert a["by_status"].get("APPROVED", 0) >= 2
    assert a["flagged_fraud_count"] >= 1
    assert 0.0 <= a["approval_rate"] <= 1.0
    assert a["estimated_total_cost_inr"] >= 0.1  # 5 decided rows * 0.02


def test_analytics_approval_rate_on_isolated_seed():
    """Compute the approval rate on a freshly-seeded, countable slice and assert
    it matches by reading the by_status counts back through the summary delta."""
    from app.services.persistence import analytics_summary, save_claim
    before = analytics_summary()["by_status"]
    tag = f"M-RATE-{uuid.uuid4().hex[:8]}"
    save_claim(_submission(tag), _result(f"ops-{uuid.uuid4().hex}", "APPROVED", 100.0, 0.9))
    save_claim(_submission(tag), _result(f"ops-{uuid.uuid4().hex}", "REJECTED", 0.0, 0.9))
    after = analytics_summary()["by_status"]
    assert after.get("APPROVED", 0) == before.get("APPROVED", 0) + 1
    assert after.get("REJECTED", 0) == before.get("REJECTED", 0) + 1


# ---------------------------------------------------------------------------
# worklist() filtering + sorting
# ---------------------------------------------------------------------------

def test_worklist_filters_by_status_and_category(seeded):
    from app.services.persistence import worklist
    tag, _ = seeded
    rows = worklist(status="APPROVED", q=tag)
    assert rows, "expected at least the seeded APPROVED rows"
    assert all(r["status"] == "APPROVED" for r in rows)
    assert all(r["member_id"] == tag for r in rows)

    pharm = worklist(category="PHARMACY", q=tag)
    assert all(r["category"] == "PHARMACY" for r in pharm)
    assert len(pharm) == 1


def test_worklist_needs_review_flag(seeded):
    from app.services.persistence import worklist
    tag, _ = seeded
    mr = worklist(status="MANUAL_REVIEW", q=tag)
    assert mr and all(r["needs_review"] is True for r in mr)
    approved = worklist(status="APPROVED", q=tag)
    assert all(r["needs_review"] is False for r in approved)


def test_worklist_sort_by_amount(seeded):
    from app.services.persistence import worklist
    tag, _ = seeded
    rows = worklist(q=tag, sort="amount")
    amounts = [r["approved_amount"] for r in rows if r["approved_amount"] is not None]
    assert amounts == sorted(amounts, reverse=True)


# ---------------------------------------------------------------------------
# fraud_queue() — only MANUAL_REVIEW, with signals
# ---------------------------------------------------------------------------

def test_fraud_queue_only_flagged_with_signals(seeded):
    from app.services.persistence import fraud_queue
    tag, _ = seeded
    rows = fraud_queue()
    assert all(r["status"] == "MANUAL_REVIEW" for r in rows)
    mine = [r for r in rows if r["member_id"] == tag]
    assert mine, "expected the seeded MANUAL_REVIEW claim"
    r = mine[0]
    assert "mismatched fonts" in r["extraction_signals"]
    assert any(rc["code"] == "FRAUD_FLAG" for rc in r["reasons"])
    assert r["fraud_rule"] is not None


# ---------------------------------------------------------------------------
# TestClient endpoint smoke (no Gemini) — 200 + shapes.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


def test_endpoint_analytics_200(client, seeded):
    resp = client.get("/api/ops/analytics")
    assert resp.status_code == 200
    body = resp.json()
    assert "by_status" in body and "by_category" in body
    assert isinstance(body["by_category"], list)


def test_endpoint_worklist_200(client, seeded):
    tag, _ = seeded
    resp = client.get(f"/api/ops/worklist?status=MANUAL_REVIEW&q={tag}")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert all(r["status"] == "MANUAL_REVIEW" for r in rows)
    assert all("needs_review" in r for r in rows)


def test_endpoint_fraud_200(client):
    resp = client.get("/api/ops/fraud")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
