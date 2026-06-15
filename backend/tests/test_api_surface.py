"""API-surface guard.

This test freezes the *exact* public HTTP surface of the app so that mechanical
refactors (e.g. splitting ``main.py`` into ``APIRouter`` modules) provably change
nothing observable. Two layers:

1. Route inventory — the sorted set of ``(method, path)`` pairs.
2. Full OpenAPI schema — byte-for-byte (canonical JSON). Catches a dropped route,
   a renamed handler (operationId), a changed parameter/request/response model,
   or a status code — not just path existence.

The baseline lives in ``tests/snapshots/``. To intentionally update it after a
*reviewed* surface change, run with ``UPDATE_API_SNAPSHOT=1``.
"""

import json
import os
import pathlib

from fastapi.testclient import TestClient

from app.main import app

SNAP_DIR = pathlib.Path(__file__).parent / "snapshots"
ROUTES_SNAP = SNAP_DIR / "route_inventory.json"
OPENAPI_SNAP = SNAP_DIR / "openapi.json"

_UPDATE = os.environ.get("UPDATE_API_SNAPSHOT") == "1"


def _route_inventory() -> list[list[str]]:
    rows = []
    for r in app.routes:
        methods = getattr(r, "methods", None)
        path = getattr(r, "path", "")
        if not methods:
            continue
        for m in sorted(methods - {"HEAD", "OPTIONS"}):
            rows.append([m, path])
    return sorted(rows)


def _openapi_canonical() -> str:
    # TestClient triggers schema generation through the real ASGI app.
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
    return json.dumps(schema, sort_keys=True, indent=2)


def _read_or_write(path: pathlib.Path, current: str) -> str:
    if _UPDATE or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(current)
    return path.read_text()


def test_route_inventory_unchanged():
    current = json.dumps(_route_inventory(), indent=2)
    baseline = _read_or_write(ROUTES_SNAP, current)
    assert current == baseline, (
        "Route inventory changed. If intentional, re-run with UPDATE_API_SNAPSHOT=1.\n"
        f"Run a diff against {ROUTES_SNAP} to see what moved."
    )


def test_openapi_schema_unchanged():
    current = _openapi_canonical()
    baseline = _read_or_write(OPENAPI_SNAP, current)
    assert current == baseline, (
        "OpenAPI schema changed (path/operationId/params/models/responses). "
        "If intentional, re-run with UPDATE_API_SNAPSHOT=1."
    )
