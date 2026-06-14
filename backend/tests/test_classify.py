"""Tests for the shift-left document-classification endpoint and the policy
document-requirements endpoint. PURE-ADDITIVE feature, independent of the
decision pipeline.

The guard tests are deterministic (no Gemini): they assert the upload guards
(415 wrong-type / 413 too-large) reject the file BEFORE any extraction runs, so
they stay in the "not live" suite. The happy-path classify is live.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# --- Deterministic: document-requirements map --------------------------------

def test_document_requirements_returns_full_map():
    r = client.get("/api/policy/document-requirements")
    assert r.status_code == 200
    body = r.json()
    # One entry per claim category, each with required/optional lists.
    assert set(body) == {
        "CONSULTATION", "DIAGNOSTIC", "PHARMACY", "DENTAL", "VISION", "ALTERNATIVE_MEDICINE",
    }
    cons = body["CONSULTATION"]
    assert cons["required"] == ["PRESCRIPTION", "HOSPITAL_BILL"]
    assert "optional" in cons


# --- Deterministic: classify upload guards (no Gemini called) ----------------

def test_classify_rejects_wrong_content_type():
    """A .txt / text-plain file is rejected with 415 before extraction runs."""
    r = client.post(
        "/api/documents/classify",
        files={"file": ("note.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert r.status_code == 415


def test_classify_rejects_too_large_file():
    """A >15MB PNG is rejected with 413 before extraction runs."""
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (16 * 1024 * 1024)
    r = client.post(
        "/api/documents/classify",
        files={"file": ("big.png", io.BytesIO(big), "image/png")},
    )
    assert r.status_code == 413


# --- Live: real prescription classifies as PRESCRIPTION ----------------------

@pytest.mark.live
def test_classify_prescription_live(tmp_path):
    from app.fixtures.loader import load_cases
    from app.fixtures.renderer import render_case_documents
    from tests.conftest import REPO_ROOT

    case = [c for c in load_cases(str(REPO_ROOT / "test_cases.json"))
            if c["case_id"] == "TC004"][0]
    paths = render_case_documents(case, str(tmp_path))
    # F007 is the TC004 prescription fixture.
    rx_path = paths["F007"]
    with open(rx_path, "rb") as fh:
        r = client.post(
            "/api/documents/classify",
            files={"file": ("F007.png", fh, "image/png")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["doc_type"] == "PRESCRIPTION"
    assert body["readable"] is True
