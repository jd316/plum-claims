import logging, os
from typing import Any, cast

from fastapi import (APIRouter, Depends, File, Form, Header, HTTPException,
                     UploadFile)

from app.config import settings
from app.models.schemas import DocumentInput
from app.services.auth import Principal
from app.deps_auth import require_user, require_ops, require_owner_or_ops
from app.agents.extraction import extract_document
from app.services import persistence
from app.services.policy_engine import get_policy_engine
from app.services.idempotency import get_store as get_idempotency_store
from app.api.common import (_validate_upload, _llm_rate_limit, _ingest_claim,
                            _idempotent_replay, _claim_member_id, _run_and_persist)

log = logging.getLogger("plum.claims")

router = APIRouter()


@router.get("/api/members")
def members(user: Principal = Depends(require_ops)):
    pe = get_policy_engine(settings.policy_path)
    return [{"member_id": m["member_id"], "name": m["name"], "relationship": m.get("relationship")}
            for m in pe.members()]

# ---------------------------------------------------------------------------
# Shift-left document checks — live classification of a single uploaded file as
# the member fills out the form, plus the per-category document requirements the
# UI uses to build drop-zones. PURE-ADDITIVE: independent of the decision
# pipeline; never reads/writes claim state and never blocks submission.
# ---------------------------------------------------------------------------

@router.post("/api/documents/classify")
def classify_document(file: UploadFile = File(...),
                      user: Principal = Depends(require_user),
                      _rl: None = Depends(_llm_rate_limit)):
    """Live single-file classification for shift-left UX feedback. Saves the file
    to a scratch path, runs the SAME vision extraction the pipeline uses, and
    returns a small JSON summary. A Gemini failure degrades gracefully to a 200
    UNKNOWN response rather than 500-ing the upload. This endpoint does NOT touch
    the decision pipeline and its result is never authoritative."""
    path = _validate_upload(file)
    try:
        try:
            res = extract_document(
                DocumentInput(file_id="scratch", file_name=file.filename, stored_path=path)
            )
        except Exception as e:  # noqa: BLE001 — never 500 the upload on a vision hiccup
            log.warning("classify_document extraction failed: %s", e)
            return {"doc_type": "UNKNOWN", "readable": True, "quality_issues": [],
                    "patient_name": None, "confidence": 0.0, "error": "could not analyze"}
        return {
            "doc_type": res.doc_type,
            "readable": res.quality.readable,
            "quality_issues": res.quality.quality_issues,
            "patient_name": res.patient_name.value,
            "confidence": res.patient_name.confidence,
        }
    finally:
        # Best-effort cleanup — leaving a scratch file behind must never error the request.
        try:
            os.remove(path)
        except OSError:
            pass


@router.post("/api/claims")
def submit_claim(payload: str = Form(...), files: list[UploadFile] = File(...),
                 idempotency_key: str | None = Header(default=None),
                 user: Principal = Depends(require_user)):
    # Idempotency: a repeat of a previously-seen key replays the stored result
    # instead of re-processing. No key → behaves exactly as before (always processed).
    replay = _idempotent_replay(idempotency_key)
    if replay is not None:
        # Authorize the REPLAY too: a non-owner member must not retrieve another
        # member's claim by replaying its idempotency key (IDOR). Resolve the owning
        # member from the stored submission and enforce ownership. No-op when auth off.
        require_owner_or_ops(_claim_member_id(replay.get("claim_id")), user)
        return replay
    claim_id, sub = _ingest_claim(payload, files)
    # When auth is ON, a member may only submit claims for their own member_id (ops: any).
    require_owner_or_ops(sub.member_id, user)
    result = _run_and_persist(sub, claim_id)
    get_idempotency_store().put(idempotency_key, claim_id)
    return result


@router.post("/api/claims/async")
def submit_claim_async(payload: str = Form(...), files: list[UploadFile] = File(...),
                       idempotency_key: str | None = Header(default=None),
                       user: Principal = Depends(require_user)):
    """Asynchronous submit (credible-scale path): validate + save files exactly like
    the sync endpoint, then enqueue the claim on the Celery/Redis task queue and
    return immediately with a job_id the UI can poll via GET /api/jobs/{job_id}.

    GRACEFUL FALLBACK: if the broker is unreachable (Redis down / no worker), we do
    NOT 503 — we fall back to processing the claim synchronously in-request and
    return status "completed" with the full result. So the async endpoint always
    works, even without a running worker; it just loses the off-thread benefit."""
    # Idempotency: if this key's prior claim already completed and is stored, replay
    # it as a completed envelope instead of enqueuing a duplicate. No key → as before.
    replay = _idempotent_replay(idempotency_key)
    if replay is not None:
        # Authorize the replay (IDOR guard): a non-owner member must not retrieve
        # another member's stored claim via its idempotency key. No-op when auth off.
        require_owner_or_ops(_claim_member_id(replay.get("claim_id")), user)
        return {"job_id": None, "claim_id": replay.get("claim_id"), "status": "completed",
                "result": replay, "idempotent_replay": True}
    claim_id, sub = _ingest_claim(payload, files)
    # When auth is ON, a member may only submit claims for their own member_id (ops: any).
    require_owner_or_ops(sub.member_id, user)
    submission_json = sub.model_dump(mode="json")
    upload_paths = {d.file_id: d.stored_path for d in sub.documents}
    try:
        from app.worker import process_claim_task
        # connect_timeout keeps a dead broker from hanging the request; on failure we
        # fall through to the synchronous path below.
        async_result = cast(Any, process_claim_task).apply_async(
            args=[submission_json, claim_id, upload_paths],
            retry=False,
        )
        # Record key→claim_id now; the result is replayable once the worker persists it.
        get_idempotency_store().put(idempotency_key, claim_id)
        return {"job_id": async_result.id, "claim_id": claim_id, "status": "queued"}
    except Exception as e:  # noqa: BLE001 — broker unreachable → graceful sync fallback
        log.warning("Broker unreachable for async submit (%s); processing synchronously: %s",
                    claim_id, e)
        result = _run_and_persist(sub, claim_id)
        get_idempotency_store().put(idempotency_key, claim_id)
        return {"job_id": None, "claim_id": claim_id, "status": "completed",
                "result": result, "fallback": "sync"}


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str, user: Principal = Depends(require_user)):
    """Poll a queued async claim. Maps Celery's AsyncResult state to the UI's
    status vocabulary and, once completed, attaches the persisted ClaimResult.

    Celery state → status: PENDING/RECEIVED→queued, STARTED→started,
    SUCCESS→completed, FAILURE→failed (everything else → queued)."""
    from app.worker import celery_app
    try:
        res = celery_app.AsyncResult(job_id)
        state = res.state
    except Exception as e:  # noqa: BLE001 — broker down → report failed cleanly, no 500
        log.warning("job_status could not reach broker for %s: %s", job_id, e)
        raise HTTPException(503, detail="Job backend unavailable")

    status_map = {"PENDING": "queued", "RECEIVED": "queued", "STARTED": "started",
                  "SUCCESS": "completed", "FAILURE": "failed"}
    status = status_map.get(state, "queued")
    out: dict = {"job_id": job_id, "status": status, "claim_id": None, "result": None}

    if status == "completed":
        # The task return value is {..., claim_id}; prefer the persisted record so the
        # poller sees exactly what is stored. Fall back to the task payload if the DB
        # lacks it (e.g. persistence was down during processing).
        payload = res.result if isinstance(res.result, dict) else None
        claim_id = (payload or {}).get("claim_id")
        if claim_id:
            # The completed result is PHI — enforce owner-or-ops before returning it
            # (the job_id is an unguessable Celery UUID, but that is not authorization).
            require_owner_or_ops(_claim_member_id(claim_id), user)
            out["claim_id"] = claim_id
            out["result"] = persistence.get_claim(claim_id) or payload
        else:
            # Fail-CLOSED: a completed job with no resolvable claim id has no owner to
            # authorize against, so we must NOT return the raw result payload (PHI) here.
            out["claim_id"] = None
            out["result"] = None
            out["note"] = "result unavailable (no associated claim id)"
    elif status == "failed":
        out["error"] = str(res.result)[:300]
    return out
