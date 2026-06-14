"""Tests for the asynchronous claim-processing path (Celery + Redis).

Two tiers:
  * DETERMINISTIC (default, no broker, no Gemini): the Celery app + task import
    cleanly and the task is registered. These never touch Redis.
  * LIVE (@pytest.mark.live): the task BODY runs the real run_claim pipeline on a
    rendered TC004 fixture and must return APPROVED 1350. This is a live-Gemini
    call but does NOT require a running broker — we call the task function directly
    (process_claim_task.run / the underlying body), exercising the same code a
    worker would run. The true broker round-trip is covered manually (see README /
    the verify steps) and skipped in CI.
"""
import json

import pytest

from app.fixtures.loader import load_cases, case_to_submission
from app.fixtures.renderer import render_case_documents
from tests.conftest import REPO_ROOT


# --------------------------------------------------------------------------- #
# Deterministic — import & registration. No broker, no network.
# --------------------------------------------------------------------------- #

def test_celery_app_imports_without_broker():
    """Importing app.worker must not require Redis to be up (lazy connection)."""
    import app.worker as w
    assert w.celery_app.main == "plum"
    # Broker/result-backend default to redis_url via config.
    assert w.celery_app.conf.broker_url
    assert w.celery_app.conf.result_backend


def test_main_imports_without_broker():
    """Importing app.main (which references the worker lazily) must be broker-free."""
    import app.main  # noqa: F401


def test_process_claim_task_is_registered():
    import app.worker as w
    assert "app.worker.process_claim_task" in w.celery_app.tasks
    # The task is a normal callable with .delay / .apply_async / .run.
    task = w.celery_app.tasks["app.worker.process_claim_task"]
    assert hasattr(task, "delay") and hasattr(task, "run")


def test_async_endpoints_registered():
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/claims/async" in paths
    assert "/api/jobs/{job_id}" in paths
    # The sync endpoint is untouched / still present.
    assert "/api/claims" in paths


def _tc004_submission(tmp_path):
    case = next(c for c in load_cases(str(REPO_ROOT / "test_cases.json"))
                if c["case_id"] == "TC004")
    paths = render_case_documents(case, str(tmp_path / "TC004"))
    return case_to_submission(case, paths)


# --------------------------------------------------------------------------- #
# Live — task body runs the real pipeline (no broker needed).
# --------------------------------------------------------------------------- #

@pytest.mark.live
def test_process_claim_task_body_approves_tc004(tmp_path):
    """Call the task function directly (synchronously) on a rendered TC004 fixture.
    This exercises the exact run_claim/state_to_result/save_claim path a worker runs
    and must yield APPROVED 1350 — proving the async task body is correct without a
    broker round-trip."""
    from app.worker import process_claim_task

    sub = _tc004_submission(tmp_path)
    submission_json = sub.model_dump(mode="json")
    upload_paths = {d.file_id: d.stored_path for d in sub.documents}

    # .run() invokes the task body synchronously (no broker / no worker pool).
    result = process_claim_task.run(submission_json, "CLM-async-test", upload_paths)

    assert isinstance(result, dict)
    assert result["claim_id"] == "CLM-async-test"
    assert result["decision"]["status"] == "APPROVED"
    assert result["decision"]["approved_amount"] == 1350
    assert result["trace"]


@pytest.mark.live
def test_async_endpoint_fallback_or_queue(tmp_path):
    """POST /api/claims/async either queues (broker up) or gracefully processes
    synchronously (broker down). Both outcomes are valid; with no worker/Redis in
    CI we expect the sync fallback to return a completed APPROVED 1350 result."""
    from fastapi.testclient import TestClient
    from app.main import app

    sub = _tc004_submission(tmp_path)
    inp = {
        "member_id": sub.member_id, "policy_id": sub.policy_id,
        "claim_category": sub.claim_category,
        "treatment_date": sub.treatment_date.isoformat(),
        "claimed_amount": sub.claimed_amount,
        "ytd_claims_amount": sub.ytd_claims_amount,
    }
    files = [("files", (f"{d.file_id}.png", open(d.stored_path, "rb"), "image/png"))
             for d in sub.documents]
    with TestClient(app) as client:
        r = client.post("/api/claims/async",
                        data={"payload": json.dumps(inp)}, files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("queued", "completed")
    if body["status"] == "completed":
        # Broker-down graceful fallback path.
        assert body.get("fallback") == "sync"
        assert body["result"]["decision"]["status"] == "APPROVED"
        assert body["result"]["decision"]["approved_amount"] == 1350
