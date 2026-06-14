"""Locust load test for the FAST / deterministic claim-API paths.

We deliberately do NOT hammer `POST /api/claims` (sync) — that runs the full live
Gemini pipeline (~30s, rate-limited, ~₹0.40/claim). Load-testing that at volume is
impractical and expensive; we project it instead (see scale_projection.py).

What we DO load-test for real (all fast, deterministic, no heavy Gemini):
  * GET  /api/health                          — liveness
  * GET  /api/members                         — policy roster (read)
  * GET  /api/policy/document-requirements    — per-category doc map (read)
  * GET  /api/claims                          — claims list (DB read)
  * POST /api/claims/async                    — ENQUEUE latency only

ENQUEUE measurement note:
  `POST /api/claims/async` returns immediately with {"status": "queued"} when a
  Celery worker + broker are up (the worker does the slow Gemini work off-thread),
  so we measure pure enqueue latency. If NO broker/worker is up, the endpoint's
  graceful fallback processes the claim SYNCHRONOUSLY (~30s) and returns
  {"status": "completed", "fallback": "sync"} — which would pollute the fast-path
  numbers. To stay honest the AsyncEnqueueUser task ASSERTS a "queued" response and
  FAILS the sample otherwise, so a fallback shows up as an error rather than a fake
  latency. Run this user class ONLY against a stack with a worker, or rely on
  ReadOnlyUser (the default weighting) for the fast-path RPS/latency numbers.

Run (read-only fast paths, no worker needed):
  .venv/bin/locust -f loadtest/locustfile.py --headless -u 50 -r 10 -t 30s \
      --host http://localhost:8000 ReadOnlyUser

Run (include enqueue, requires a worker):
  .venv/bin/locust -f loadtest/locustfile.py --headless -u 50 -r 10 -t 30s \
      --host http://localhost:8000
"""
from __future__ import annotations

import io
import json

from locust import HttpUser, between, task

# A tiny in-memory PNG-ish payload for the multipart enqueue. The async endpoint
# validates type/size and saves the file, then enqueues — it does NOT run vision
# inline (the worker does), so the byte content is irrelevant to enqueue latency.
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

_ASYNC_PAYLOAD = {
    "member_id": "EMP001",
    "policy_id": "PLUM_GHI_2024",
    "claim_category": "CONSULTATION",
    "treatment_date": "2025-06-01",
    "claimed_amount": 1500.0,
    "hospital_name": "Apollo Hospitals",
}


class ReadOnlyUser(HttpUser):
    """Hammers the fast read endpoints. Needs no worker/broker — safe everywhere.

    Task weights reflect a realistic ops/UI mix: lots of health + roster + doc-req
    polling, fewer (heavier) claims-list reads."""
    wait_time = between(0.0, 0.1)

    @task(5)
    def health(self):
        self.client.get("/api/health", name="GET /api/health")

    @task(4)
    def members(self):
        self.client.get("/api/members", name="GET /api/members")

    @task(4)
    def doc_requirements(self):
        self.client.get("/api/policy/document-requirements",
                        name="GET /api/policy/document-requirements")

    @task(2)
    def claims_list(self):
        self.client.get("/api/claims", name="GET /api/claims")


class AsyncEnqueueUser(HttpUser):
    """Measures POST /api/claims/async ENQUEUE latency.

    Asserts the response is {"status": "queued"} — if the broker is down and the
    endpoint falls back to synchronous (~30s) processing, the sample is marked a
    FAILURE rather than recorded as a (misleading) fast latency. So: run this only
    against a stack WITH a Celery worker + Redis."""
    wait_time = between(0.1, 0.3)

    @task
    def enqueue(self):
        files = {"files": ("scan.png", io.BytesIO(_TINY_PNG), "image/png")}
        data = {"payload": json.dumps(_ASYNC_PAYLOAD)}
        with self.client.post("/api/claims/async", data=data, files=files,
                              name="POST /api/claims/async (enqueue)",
                              catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"unexpected status {resp.status_code}")
                return
            try:
                body = resp.json()
            except ValueError:
                resp.failure("non-JSON response")
                return
            if body.get("status") == "queued":
                resp.success()
            else:
                # Synchronous fallback (no worker) — do not count as a fast enqueue.
                resp.failure(f"not queued (status={body.get('status')}, "
                             f"fallback={body.get('fallback')}) — start a worker")
