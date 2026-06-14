"""Tests for the policy-as-code studio (policy_store + preview).

Deterministic — no Gemini, no pipeline. Uses real Postgres; the whole module skips
if Postgres is unreachable (mirrors test_ops.py / test_persistence.py).

EVAL-SAFETY: these tests touch the live policy file (settings.policy_path) only inside
the activate test, which captures the original bytes up-front and RESTORES them (by
re-activating v1) before finishing. A module-level autouse fixture additionally snapshots
+ restores the file as a backstop, so the repo's policy_terms.json is NEVER left modified
regardless of which assertion fails.
"""
from __future__ import annotations

import copy
import json

import pytest

from app.config import settings
from app.services import policy_store
from app.services.preview_sample import from_test_case, from_inline


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
        pytest.skip("Postgres unreachable — skipping policy studio tests")
    from app.services.persistence import init_db
    init_db()
    policy_store.seed_initial_version()


@pytest.fixture(autouse=True)
def restore_policy_file():
    """Backstop: snapshot the live policy file bytes before each test and restore them
    after, so a mid-test failure can never leave policy_terms.json modified on disk."""
    with open(settings.policy_path, "rb") as f:
        original = f.read()
    try:
        yield
    finally:
        with open(settings.policy_path, "wb") as f:
            f.write(original)
        from app.services.policy_engine import invalidate_policy_cache
        invalidate_policy_cache(settings.policy_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_policy() -> dict:
    with open(settings.policy_path) as f:
        return json.load(f)


def _consultation_copay_candidate(copay: int) -> dict:
    """A clone of the current policy with consultation copay_percent set to `copay`."""
    p = copy.deepcopy(_current_policy())
    p["opd_categories"]["consultation"]["copay_percent"] = copay
    return p


# ---------------------------------------------------------------------------
# seed_initial_version
# ---------------------------------------------------------------------------

def test_seed_initial_version_is_v1_active_and_idempotent():
    active = policy_store.get_active()
    assert active is not None
    # v1 is active and equals the original file content.
    versions = policy_store.list_versions()
    assert any(v["version_no"] == 1 and v["is_active"] for v in versions)
    assert active["policy_json"]["policy_id"] == _current_policy()["policy_id"]

    # Idempotent: a second seed inserts nothing new.
    before = len(policy_store.list_versions())
    assert policy_store.seed_initial_version() is None
    assert len(policy_store.list_versions()) == before


# ---------------------------------------------------------------------------
# create_version + validation
# ---------------------------------------------------------------------------

def test_create_version_stores_inactive():
    candidate = _consultation_copay_candidate(20)
    row = policy_store.create_version(candidate, label="copay 20% test", actor="tester")
    assert row["is_active"] is False
    assert row["label"] == "copay 20% test"
    assert row["policy_json"]["opd_categories"]["consultation"]["copay_percent"] == 20
    # The active version is unaffected.
    assert policy_store.get_active()["version_no"] == 1


def test_create_version_rejects_missing_required_keys():
    bad = {"coverage": {}, "opd_categories": {}}  # missing waiting_periods, exclusions, ...
    with pytest.raises(policy_store.PolicyValidationError) as ei:
        policy_store.create_version(bad, label="bad")
    # Error names a missing key for a clear 422.
    assert "missing required key" in str(ei.value)
    assert "members" in str(ei.value)


# ---------------------------------------------------------------------------
# Preview (read-only) — the headline before/after proof.
# ---------------------------------------------------------------------------

def test_preview_tc004_copay_20_gives_1200_without_touching_file():
    """TC004 pays ₹1,350 under the active policy (10% consultation copay) and ₹1,200
    under a candidate with copay 20% — and the preview must NOT modify the live file."""
    with open(settings.policy_path, "rb") as f:
        before_bytes = f.read()

    candidate = _consultation_copay_candidate(20)
    result = policy_store.preview_decision(candidate, from_test_case("TC004"))

    assert result["before"]["status"] == "APPROVED"
    assert result["after"]["status"] == "APPROVED"
    assert abs(result["before"]["approved_amount"] - 1350.0) < 0.01
    assert abs(result["after"]["approved_amount"] - 1200.0) < 0.01
    assert result["changed"] is True

    # The live policy file is byte-identical after the preview (read-only guarantee).
    with open(settings.policy_path, "rb") as f:
        assert f.read() == before_bytes


def test_preview_inline_sample_runs():
    candidate = _consultation_copay_candidate(20)
    sample = {"member_id": "EMP001", "claim_category": "CONSULTATION",
              "claimed_amount": 1500, "hospital_name": "City Clinic",
              "line_items": [{"description": "Consultation Fee", "amount": 1500}]}
    result = policy_store.preview_decision(candidate, from_inline(sample))
    assert abs(result["before"]["approved_amount"] - 1350.0) < 0.01
    assert abs(result["after"]["approved_amount"] - 1200.0) < 0.01


# ---------------------------------------------------------------------------
# activate_version — writes file + flips is_active + invalidates cache, then RESTORE.
# ---------------------------------------------------------------------------

def test_activate_writes_file_flips_active_then_restore_v1():
    from app.services.policy_engine import get_policy_engine

    with open(settings.policy_path, "rb") as f:
        original_bytes = f.read()

    # The seeded v1 (active, original).
    v1 = next(v for v in policy_store.list_versions() if v["version_no"] == 1)

    # Create + activate a copay-20 candidate.
    candidate = _consultation_copay_candidate(20)
    new_row = policy_store.create_version(candidate, label="activate-test", actor="tester")
    meta = policy_store.activate_version(new_row["id"], actor="tester")
    assert meta["is_active"] is True

    # File now reflects the candidate, and the live engine re-reads copay 20.
    on_disk = _current_policy()
    assert on_disk["opd_categories"]["consultation"]["copay_percent"] == 20
    engine = get_policy_engine(settings.policy_path)
    assert engine.category_rules("CONSULTATION")["copay_percent"] == 20

    # Exactly one active version, and it's the new one.
    actives = [v for v in policy_store.list_versions() if v["is_active"]]
    assert len(actives) == 1 and actives[0]["id"] == new_row["id"]

    # RESTORE: re-activate v1 so the original file is intact at test end.
    policy_store.activate_version(v1["id"], actor="tester")
    with open(settings.policy_path, "rb") as f:
        assert f.read() == original_bytes
    engine = get_policy_engine(settings.policy_path)
    assert engine.category_rules("CONSULTATION")["copay_percent"] == 10


# ---------------------------------------------------------------------------
# diff_versions
# ---------------------------------------------------------------------------

def test_diff_versions_reports_changed_leaf_path():
    v1 = next(v for v in policy_store.list_versions() if v["version_no"] == 1)
    candidate = _consultation_copay_candidate(20)
    new_row = policy_store.create_version(candidate, label="diff-test")
    diff = policy_store.diff_versions(v1["id"], new_row["id"])
    changes = {c["path"]: c for c in diff["changes"]}
    path = "opd_categories.consultation.copay_percent"
    assert path in changes
    assert changes[path]["before"] == 10
    assert changes[path]["after"] == 20
    assert changes[path]["change"] == "changed"
