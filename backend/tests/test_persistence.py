"""Tests for app/services/persistence.py — uses real Postgres, NOT marked live."""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.models.schemas import (
    ClaimSubmission,
    ClaimResult,
    Decision,
    DocumentInput,
    DocumentProblem,
    ReasonCode,
)


# ---------------------------------------------------------------------------
# DB reachability guard — skip the entire module if Postgres is unreachable.
# ---------------------------------------------------------------------------

def _db_reachable() -> bool:
    try:
        from app.services.persistence import engine
        with engine.connect() as conn:
            from sqlalchemy import text
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="module", autouse=True)
def require_db():
    if not _db_reachable():
        pytest.skip("Postgres unreachable — skipping persistence tests")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_submission(member_id: str = "M-TEST-001") -> ClaimSubmission:
    return ClaimSubmission(
        member_id=member_id,
        policy_id="POL-TEST-001",
        claim_category="CONSULTATION",
        treatment_date=date(2026, 1, 15),
        claimed_amount=1500.00,
        documents=[
            DocumentInput(
                file_id="doc-001",
                file_name="receipt.png",
                stored_path="/tmp/dummy_receipt.png",
            )
        ],
    )


def _make_result(claim_id: str, blocked: bool = False) -> ClaimResult:
    if blocked:
        return ClaimResult(
            claim_id=claim_id,
            blocked=True,
            decision=None,
            problems=[
                DocumentProblem(
                    kind="UNREADABLE_DOCUMENT",
                    file_id="doc-001",
                    message="Document could not be read",
                )
            ],
        )
    return ClaimResult(
        claim_id=claim_id,
        blocked=False,
        decision=Decision(
            status="APPROVED",
            approved_amount=1200.00,
            confidence=0.95,
            reason_codes=[ReasonCode(code="OK", detail="All checks passed")],
            member_message="Your claim has been approved.",
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_init_db_is_idempotent():
    """init_db() must be callable multiple times without error."""
    from app.services.persistence import init_db
    init_db()
    init_db()  # second call — must not raise


def test_save_and_get_claim():
    """save_claim returns the claim_id and get_claim retrieves the correct record."""
    from app.services.persistence import init_db, save_claim, get_claim

    init_db()

    claim_id = f"test-{uuid.uuid4().hex}"
    sub = _make_submission()
    result = _make_result(claim_id)

    returned_id = save_claim(sub, result)

    assert returned_id == claim_id

    record = get_claim(claim_id)
    assert record is not None, "get_claim should return a dict for a saved claim"
    assert isinstance(record, dict)
    # The stored result is the full ClaimResult dict; decision.status must match.
    assert record["decision"]["status"] == "APPROVED"
    assert record["claim_id"] == claim_id


def test_get_claim_missing_returns_none():
    """get_claim with a non-existent id must return None."""
    from app.services.persistence import get_claim

    assert get_claim("does-not-exist-xyz-abc-123") is None


def test_list_claims_contains_saved_claim():
    """list_claims must include a previously saved claim_id."""
    from app.services.persistence import init_db, save_claim, list_claims

    init_db()

    claim_id = f"test-{uuid.uuid4().hex}"
    sub = _make_submission(member_id="M-LIST-TEST")
    result = _make_result(claim_id)
    save_claim(sub, result)

    claims = list_claims()
    assert isinstance(claims, list)

    ids = [c["claim_id"] for c in claims]
    assert claim_id in ids, f"Expected {claim_id} in list_claims output"


def test_list_claims_created_at_is_iso_string():
    """created_at in list_claims must be an ISO-format string (contains 'T').

    This guards the Wave-1 isoformat fix — if created_at were returned as a
    raw datetime object the JSON serialiser would fail and the API would break.
    """
    from app.services.persistence import init_db, save_claim, list_claims

    init_db()

    claim_id = f"test-{uuid.uuid4().hex}"
    save_claim(_make_submission(), _make_result(claim_id))

    claims = list_claims()
    # Find our record (most recent should be at the front but search to be safe).
    row = next((c for c in claims if c["claim_id"] == claim_id), None)
    assert row is not None
    created_at = row["created_at"]
    assert isinstance(created_at, str), f"created_at must be a str, got {type(created_at)}"
    assert "T" in created_at, f"created_at must be ISO format (contains 'T'), got {created_at!r}"


def test_blocked_claim_round_trip():
    """A blocked claim (decision=None) must round-trip correctly."""
    from app.services.persistence import init_db, save_claim, get_claim

    init_db()

    claim_id = f"test-{uuid.uuid4().hex}"
    sub = _make_submission()
    result = _make_result(claim_id, blocked=True)

    save_claim(sub, result)

    record = get_claim(claim_id)
    assert record is not None
    assert record["blocked"] is True
    assert record["decision"] is None
    # problems list must survive the JSON round-trip
    assert len(record["problems"]) == 1
    assert record["problems"][0]["kind"] == "UNREADABLE_DOCUMENT"
