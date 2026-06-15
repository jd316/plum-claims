"""Cross-router shared helpers for the HTTP API.

These were extracted verbatim from ``app.main`` when the monolithic module was
split into ``APIRouter`` modules. They are imported by both ``app.main`` and the
per-feature routers under ``app.api.*``. This module must NOT import from
``app.main`` (no import cycles)."""

import json, logging, os, uuid

from fastapi import HTTPException, Request, UploadFile
from pydantic import ValidationError

from app.config import settings
from app.models.schemas import ClaimSubmission, DocumentInput
from app.graph.build import run_claim
from app.evalrunner.runner import state_to_result
from app.services import persistence
from app.services import crypto
from app.services.object_store import get_object_store, storage_key
from app.services.policy_engine import get_policy_engine
from app.services.ratelimit import SlidingWindowLimiter
from app.services.idempotency import get_store as get_idempotency_store

log = logging.getLogger("plum.claims")

MAX_FILES = 10
MAX_FILE_BYTES = 15 * 1024 * 1024  # 15 MB per file
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "application/pdf"}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}

_login_limiter = SlidingWindowLimiter(settings.login_rate_limit_max,
                                      settings.login_rate_limit_window_seconds)

_llm_limiter = SlidingWindowLimiter(settings.llm_rate_limit_max,
                                    settings.llm_rate_limit_window_seconds)


def _client_ip(request: Request) -> str:
    """The real client IP for rate-limiting. nginx sets X-Real-IP to $remote_addr,
    OVERWRITING any client-supplied value, so it is trustworthy. We deliberately do
    NOT trust the leftmost X-Forwarded-For entry: that hop is client-controlled (nginx
    appends the real peer to whatever the client sent), so honoring it would let an
    attacker rotate a spoofed value to evade the per-IP rate limit. Falls back to the
    socket peer when no trusted X-Real-IP is present (e.g. tests / direct calls)."""
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    return request.client.host if request.client else "unknown"


def _llm_rate_limit(request: Request) -> None:
    """Per-IP throttle for paid Gemini-backed endpoints (cost-DoS guard). Gated OFF by
    default → no-op in dev/test/eval; enable settings.llm_rate_limit_enabled in prod."""
    if not settings.llm_rate_limit_enabled:
        return
    ip = _client_ip(request)
    if not _llm_limiter.allow(f"llm|{ip}"):
        raise HTTPException(429, detail="Rate limit exceeded for AI processing — please retry shortly.")


def _validate_upload(f: UploadFile) -> str:
    """Apply the SAME guards as /api/claims (type 415, size 413) and stream the
    upload to a scratch file. Returns the scratch path. Raises HTTPException on
    a bad type/size. The caller is responsible for cleaning up the file."""
    ext = os.path.splitext(os.path.basename(f.filename or ""))[1].lower()[:10]
    if (f.content_type not in ALLOWED_CONTENT_TYPES) and (ext not in ALLOWED_EXTENSIONS):
        raise HTTPException(415, detail=(f"Unsupported file type for '{f.filename}'. "
                                         "Allowed: PNG, JPEG, PDF."))
    scratch_dir = os.path.join(settings.storage_dir, "scratch")
    os.makedirs(scratch_dir, exist_ok=True)
    path = os.path.join(scratch_dir, f"{uuid.uuid4().hex}{ext or '.bin'}")
    written = 0
    with open(path, "wb") as out:
        while chunk := f.file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_FILE_BYTES:
                out.close()
                os.remove(path)
                raise HTTPException(413, detail=(f"File '{f.filename}' exceeds the "
                                                 f"{MAX_FILE_BYTES // (1024 * 1024)} MB limit."))
            out.write(chunk)
    return path


def _ingest_claim(payload: str, files: list[UploadFile]) -> tuple[str, ClaimSubmission]:
    """Shared ingest for the sync + async submit paths: enforce the upload count /
    type / size guards, stream each file to disk under a server-generated claim id,
    and build a validated ClaimSubmission. Returns (claim_id, submission). Raises
    HTTPException (400/413/415/422) on bad input — never a 500. The decision
    pipeline is NOT run here; that is the caller's choice (sync vs. enqueue)."""
    # ---- Upload count limit ----
    if len(files) > MAX_FILES:
        raise HTTPException(400, detail=f"Too many files: {len(files)} (max {MAX_FILES}).")

    claim_id = f"CLM-{uuid.uuid4().hex[:10]}"
    updir = os.path.join(settings.storage_dir, "uploads", claim_id)
    os.makedirs(updir, exist_ok=True)
    docs = []
    for i, f in enumerate(files):
        # ---- Content-type / extension validation ----
        ext = os.path.splitext(os.path.basename(f.filename or ""))[1].lower()[:10]
        if (f.content_type not in ALLOWED_CONTENT_TYPES) and (ext not in ALLOWED_EXTENSIONS):
            raise HTTPException(415, detail=(f"Unsupported file type for '{f.filename}'. "
                                             "Allowed: PNG, JPEG, PDF."))
        # Security: never build the on-disk path from the client filename (path-traversal).
        # Use a server-generated name; keep the original filename only as display metadata.
        file_id = f"F{i+1:03d}"
        path = os.path.join(updir, f"{file_id}{ext}")
        # ---- Size cap: stream in chunks so we never load an oversized file fully into memory ----
        written = 0
        with open(path, "wb") as out:
            while chunk := f.file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_FILE_BYTES:
                    out.close()
                    os.remove(path)
                    raise HTTPException(413, detail=(f"File '{f.filename}' exceeds the "
                                                     f"{MAX_FILE_BYTES // (1024 * 1024)} MB limit."))
                out.write(chunk)
        # Encrypt the source document at rest (the densest PHI) BEFORE it is placed/
        # mirrored by the object store. Gated by phi_encryption_enabled → OFF in
        # dev/test/eval (files stay plaintext; every reader passes through unchanged),
        # ON in production. A failure here never breaks ingest (file stays plaintext).
        if settings.phi_encryption_enabled:
            try:
                crypto.encrypt_file_in_place(path)
            except Exception as e:  # noqa: BLE001 — encryption must not break ingest
                log.warning("source-doc encryption failed for %s: %s", file_id, e)
        # Route the stored file through the object_store abstraction. In LOCAL mode
        # (default) this is a no-op: the file already lives at `path` under storage_dir
        # and put() returns that exact path, so stored_path/serving is unchanged. In
        # minio mode the bytes are additionally mirrored to the bucket (best-effort).
        try:
            key = storage_key("uploads", claim_id, f"{file_id}{ext}")
            path = get_object_store().put(key, path)
        except Exception as e:  # noqa: BLE001 — object_store must never break ingest
            log.warning("object_store.put failed for %s; using local path: %s", file_id, e)
        docs.append(DocumentInput(file_id=file_id, file_name=f.filename, stored_path=path))

    # ---- Input validation: malformed JSON / invalid submission → clean 422, not 500 ----
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as e:
        raise HTTPException(422, detail=f"Invalid payload JSON: {e}")
    try:
        sub = ClaimSubmission(**data, documents=docs)
    except (ValidationError, TypeError) as e:
        raise HTTPException(422, detail=f"Invalid claim submission: {e}")
    _accumulate_history(sub)
    return claim_id, sub


def _accumulate_history(sub: ClaimSubmission) -> None:
    """API-layer history accumulation (annual + family-floater). This is the ONLY
    place accumulation happens: the eval runner calls run_claim directly and never
    invokes this, so the 12 cases stay unchanged. Best-effort — a DB hiccup leaves
    the submission as-is (rules then skip the annual/floater checks)."""
    from app.services.accumulation import (member_ytd, family_floater_used,
                                            member_alt_med_sessions_ytd)
    pe = get_policy_engine(settings.policy_path)
    try:
        # Only fill YTD when the caller didn't supply it — an explicit value wins
        # (mirrors the eval/test path that passes its own ytd_claims_amount).
        if sub.ytd_claims_amount is None:
            sub.ytd_claims_amount = member_ytd(sub.member_id, pe)
        # Floater utilisation is always computed from history (the caller never
        # supplies it); None only if the floater is disabled in policy.
        if pe.family_floater().get("enabled"):
            sub.floater_used_amount = family_floater_used(sub.member_id, pe)
        # Alt-medicine session count (consumed only by the gated session-cap rule).
        if sub.claim_category == "ALTERNATIVE_MEDICINE" and sub.alt_med_sessions_ytd is None:
            sub.alt_med_sessions_ytd = member_alt_med_sessions_ytd(sub.member_id, pe)
    except Exception as e:  # noqa: BLE001 — accumulation must never break ingest
        log.warning("history accumulation failed for member %s; continuing without it: %s",
                    sub.member_id, e)


def _run_and_persist(sub: ClaimSubmission, claim_id: str) -> dict:
    """Run the pipeline synchronously and persist (best-effort). Shared by the sync
    endpoint and the async broker-down fallback."""
    state = run_claim(sub)
    result = state_to_result(state, claim_id)
    # ---- Persistence must not crash a completed (expensive) claim: a DB outage logs a
    #      warning but we still return the computed result. ----
    try:
        persistence.save_claim(sub, result)
    except Exception as e:
        log.warning("save_claim failed for %s; returning result without persisting: %s", claim_id, e)
    # Immutable audit log: append the (non-PHI) decision summary. Best-effort and
    # strictly non-blocking — an audit failure never affects the returned result.
    try:
        from app.services.audit import record_decision
        if result.decision is not None:
            record_decision(claim_id, result.decision, actor="system")
    except Exception as e:  # noqa: BLE001 — auditing must never block the response
        log.warning("audit record_decision failed for %s (non-blocking): %s", claim_id, e)
    return result.model_dump(mode="json")


def _idempotent_replay(idempotency_key: str | None) -> dict | None:
    """If `idempotency_key` was seen before and its claim is still stored, return
    that prior ClaimResult JSON tagged with idempotent_replay=True (so a network
    retry / double-submit gets the original result instead of re-processing). Returns
    None when there is no key, the key is unseen, or the prior claim has aged out of
    storage — in all of which the caller processes the submit normally."""
    if not idempotency_key:
        return None
    prior_claim_id = get_idempotency_store().get(idempotency_key)
    if not prior_claim_id:
        return None
    stored = persistence.get_claim(prior_claim_id)
    if stored is None:
        return None  # mapping outlived the claim record → process as new
    return {**stored, "idempotent_replay": True}


def _claim_member_id(claim_id: str | None) -> str | None:
    """Recover the owning member_id for a claim from its stored submission (the
    ClaimResult has no top-level member_id). None if the claim/submission is absent."""
    if not claim_id:
        return None
    sub = persistence.get_submission(claim_id)
    return (sub or {}).get("member_id") if sub else None


def _reconstructed_facts_or_404(claim_id: str):
    """Bundle a stored ClaimResult + its submission and reconstruct a facts object,
    or raise 404. Returns (facts, member_id) so the caller can scope access."""
    from app.services.counterfactual import reconstruct_facts
    result = persistence.get_claim(claim_id)
    submission = persistence.get_submission(claim_id)
    if result is None or submission is None:
        raise HTTPException(404, "claim not found")
    facts = reconstruct_facts({**result, "submission": submission})
    return facts, submission.get("member_id")


def _claim_facts_for_chat(result: dict) -> str:
    """Flatten the stored ClaimResult into a compact plain-text fact sheet the
    chat model answers FROM. Only fields already computed for this claim — no
    policy lookups, no invented context."""
    lines: list[str] = []
    d = result.get("decision") or {}
    if result.get("blocked"):
        lines.append("This claim was BLOCKED before a decision was made.")
        for p in result.get("problems") or []:
            lines.append(f"- Issue ({p.get('kind')}): {p.get('message')}")
    if d:
        lines.append(f"Decision status: {d.get('status')}")
        lines.append(f"Approved amount: ₹{d.get('approved_amount')}")
        if d.get("member_message"):
            lines.append(f"Member message: {d.get('member_message')}")
        for rc in d.get("reason_codes") or []:
            lines.append(f"- Reason ({rc.get('code')}): {rc.get('detail')}")
        for rec in d.get("recommendations") or []:
            lines.append(f"- Recommendation: {rec}")
        fin = d.get("financial") or {}
        if fin:
            lines.append(f"Gross covered: ₹{fin.get('gross')}; "
                         f"network discount: ₹{fin.get('network_discount_amount')} "
                         f"({fin.get('network_discount_pct')}%); "
                         f"co-pay: ₹{fin.get('copay_amount')} ({fin.get('copay_pct')}%); "
                         f"approved: ₹{fin.get('approved_amount')}.")
            for step in fin.get("steps") or []:
                lines.append(f"- Calculation: {step}")
    for t in result.get("trace") or []:
        if t.get("summary"):
            lines.append(f"- Step [{t.get('agent')}] {t.get('status')}: {t.get('summary')}")
    return "\n".join(lines) if lines else "No decision details are available for this claim."


_EXT_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
}


def _content_type_for(path: str) -> str:
    ext = os.path.splitext(path or "")[1].lower()
    return _EXT_CONTENT_TYPES.get(ext, "application/octet-stream")


def _doc_types_by_file_id(result: dict | None) -> dict[str, str]:
    """Recover per-document doc_type from the stored result. The ClaimResult has
    no top-level extractions, but the extraction trace summarises each document as
    '<file_id> → <DOC_TYPE>; ...'. Parse that defensively; fall back to UNKNOWN."""
    out: dict[str, str] = {}
    if not result:
        return out
    for entry in result.get("trace", []) or []:
        if entry.get("agent") != "extraction":
            continue
        summary = entry.get("summary") or ""
        if "→" not in summary:
            continue
        left, _, right = summary.partition("→")
        file_id = left.strip()
        doc_type = right.split(";", 1)[0].strip()
        if file_id and doc_type:
            out[file_id] = doc_type
    return out


def _documents_for(claim_id: str) -> list[dict]:
    """Build the document metadata list from the stored submission + result.
    Raises HTTPException(404) if the claim is unknown."""
    submission = persistence.get_submission(claim_id)
    if submission is None:
        raise HTTPException(404, "claim not found")
    doc_types = _doc_types_by_file_id(persistence.get_claim(claim_id))
    docs = []
    for d in submission.get("documents", []) or []:
        fid = d.get("file_id")
        docs.append({
            "file_id": fid,
            "file_name": d.get("file_name"),
            "doc_type": doc_types.get(fid, "UNKNOWN"),
            "content_type": _content_type_for(d.get("stored_path") or d.get("file_name") or ""),
        })
    return docs
