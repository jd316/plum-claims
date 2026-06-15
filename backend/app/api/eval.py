import logging, os, threading

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.services.auth import Principal
from app.deps_auth import require_ops
from app.evalrunner.runner import run_all, to_markdown
from app.fixtures.loader import load_cases

log = logging.getLogger("plum.claims")

router = APIRouter()


@router.get("/api/eval/cases")
def eval_cases(user: Principal = Depends(require_ops)):
    return load_cases(settings.test_cases_path)

# Guard against launching a second expensive live eval run while one is in progress.
_eval_lock = threading.Lock()

@router.post("/api/eval/run")
def eval_run(user: Principal = Depends(require_ops)):
    if not _eval_lock.acquire(blocking=False):
        raise HTTPException(409, "An eval run is already in progress")
    try:
        results = run_all()
        # Best-effort report write to an absolute path under the storage dir.
        # A write failure must never discard the (expensive) computed results.
        try:
            report_path = os.path.join(settings.storage_dir, "eval_report.md")
            os.makedirs(settings.storage_dir, exist_ok=True)
            with open(report_path, "w") as f:
                f.write(to_markdown(results))
        except Exception as e:
            log.warning("Failed to write eval report; returning results anyway: %s", e)
        return results
    finally:
        _eval_lock.release()


@router.post("/api/eval/message-quality")
def eval_message_quality(user: Principal = Depends(require_ops)):
    """Run the LLM-as-judge message-quality rubric on the 12 eval cases (12 judge
    calls). ADDITIVE — grades the member-facing text the pipeline already produced,
    never changing a decision. Re-uses the same lock as /api/eval/run since both run
    the live 12-case pipeline."""
    from app.evalrunner.message_quality import run_message_quality_eval
    if not _eval_lock.acquire(blocking=False):
        raise HTTPException(409, "An eval run is already in progress")
    try:
        return run_message_quality_eval()
    finally:
        _eval_lock.release()
