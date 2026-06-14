"""Ops inline field correction over the DETERMINISTIC decision layer.

An operator reviewing a claim can correct a low-confidence EXTRACTED field (a
misread bill total, patient name, diagnosis, or the line-items table). The system
re-runs the DETERMINISTIC decision on the corrected facts and persists the corrected
outcome with an append-only audit trail. NO Gemini — the re-decide is exact and
instant, built ENTIRELY on the existing machinery:

  * `reconstruct_facts(stored)`  (app.services.counterfactual) rebuilds the structured
    facts (submission + extractions + semantic + member) from the stored ClaimResult.
  * `decide_from_facts(case, pe)` (app.evalrunner.decision_eval) re-runs the 5 rule
    checks + financial + aggregator exactly as the pipeline's decide stage.

The corrected decision + corrected extractions become the new stored state; the
ORIGINAL decision is appended to `correction_history` and NEVER lost. An audit row
(record_correction) captures the actor + which fields changed + original→new outcome.

PURE-ADDITIVE & deterministic. The live extraction pipeline and the 12 cases are
untouched. The only mutation is to the single stored claim being corrected.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.config import settings
from app.models.schemas import ClaimResult, ExtractionResult, LineItem
from app.services.policy_engine import get_policy_engine
from app.services.counterfactual import reconstruct_facts
from app.evalrunner.decision_eval import decide_from_facts

# Editable StrField extraction fields ops may correct (NumField total_amount and the
# line_items list are handled specially below). Restricting to a whitelist keeps the
# correction honest (you cannot inject arbitrary attributes onto the extraction).
_STR_FIELDS = {"patient_name", "doctor_name", "doctor_registration",
               "diagnosis", "treatment", "hospital_name", "document_date"}


class CorrectionError(ValueError):
    """A correction referenced an unknown document/field or carried a bad value."""


def _find_extraction(extractions: list[ExtractionResult], file_id: str) -> ExtractionResult:
    for e in extractions:
        if e.file_id == file_id:
            return e
    raise CorrectionError(f"no extracted document with file_id {file_id!r}")


def _apply_one(extraction: ExtractionResult, field: str, value) -> dict:
    """Apply a single field correction to an ExtractionResult IN PLACE (on the
    reconstructed, non-persisted copy). Sets the new value, bumps confidence to 1.0,
    and returns a NON-PHI change descriptor (field name + before/after confidence,
    no PHI values) for the correction_history / audit. Raises CorrectionError on a
    bad field or value."""
    if field == "line_items":
        if not isinstance(value, list):
            raise CorrectionError("line_items correction must be a list of {description, amount}")
        new_items: list[LineItem] = []
        for raw in value:
            if not isinstance(raw, dict) or "amount" not in raw:
                raise CorrectionError("each line item needs at least an 'amount'")
            new_items.append(LineItem(
                description=str(raw.get("description") or "Corrected line"),
                amount=float(raw["amount"]),
                confidence=1.0,
                is_branded=raw.get("is_branded"),
            ))
        before_n = len(extraction.line_items)
        extraction.line_items = new_items
        # Keep the bill total consistent with the corrected lines.
        extraction.total_amount.value = round(sum(li.amount for li in new_items), 2)
        extraction.total_amount.confidence = 1.0
        return {"file_id": extraction.file_id, "field": "line_items",
                "before_count": before_n, "after_count": len(new_items),
                "corrected_total": extraction.total_amount.value}

    if field == "total_amount":
        try:
            amount = float(value)
        except (TypeError, ValueError):
            raise CorrectionError("total_amount must be a number")
        before_conf = extraction.total_amount.confidence
        extraction.total_amount.value = round(amount, 2)
        extraction.total_amount.confidence = 1.0
        # If the bill has exactly one line, mirror the corrected total onto it so the
        # financial calculator (which reads line items) sees the corrected figure.
        if len(extraction.line_items) == 1:
            extraction.line_items[0].amount = round(amount, 2)
        return {"file_id": extraction.file_id, "field": "total_amount",
                "before_confidence": before_conf, "after_confidence": 1.0,
                "human_corrected": True}

    if field in _STR_FIELDS:
        sf = getattr(extraction, field)
        before_conf = sf.confidence
        sf.value = None if value is None else str(value)
        sf.confidence = 1.0
        return {"file_id": extraction.file_id, "field": field,
                "before_confidence": before_conf, "after_confidence": 1.0,
                "human_corrected": True}

    raise CorrectionError(
        f"field {field!r} is not correctable (allowed: total_amount, line_items, "
        f"{', '.join(sorted(_STR_FIELDS))})")


def _decision_summary(decision) -> dict:
    return {
        "status": decision.status if decision else None,
        "amount": decision.approved_amount if decision else None,
        "reason_codes": [{"code": c.code, "detail": c.detail}
                         for c in (decision.reason_codes if decision else [])],
    }


def apply_correction(stored_result: dict, submission: dict, corrections: list[dict],
                     actor: str = "ops") -> tuple[ClaimResult, dict]:
    """Apply ops field corrections to a stored claim and re-decide deterministically.

    Inputs are the stored ClaimResult dict + its submission dict (exactly what
    persistence.get_claim / get_submission return) and a list of corrections
    ``[{file_id, field, value}, ...]``. Pure function over the inputs: it builds a
    fresh ClaimResult and never touches the DB (the endpoint persists + audits).

    Steps (all deterministic, no Gemini):
      1. reconstruct_facts(stored + submission) → a facts object whose `extractions`
         are model copies we can safely mutate.
      2. apply each correction to the corresponding ExtractionResult (set value, bump
         confidence to 1.0; for line_items replace the list + sync the total).
      3. decide_from_facts(facts) → the new Decision on the CORRECTED facts.
      4. build the new ClaimResult: corrected extractions + new decision become the
         new state; the ORIGINAL decision is appended to correction_history (append-
         only — never lost); corrected_by / corrected_at are stamped.

    Returns (new_result, summary) where summary = {before, after, changed_fields,
    changed_rules} for the endpoint response + audit. Raises CorrectionError for an
    unknown document/field or a malformed value (the endpoint maps it to a 422)."""
    if not corrections:
        raise CorrectionError("no corrections supplied")

    original = ClaimResult(**stored_result)
    original_decision = original.decision  # preserved verbatim below

    # 1) Reconstruct facts (extractions are deep-copied model instances we can mutate).
    facts = reconstruct_facts({**stored_result, "submission": submission})
    pe = get_policy_engine(settings.policy_path)

    # Decision BEFORE the correction, computed from the SAME facts/engine path used to
    # re-decide, so before↔after are strictly comparable (verdict-level diffing too).
    before_decision, before_verdicts = _decide_with_verdicts(facts, pe)

    # 2) Apply each correction in order; collect non-PHI change descriptors.
    change_records: list[dict] = []
    changed_field_names: list[str] = []
    financial_touched = False
    for corr in corrections:
        file_id = corr.get("file_id")
        field = corr.get("field")
        if not file_id or not field:
            raise CorrectionError("each correction needs a file_id and a field")
        extraction = _find_extraction(facts.extractions, file_id)
        change_records.append(_apply_one(extraction, field, corr.get("value")))
        changed_field_names.append(field)
        if field in ("total_amount", "line_items"):
            financial_touched = True

    # Keep submission.claimed_amount consistent with the corrected bill totals: the
    # limits rule (and the financial fallback) read claimed_amount, so a corrected
    # total/line-items must be reflected there or the re-decide would mix old+new
    # figures. Mirrors counterfactual._set_claimed. Submission JSONB is NOT persisted
    # (the endpoint only updates the result), so the original submission is untouched.
    if financial_touched:
        bill_total = sum(
            e.total_amount.value or 0.0
            for e in facts.extractions
            if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL") and e.total_amount.value)
        if bill_total > 0:
            facts.submission.claimed_amount = round(bill_total, 2)

    # 3) Re-decide on the CORRECTED facts (deterministic).
    after_decision, after_verdicts = _decide_with_verdicts(facts, pe)

    changed_rules = _changed_rules(before_verdicts, after_verdicts)

    # 4) Assemble the new persisted state. Corrected extractions + new decision are the
    #    new truth; the ORIGINAL decision is appended to history (never overwritten).
    now = datetime.now(timezone.utc).isoformat()
    history_entry = {
        "corrected_at": now,
        "corrected_by": actor,
        "changed_fields": change_records,
        "before": _decision_summary(original_decision),
        "after": _decision_summary(after_decision),
        "changed_rules": changed_rules,
    }

    new_result = original.model_copy(deep=True)
    new_result.decision = after_decision
    new_result.extractions = facts.extractions
    new_result.corrected_by = actor
    new_result.corrected_at = now
    # Append-only: keep prior history (legacy = []), then add this correction last.
    new_result.correction_history = list(original.correction_history) + [history_entry]

    summary = {
        "before": {"status": before_decision.status,
                   "amount": before_decision.approved_amount},
        "after": {"status": after_decision.status,
                  "amount": after_decision.approved_amount},
        "changed_fields": change_records,
        "changed_rules": changed_rules,
    }
    return new_result, summary


def _decide_with_verdicts(facts, pe):
    """Re-decide AND surface the per-rule verdicts so we can report which rules
    changed. decide_from_facts is the single source of truth for the Decision; the
    verdicts come from the shared counterfactual._decide path (same rules/order)."""
    from app.services.counterfactual import _decide as _cf_decide
    decision = decide_from_facts(facts, pe)
    _, verdicts = _cf_decide(facts, pe)
    return decision, verdicts


def _changed_rules(before, after) -> list[dict]:
    """Which rule verdicts changed (status or reason_code) before→after the correction."""
    by_after = {v.rule: v for v in after}
    out: list[dict] = []
    for b in before:
        a = by_after.get(b.rule)
        if a and (a.status != b.status or a.reason_code != b.reason_code):
            out.append({"rule": b.rule,
                        "before": {"status": b.status, "reason_code": b.reason_code},
                        "after": {"status": a.status, "reason_code": a.reason_code}})
    return out
