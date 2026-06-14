"""Deterministic tests for the Ops document-viewer endpoints (no Gemini, no pipeline).

Persists a claim directly via persistence.save_claim with a real tiny PNG on disk
under storage_dir/uploads/<id>/F001.png, then exercises the listing + streaming +
security (path-traversal / unknown file) behaviour of the two new endpoints.
"""
from __future__ import annotations

import base64
import os
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.schemas import (
    ClaimResult,
    ClaimSubmission,
    Decision,
    DocumentInput,
)

# Smallest valid 1x1 PNG.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _db_reachable() -> bool:
    try:
        from sqlalchemy import text
        from app.services.persistence import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="module", autouse=True)
def require_db():
    if not _db_reachable():
        pytest.skip("Postgres unreachable — skipping document endpoint tests")


@pytest.fixture
def persisted_claim():
    """Persist a claim with one real PNG on disk under storage_dir/uploads/<id>/."""
    from app.services.persistence import init_db, save_claim

    init_db()
    claim_id = f"test-doc-{uuid.uuid4().hex}"
    updir = os.path.join(settings.storage_dir, "uploads", claim_id)
    os.makedirs(updir, exist_ok=True)
    stored_path = os.path.join(updir, "F001.png")
    with open(stored_path, "wb") as f:
        f.write(_PNG_BYTES)

    sub = ClaimSubmission(
        member_id="M-DOC-001",
        policy_id="POL-DOC-001",
        claim_category="CONSULTATION",
        treatment_date=date(2026, 1, 15),
        claimed_amount=1500.0,
        documents=[
            DocumentInput(file_id="F001", file_name="receipt.png", stored_path=stored_path)
        ],
    )
    result = ClaimResult(
        claim_id=claim_id,
        blocked=False,
        decision=Decision(status="APPROVED", approved_amount=1200.0, confidence=0.9),
    )
    save_claim(sub, result)
    return claim_id, stored_path


def test_list_documents(persisted_claim):
    claim_id, _ = persisted_claim
    r = TestClient(app).get(f"/api/claims/{claim_id}/documents")
    assert r.status_code == 200
    docs = r.json()
    assert len(docs) == 1
    d = docs[0]
    assert d["file_id"] == "F001"
    assert d["file_name"] == "receipt.png"
    assert d["content_type"] == "image/png"
    assert d["doc_type"] == "UNKNOWN"  # no extraction trace present


def test_stream_document_returns_image_bytes(persisted_claim):
    claim_id, _ = persisted_claim
    r = TestClient(app).get(f"/api/claims/{claim_id}/documents/F001")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == _PNG_BYTES


def test_unknown_claim_returns_404():
    client = TestClient(app)
    assert client.get("/api/claims/nope-xyz-123/documents").status_code == 404
    assert client.get("/api/claims/nope-xyz-123/documents/F001").status_code == 404


def test_unknown_file_id_returns_404(persisted_claim):
    claim_id, _ = persisted_claim
    r = TestClient(app).get(f"/api/claims/{claim_id}/documents/F999")
    assert r.status_code == 404


def test_path_traversal_is_rejected():
    """A claim whose stored_path points outside storage_dir must be rejected (403),
    even though the path is read only from the stored submission. The client never
    controls the on-disk path, but this guards the realpath containment check."""
    from app.services.persistence import init_db, save_claim

    init_db()
    claim_id = f"test-doc-evil-{uuid.uuid4().hex}"
    sub = ClaimSubmission(
        member_id="M-DOC-002",
        policy_id="POL-DOC-002",
        claim_category="CONSULTATION",
        treatment_date=date(2026, 1, 15),
        claimed_amount=1500.0,
        documents=[
            DocumentInput(
                file_id="F001",
                file_name="passwd",
                stored_path=os.path.join(settings.storage_dir, "uploads", "..", "..", "..", "..", "etc", "passwd"),
            )
        ],
    )
    result = ClaimResult(claim_id=claim_id, blocked=False,
                         decision=Decision(status="APPROVED", approved_amount=0.0, confidence=0.5))
    save_claim(sub, result)

    r = TestClient(app).get(f"/api/claims/{claim_id}/documents/F001")
    assert r.status_code == 403
