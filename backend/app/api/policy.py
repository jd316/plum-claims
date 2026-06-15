from typing import get_args

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.models.schemas import ClaimCategory
from app.services.auth import Principal
from app.deps_auth import require_user, require_ops
from app.services import policy_store
from app.services.preview_sample import from_test_case, from_inline
from app.services.policy_engine import get_policy_engine

router = APIRouter()


@router.get("/api/policy/document-requirements")
def document_requirements(user: Principal = Depends(require_user)):
    """The full {category: {required, optional}} map from policy, so the frontend
    can build per-category drop-zones. Read-only; reflects policy_terms.json."""
    pe = get_policy_engine(settings.policy_path)
    return {cat: pe.document_requirements(cat) for cat in get_args(ClaimCategory)}


# ---------------------------------------------------------------------------
# Policy-as-code studio (ops-only). Manages POLICY VERSIONS in the DB; only an
# explicit activate writes the chosen version's JSON to the file the engine reads
# (settings.policy_path) + invalidates the cache. The default active version is v1
# == the original policy_terms.json, so the live engine and the 12/12 eval are
# unchanged until an operator deliberately activates a different version. Preview
# is READ-ONLY: it compares the deterministic decision under a candidate vs the
# active policy without ever touching the live file.
# ---------------------------------------------------------------------------

class PolicyVersionCreate(BaseModel):
    policy_json: dict
    label: str | None = None

class PolicyPreviewRequest(BaseModel):
    policy_json: dict
    # Either a test-case id (e.g. "TC004") or an inline sample claim. If both are
    # given, test_case_id wins. The sample is run under candidate vs active policy.
    test_case_id: str | None = None
    sample: dict | None = None


@router.get("/api/policy/current")
def policy_current(user: Principal = Depends(require_ops)):
    """The active policy JSON + its version metadata."""
    active = policy_store.get_active()
    if active is None:
        raise HTTPException(404, "No active policy version (not seeded)")
    return active


@router.get("/api/policy/versions")
def policy_versions(user: Principal = Depends(require_ops)):
    """All policy versions (metadata only, newest first)."""
    return policy_store.list_versions()


@router.get("/api/policy/versions/{version_id}")
def policy_version(version_id: str, user: Principal = Depends(require_ops)):
    """One policy version with its full JSON."""
    row = policy_store.get_version(version_id)
    if row is None:
        raise HTTPException(404, f"Unknown policy version {version_id}")
    return row


@router.get("/api/policy/versions/{version_id}/diff/{other_id}")
def policy_version_diff(version_id: str, other_id: str,
                        user: Principal = Depends(require_ops)):
    """Structured leaf-path diff between two versions."""
    try:
        return policy_store.diff_versions(version_id, other_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.post("/api/policy/versions")
def policy_version_create(body: PolicyVersionCreate,
                          user: Principal = Depends(require_ops)):
    """Validate + store a new INACTIVE policy version. Does not activate."""
    try:
        return policy_store.create_version(body.policy_json, body.label, actor=user.username)
    except policy_store.PolicyValidationError as e:
        raise HTTPException(422, str(e))


@router.post("/api/policy/versions/{version_id}/activate")
def policy_version_activate(version_id: str, user: Principal = Depends(require_ops)):
    """Activate a version: writes its JSON to the live policy file, invalidates the
    cache, and audits. This changes live decisions."""
    try:
        return policy_store.activate_version(version_id, actor=user.username)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except policy_store.PolicyValidationError as e:
        raise HTTPException(422, str(e))


@router.post("/api/policy/preview")
def policy_preview(body: PolicyPreviewRequest, user: Principal = Depends(require_ops)):
    """READ-ONLY impact preview: run a sample claim through the deterministic decision
    under the candidate policy vs the active policy, and return before/after. Never
    touches the live policy file."""
    try:
        if body.test_case_id:
            sample = from_test_case(body.test_case_id)
        elif body.sample:
            sample = from_inline(body.sample)
        else:
            raise HTTPException(422, "Provide either test_case_id or sample")
        return policy_store.preview_decision(body.policy_json, sample)
    except policy_store.PolicyValidationError as e:
        raise HTTPException(422, str(e))
    except KeyError as e:
        raise HTTPException(404, str(e))
