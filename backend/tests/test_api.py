from fastapi.testclient import TestClient
from app.main import app


def test_health():
    assert TestClient(app).get("/api/health").json() == {"status": "ok"}


import json, pytest
from app.fixtures.loader import load_cases
from app.fixtures.renderer import render_case_documents
from tests.conftest import REPO_ROOT


def _db_reachable() -> bool:
    # This end-to-end test persists a claim and reads it back, so it needs Postgres in
    # addition to live Gemini. Skip (don't fail) when the DB is unreachable — mirrors the
    # guard in test_persistence.py / test_member_features.py.
    try:
        from sqlalchemy import text
        from app.services.persistence import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.mark.live
@pytest.mark.skipif(not _db_reachable(), reason="Postgres unreachable — end-to-end submit needs the DB")
def test_submit_claim_end_to_end(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    case = [c for c in load_cases(str(REPO_ROOT / "test_cases.json")) if c["case_id"] == "TC004"][0]
    paths = render_case_documents(case, str(tmp_path))
    inp = case["input"]
    payload = {k: inp[k] for k in ("member_id","policy_id","claim_category","treatment_date",
                                   "claimed_amount","ytd_claims_amount")}
    files = [("files", (f"{fid}.png", open(p, "rb"), "image/png")) for fid, p in paths.items()]
    with TestClient(app) as client:
        r = client.post("/api/claims", data={"payload": json.dumps(payload)}, files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["decision"]["status"] == "APPROVED" and body["trace"]
    with TestClient(app) as client:
        assert client.get(f"/api/claims/{body['claim_id']}").status_code == 200
