"""Seed the deployment with the 12 test cases, persisted through the PRODUCTION path,
so the live app demonstrates itself end-to-end — member claim histories, the Ops
dashboard/analytics, the fraud queue + worklist, the audit log, and replay/trace all
populate from real records (instead of empty shells on a fresh deploy).

Run inside the backend container (it has the Gemini key + DB access):

    python scripts/seed_demo_data.py              # seed the 12 cases
    python scripts/seed_demo_data.py --clear       # wipe claims + audit_log first, then seed
    python scripts/seed_demo_data.py --clear-only  # just wipe

Each case is rendered into real fixture documents and run through the SAME path a real
`POST /api/claims` uses (`_run_and_persist`: run_claim -> save_claim -> record_decision),
so at-rest encryption, the audit log, and replay facts are all populated authentically.
A few decided claims are then outcome-labelled to light up calibration / improvement
proposals. Idempotent via --clear (re-running without it would create duplicates).
"""
import os
import sys
import uuid

from app.config import settings
from app.fixtures.loader import load_cases, case_to_submission
from app.fixtures.renderer import render_case_documents
from app.api.common import _run_and_persist, _accumulate_history
from app.services import persistence


def clear() -> None:
    from sqlalchemy import text
    with persistence.engine.begin() as c:
        c.execute(text("DELETE FROM audit_log"))
        c.execute(text("DELETE FROM claims"))
    print("cleared: claims + audit_log")


def seed() -> None:
    out = os.path.join(settings.storage_dir, "demo_seed")
    decided: list[tuple[str, float, str]] = []
    n = 0
    for case in load_cases(settings.test_cases_path):
        paths = render_case_documents(case, os.path.join(out, case["case_id"]))
        sub = case_to_submission(case, paths)
        _accumulate_history(sub)                       # member YTD / floater, like the API
        claim_id = f"CLM-{uuid.uuid4().hex[:10]}"
        res = _run_and_persist(sub, claim_id)          # run -> save_claim -> record_decision
        d = res.get("decision")
        status = d["status"] if d else ("BLOCKED" if res.get("blocked") else "?")
        if d:
            decided.append((claim_id, d["confidence"], d["status"]))
        print(f"  {case['case_id']:6} {sub.member_id:7} -> {claim_id}  {status}")
        n += 1

    # Light up calibration / improvement-proposals: label a few decided claims
    # (2 correct, 1 incorrect — a non-trivial signal).
    from app.services.audit import record_outcome_label
    labelled = 0
    for i, (cid, conf, st) in enumerate(decided[:3]):
        record_outcome_label(cid, confidence=conf, correct=(i != 1),
                             decision_status=st, actor="demo")
        labelled += 1
    print(f"seeded {n} claims, labelled {labelled} outcomes")


if __name__ == "__main__":
    args = set(sys.argv[1:])
    if {"--clear", "--clear-only"} & args:
        clear()
    if "--clear-only" not in args:
        seed()
