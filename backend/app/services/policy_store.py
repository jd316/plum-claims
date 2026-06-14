"""Policy-as-code studio store.

Manages POLICY VERSIONS in the DB and (only on explicit activate) writes the chosen
version's JSON to the file the rules engine reads (``settings.policy_path``), then
invalidates the policy cache so the next decision picks it up. The studio NEVER
auto-activates: the default active version is v1 == the original ``policy_terms.json``,
so the live engine — and the 12/12 eval — are byte-identical until an operator
deliberately activates a different version.

Design that keeps the eval safe:
  * versions live in `policy_versions` (id, version_no, label, policy_json JSONB,
    is_active, created_by, created_at).
  * `seed_initial_version()` is idempotent: it inserts the CURRENT file as v1 active
    only when the table is empty. Re-running never dupes.
  * `create_version()` validates + stores an INACTIVE version. It does not touch the
    file or the cache.
  * `activate_version()` writes that version's JSON to `settings.policy_path`,
    flips is_active, invalidates the policy cache, and records an audit row.
  * preview (see `preview_decision`) is READ-ONLY: it loads a CANDIDATE engine from
    a candidate JSON in a temp file and compares the deterministic decision under the
    candidate vs the active policy — it never touches the live file or cache.
"""
from __future__ import annotations

import json
import logging
import tempfile
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # annotation-only — avoids a runtime import cycle
    from app.services.preview_sample import SampleSpec

from sqlalchemy import (String, Integer, Boolean, DateTime, JSON, Index,
                        select, func)
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.services.persistence import Base, Session
from app.services.policy_engine import (PolicyEngine, get_policy_engine,
                                        invalidate_policy_cache)

log = logging.getLogger("plum.policy_store")

# Top-level keys a candidate policy MUST carry for the rules engine to function.
# A candidate missing any of these is rejected (the caller maps this to HTTP 422).
REQUIRED_KEYS = (
    "coverage", "opd_categories", "waiting_periods", "exclusions",
    "document_requirements", "fraud_thresholds", "members",
)


class PolicyValidationError(ValueError):
    """A candidate policy JSON is structurally invalid (missing required keys)."""


class PolicyVersionRow(Base):
    """One stored policy version. The JSONB `policy_json` is the full policy document.
    Exactly one row is `is_active` at a time; that row's JSON mirrors the file the
    engine reads (kept in sync by `activate_version`)."""
    __tablename__ = "policy_versions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    policy_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    # Optional VERBATIM source text of the policy. Set for v1 (the seeded original file)
    # so re-activating v1 restores the file BYTE-IDENTICALLY — the eval-safety guarantee.
    # When None (operator-created versions), activation serializes policy_json instead.
    policy_text: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (Index("ix_policy_versions_version_no", "version_no"),)


# --------------------------------------------------------------------------- #
# Validation                                                                   #
# --------------------------------------------------------------------------- #

def validate_policy(policy_json: dict) -> None:
    """Light structural check: the candidate must be a dict carrying every key the
    rules engine reads. Raises PolicyValidationError listing what is missing."""
    if not isinstance(policy_json, dict):
        raise PolicyValidationError("policy must be a JSON object")
    missing = [k for k in REQUIRED_KEYS if k not in policy_json]
    if missing:
        raise PolicyValidationError(
            "policy is missing required key(s): " + ", ".join(missing))


# --------------------------------------------------------------------------- #
# Serialization helpers                                                        #
# --------------------------------------------------------------------------- #

def _row_to_meta(row: PolicyVersionRow) -> dict:
    """Version metadata WITHOUT the full policy_json (for list views)."""
    return {
        "id": row.id,
        "version_no": row.version_no,
        "label": row.label,
        "is_active": row.is_active,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _row_to_full(row: PolicyVersionRow) -> dict:
    return {**_row_to_meta(row), "policy_json": row.policy_json}


def _read_current_file() -> tuple[dict, str]:
    """Load the policy document currently on disk at settings.policy_path, returning
    both the parsed dict and the VERBATIM source text (for byte-identical restore)."""
    with open(settings.policy_path) as f:
        text = f.read()
    return json.loads(text), text


# --------------------------------------------------------------------------- #
# Store operations                                                             #
# --------------------------------------------------------------------------- #

def seed_initial_version() -> dict | None:
    """Idempotently insert the CURRENT policy file as v1 (active) when the table is
    empty. Returns the seeded row's meta, or None if versions already exist.

    This is the keystone of the eval-safety guarantee: the default active version is
    the original file, so until an operator activates something else, active == file
    == original and the 12/12 eval is unchanged."""
    with Session() as s:
        existing = s.execute(select(func.count()).select_from(PolicyVersionRow)).scalar() or 0
        if existing:
            return None
        policy, text = _read_current_file()
        row = PolicyVersionRow(version_no=1, label="Initial (original policy_terms.json)",
                               policy_json=policy, policy_text=text,
                               is_active=True, created_by="system")
        s.add(row)
        s.commit()
        return _row_to_meta(row)


def list_versions() -> list[dict]:
    """All versions, newest version_no first (metadata only — no full JSON)."""
    with Session() as s:
        rows = s.execute(
            select(PolicyVersionRow).order_by(PolicyVersionRow.version_no.desc())
        ).scalars().all()
        return [_row_to_meta(r) for r in rows]


def get_version(version_id: str) -> dict | None:
    """One version with its full policy_json, or None if unknown."""
    with Session() as s:
        row = s.get(PolicyVersionRow, version_id)
        return _row_to_full(row) if row else None


def get_active() -> dict | None:
    """The active version with its full policy_json, or None if none seeded yet."""
    with Session() as s:
        row = s.execute(
            select(PolicyVersionRow).where(PolicyVersionRow.is_active.is_(True))
        ).scalars().first()
        return _row_to_full(row) if row else None


def _next_version_no(s) -> int:
    current = s.execute(select(func.max(PolicyVersionRow.version_no))).scalar()
    return (current or 0) + 1


def create_version(policy_json: dict, label: str | None = None,
                   actor: str = "system") -> dict:
    """Validate + store a new INACTIVE version. Does NOT activate, write the file, or
    touch the cache. Raises PolicyValidationError on a structurally invalid candidate."""
    validate_policy(policy_json)
    with Session() as s:
        version_no = _next_version_no(s)
        row = PolicyVersionRow(version_no=version_no, label=label,
                               policy_json=policy_json, is_active=False, created_by=actor)
        s.add(row)
        s.commit()
        return _row_to_full(row)


def activate_version(version_id: str, actor: str = "system") -> dict:
    """Activate the given version: write its JSON to settings.policy_path (the file the
    engine reads), flip is_active (clearing it on every other row), invalidate the
    policy cache so the next decision re-reads the file, and record an audit row.

    This is the ONLY operation that mutates the live policy file."""
    with Session() as s:
        row = s.get(PolicyVersionRow, version_id)
        if row is None:
            raise KeyError(f"unknown policy version {version_id}")
        # Validate before we ever touch the live file.
        validate_policy(row.policy_json)
        # Write the chosen policy to the file the engine reads. Prefer the verbatim
        # source text when present (v1 → byte-identical restore of the original file);
        # otherwise pretty-print the JSON.
        with open(settings.policy_path, "w") as f:
            if row.policy_text is not None:
                f.write(row.policy_text)
            else:
                json.dump(row.policy_json, f, indent=2, ensure_ascii=False)
                f.write("\n")
        # Flip active flags: exactly one active row.
        for other in s.execute(select(PolicyVersionRow)).scalars().all():
            other.is_active = (other.id == version_id)
        s.commit()
        meta = _row_to_meta(row)
        version_no = row.version_no
    # Drop the cached engine for this path so the next get_policy_engine re-reads it.
    invalidate_policy_cache(settings.policy_path)
    _record_activation_audit(version_id, version_no, actor)
    return meta


def _record_activation_audit(version_id: str, version_no: int, actor: str) -> None:
    """Best-effort audit row for an activation, reusing the append-only audit_log
    table. Carries no PHI — just the policy version activated + actor."""
    try:
        from app.services.audit import AuditLogRow
        with Session() as s:
            s.add(AuditLogRow(
                id=uuid.uuid4().hex,
                claim_id=f"policy:{version_id}",
                actor=actor,
                action="POLICY_ACTIVATE",
                decision_status=f"v{version_no}",
                approved_amount=None,
                reason_codes=None,
            ))
            s.commit()
    except Exception as e:  # noqa: BLE001 — auditing must never block activation
        log.warning("policy activation audit failed (non-blocking): %s", e)


# --------------------------------------------------------------------------- #
# Structured diff                                                              #
# --------------------------------------------------------------------------- #

def _flatten(obj, prefix: str = "") -> dict[str, object]:
    """Flatten a nested dict/list into {dotted.path: leaf_value}. Lists are indexed."""
    out: dict[str, object] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def diff_policies(a: dict, b: dict) -> list[dict]:
    """Structured leaf-path diff between two policy dicts. Returns a list of
    {path, before, after, change} for every changed/added/removed leaf, sorted by path."""
    fa, fb = _flatten(a), _flatten(b)
    paths = sorted(set(fa) | set(fb))
    changes: list[dict] = []
    for p in paths:
        before = fa.get(p, None)
        after = fb.get(p, None)
        if p not in fa:
            changes.append({"path": p, "before": None, "after": after, "change": "added"})
        elif p not in fb:
            changes.append({"path": p, "before": before, "after": None, "change": "removed"})
        elif before != after:
            changes.append({"path": p, "before": before, "after": after, "change": "changed"})
    return changes


def diff_versions(a_id: str, b_id: str) -> dict:
    """Diff two stored versions by id. Returns {a, b, changes}."""
    a = get_version(a_id)
    b = get_version(b_id)
    if a is None:
        raise KeyError(f"unknown policy version {a_id}")
    if b is None:
        raise KeyError(f"unknown policy version {b_id}")
    return {
        "a": {k: a[k] for k in ("id", "version_no", "label")},
        "b": {k: b[k] for k in ("id", "version_no", "label")},
        "changes": diff_policies(a["policy_json"], b["policy_json"]),
    }


# --------------------------------------------------------------------------- #
# READ-ONLY impact preview                                                     #
# --------------------------------------------------------------------------- #

def _candidate_engine(policy_json: dict) -> PolicyEngine:
    """Build a PolicyEngine from a candidate policy WITHOUT touching the live file or
    the shared cache: write to a throwaway temp file and load directly."""
    validate_policy(policy_json)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        json.dump(policy_json, tf)
        tmp_path = tf.name
    try:
        return PolicyEngine(tmp_path)
    finally:
        import os
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _decision_summary(decision) -> dict:
    """Compact, JSON-friendly view of a Decision for the before/after preview."""
    return {
        "status": decision.status,
        "approved_amount": decision.approved_amount,
        "reason_codes": [{"code": c.code, "detail": c.detail} for c in decision.reason_codes],
    }


def preview_decision(candidate_policy: dict, sample: SampleSpec) -> dict:
    """READ-ONLY impact preview. Runs the SAME sample claim through the deterministic
    decision under (a) the ACTIVE policy and (b) the CANDIDATE policy, and returns
    before/after. Never writes the live file and never invalidates the cache.

    `sample` is a SampleSpec built from a test-case id or an inline claim. The candidate
    decision is computed against a throwaway temp engine; the active decision uses the
    cached live engine (the same one the pipeline reads), so the comparison is honest."""
    from app.evalrunner.decision_eval import decide_from_facts

    active_engine = get_policy_engine(settings.policy_path)
    candidate_engine = _candidate_engine(candidate_policy)

    before_case = sample.to_case(active_engine)
    after_case = sample.to_case(candidate_engine)
    before = decide_from_facts(before_case, active_engine)
    after = decide_from_facts(after_case, candidate_engine)

    return {
        "sample": sample.describe(),
        "before": _decision_summary(before),
        "after": _decision_summary(after),
        "changed": _decision_summary(before) != _decision_summary(after),
    }
