import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.config import settings
from app.services.auth import Principal
from app.deps_auth import require_user, require_owner_or_ops
from app.services import persistence
from app.services import crypto
from app.api.common import _claim_member_id, _documents_for, _content_type_for

router = APIRouter()


@router.get("/api/claims")
def claims_list(user: Principal = Depends(require_user)):
    claims = persistence.list_claims()
    # Scope: a member sees only their own claims; ops (and the auth-off system
    # principal) see all. The list is filtered in-app from the indexed member_id.
    if settings.auth_enabled and not user.is_ops:
        claims = [c for c in claims if c.get("member_id") == user.member_id]
    return claims


@router.get("/api/claims/{claim_id}")
def claim_detail(claim_id: str, user: Principal = Depends(require_user)):
    r = persistence.get_claim(claim_id)
    if not r: raise HTTPException(404, "claim not found")
    require_owner_or_ops(_claim_member_id(claim_id), user)
    return r


# ---------------------------------------------------------------------------
# Ops document viewer — read-only access to a claim's source documents.
# Pure-additive: does not touch the decision pipeline.
# ---------------------------------------------------------------------------

@router.get("/api/claims/{claim_id}/documents")
def claim_documents(claim_id: str, user: Principal = Depends(require_user)):
    require_owner_or_ops(_claim_member_id(claim_id), user)
    return _documents_for(claim_id)

@router.api_route("/api/claims/{claim_id}/documents/{file_id}", methods=["GET", "HEAD"])
def claim_document_file(claim_id: str, file_id: str,
                        user: Principal = Depends(require_user)):
    submission = persistence.get_submission(claim_id)
    if submission is None:
        raise HTTPException(404, "claim not found")
    require_owner_or_ops(submission.get("member_id"), user)
    # SECURITY: resolve the path ONLY from the stored submission — never from the
    # client-supplied file_id. Then confirm the realpath is inside storage_dir to
    # block path-traversal / arbitrary file reads.
    stored_path = next((d.get("stored_path") for d in (submission.get("documents") or [])
                        if d.get("file_id") == file_id), None)
    if not stored_path:
        raise HTTPException(404, "file not found")
    real_path = os.path.realpath(stored_path)
    storage_root = os.path.realpath(settings.storage_dir)
    if os.path.commonpath([real_path, storage_root]) != storage_root:
        raise HTTPException(403, "file outside storage root")
    if not os.path.isfile(real_path):
        raise HTTPException(404, "file missing on disk")
    # Read through the decrypt-aware helper so an at-rest-encrypted document is served
    # as its original bytes (plaintext/legacy files pass through unchanged). Files are
    # capped at 15 MB on ingest, so loading into memory here is bounded.
    return Response(content=crypto.read_file_decrypted(real_path),
                    media_type=_content_type_for(real_path))
