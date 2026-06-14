"""Celery worker for asynchronous claim processing.

This is the credible-scale path: claim processing (a multi-second, Gemini-bound
pipeline) runs off the request thread in a worker pool, with a job-status API the
UI can poll. It is ADDITIVE — the synchronous POST /api/claims is unchanged and
the eval calls run_claim directly.

IMPORTANT: this module is import-safe. Constructing the Celery app does NOT open a
connection to the broker, so `import app.worker` (and therefore `import app.main`)
works with Redis down — connections are established lazily on .delay()/worker boot.
"""
import logging

from celery import Celery

from app.config import settings
from app.graph.build import run_claim
from app.evalrunner.runner import state_to_result
from app.models.schemas import ClaimResult, ClaimSubmission
from app.services import persistence

log = logging.getLogger("plum.worker")

celery_app = Celery(
    "plum",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
# Keep result metadata around long enough for the UI to poll; serialize as JSON.
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=86400,  # 24h
    task_track_started=True,  # surface a STARTED state to the job-status API
    # Fail fast when the broker is down so the API's graceful sync fallback kicks in
    # quickly instead of hanging the request on connection retries.
    broker_connection_retry_on_startup=True,
    broker_transport_options={"max_retries": 1},
)
# Don't block the publishing request retrying a dead broker — surface the error so
# the API can fall back to synchronous processing.
celery_app.conf.broker_connection_max_retries = 0


def _process_claim(submission_json: dict, claim_id: str) -> dict:
    """Pure function body: reconstruct the submission, run the pipeline, persist,
    and return the ClaimResult JSON. Documents are already saved to disk by the API
    (paths live inside submission_json["documents"]), so the worker only needs the
    structured submission — no file payloads cross the broker."""
    sub = ClaimSubmission(**submission_json)
    state = run_claim(sub)
    result: ClaimResult = state_to_result(state, claim_id)
    # Persistence must not crash a completed (expensive) claim: a DB outage logs a
    # warning but we still return the computed result so the poller can read it.
    try:
        persistence.save_claim(sub, result)
    except Exception as e:  # noqa: BLE001
        log.warning("save_claim failed for %s; returning result without persisting: %s",
                    claim_id, e)
    # Immutable audit log (non-PHI decision summary). Best-effort, non-blocking.
    try:
        from app.services.audit import record_decision
        if result.decision is not None:
            record_decision(claim_id, result.decision, actor="system")
    except Exception as e:  # noqa: BLE001 — auditing must never block processing
        log.warning("audit record_decision failed for %s (non-blocking): %s", claim_id, e)
    return result.model_dump(mode="json")


@celery_app.task(name="app.worker.process_claim_task")
def process_claim_task(submission_json: dict, claim_id: str,
                       upload_paths: dict | None = None) -> dict:
    """Celery task: process one claim asynchronously.

    Args:
        submission_json: ClaimSubmission.model_dump(mode="json") — includes the
            documents list with on-disk stored_path for each file.
        claim_id: the server-generated claim id (CLM-…) to persist under.
        upload_paths: optional {file_id: path} echo for observability/back-compat;
            the authoritative paths already live inside submission_json.

    Returns the persisted ClaimResult as a JSON-serialisable dict.
    """
    return _process_claim(submission_json, claim_id)
