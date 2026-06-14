"""Production policy-enforcement flags (architecture.md §10a: deferred → enforced-but-gated).

Every rule here is gated OFF by default so the 12-case + synthetic eval are unchanged.
These tests prove (a) the default-OFF no-op and (b) the enforcement when the flag is ON.
We toggle settings via monkeypatch so the global default is never mutated across tests."""
from datetime import date


from app.config import settings
from app.graph.nodes import intake
from app.models.schemas import (ClaimSubmission, DocumentInput, ExtractionResult, LineItem,
                                 SemanticMapping)
from app.rules import limits, fraud, waiting_period, coverage_exclusion
from app.rules.base import RuleContext
from app.rules.financial import calculate
from app.services.policy_engine import PolicyEngine
from tests.conftest import REPO_ROOT

PE = PolicyEngine(str(REPO_ROOT / "policy_terms.json"))


def _sub(**kw) -> ClaimSubmission:
    base = dict(member_id="EMP001", policy_id="PLUM_GHI_2024", claim_category="CONSULTATION",
                treatment_date=date(2024, 11, 1), claimed_amount=1500.0,
                documents=[DocumentInput(file_id="F1", file_name="b.pdf", stored_path="/tmp/x.pdf")])
    base.update(kw)
    return ClaimSubmission(**base)


def _ctx(sub: ClaimSubmission, items=None, semantic=None, member=None,
         extractions=None) -> RuleContext:
    ex = extractions if extractions is not None else [
        ExtractionResult(file_id="F1", doc_type="HOSPITAL_BILL", line_items=items or [])]
    return RuleContext(submission=sub, member=member or PE.member(sub.member_id),
                       extractions=ex, semantic=semantic or SemanticMapping(), pe=PE)


# --- Phase 1b: submission deadline -----------------------------------------

def test_deadline_off_by_default_does_not_block_2024_claim():
    # Default OFF: a claim submitted long after the 2024 treatment date must NOT be blocked.
    sub = _sub(submission_date=date(2025, 6, 1))
    out = intake({"submission": sub})
    assert not out.get("problems")


def test_deadline_on_blocks_late_claim_with_specific_message(monkeypatch):
    monkeypatch.setattr(settings, "submission_deadline_enabled", True)
    sub = _sub(treatment_date=date(2024, 11, 1), submission_date=date(2024, 12, 15))  # 44 days
    out = intake({"submission": sub})
    msgs = [p.message for p in out.get("problems", [])]
    assert any("within 30 days" in m and "2024-12-01" in m and "44 days" in m for m in msgs), msgs


def test_deadline_on_allows_within_window(monkeypatch):
    monkeypatch.setattr(settings, "submission_deadline_enabled", True)
    sub = _sub(treatment_date=date(2024, 11, 1), submission_date=date(2024, 11, 20))  # 19 days
    out = intake({"submission": sub})
    assert not out.get("problems")


# --- Phase 1c: configurable consultation sub-limit scope --------------------

def test_consultation_per_line_item_scope_is_not_capped_at_sub_limit():
    # Default reading (reproduces TC010): consultation ₹4,500 at a network hospital is NOT
    # capped at the ₹2,000 consultation sub_limit; per_claim_limit governs instead.
    assert settings.sub_limit_scope == "per_line_item"
    items = [LineItem(description="Consultation Fee", amount=1500),
             LineItem(description="Medicines", amount=3000)]
    fb = calculate(PE, "CONSULTATION", is_network=True, items=items, disallowed=[])
    assert fb.approved_amount == 3240.0  # 4500 −20% → 3600 −10% → 3240, uncapped


def test_consultation_whole_claim_scope_caps_at_sub_limit(monkeypatch):
    # Literal reading: consultation sub_limit (₹2,000) caps the whole claim.
    monkeypatch.setattr(settings, "sub_limit_scope", "whole_claim")
    items = [LineItem(description="Consultation Fee", amount=1500),
             LineItem(description="Medicines", amount=3000)]
    fb = calculate(PE, "CONSULTATION", is_network=True, items=items, disallowed=[])
    assert fb.approved_amount == 2000.0  # capped at the consultation sub_limit
    # ...and the limits rule FAILs with a sub-limit reason code.
    v = limits.check(_ctx(_sub(claimed_amount=4500.0), items))
    assert v.status == "FAIL" and v.reason_code == "SUB_LIMIT_EXCEEDED"


# --- Phase 2a: category-mismatch routing -----------------------------------

def test_category_mismatch_off_by_default_passes():
    sm = SemanticMapping(category_match=False, mapped_category="DIAGNOSTIC", confidence=0.95)
    assert fraud.check(_ctx(_sub(), semantic=sm)).status == "PASS"


def test_category_mismatch_on_routes_to_manual_review(monkeypatch):
    monkeypatch.setattr(settings, "category_match_enforcement_enabled", True)
    sm = SemanticMapping(category_match=False, mapped_category="DIAGNOSTIC", confidence=0.95)
    v = fraud.check(_ctx(_sub(), semantic=sm))
    assert v.status == "FLAG" and "category mismatch" in v.detail


def test_category_mismatch_below_confidence_does_not_flag(monkeypatch):
    monkeypatch.setattr(settings, "category_match_enforcement_enabled", True)
    sm = SemanticMapping(category_match=False, mapped_category="DIAGNOSTIC", confidence=0.4)
    assert fraud.check(_ctx(_sub(), semantic=sm)).status == "PASS"


# --- Phase 2b: pre-existing-condition waiting period -----------------------

def _ped_member(eligible_from: str | None) -> dict:
    m = dict(PE.member("EMP001"))
    if eligible_from:
        m["pre_existing_condition_eligible_from"] = eligible_from
    return m


def test_ped_off_by_default_passes():
    m = _ped_member("2025-04-01")  # marker present, but flag OFF → ignored
    assert waiting_period.check(_ctx(_sub(), member=m)).status == "PASS"


def test_ped_on_blocks_treatment_before_eligibility(monkeypatch):
    monkeypatch.setattr(settings, "pre_existing_condition_check_enabled", True)
    m = _ped_member("2025-04-01")
    v = waiting_period.check(_ctx(_sub(treatment_date=date(2024, 11, 1)), member=m))
    assert v.status == "FAIL" and v.reason_code == "WAITING_PERIOD" and "pre-existing" in v.detail


def test_ped_on_allows_member_without_marker(monkeypatch):
    monkeypatch.setattr(settings, "pre_existing_condition_check_enabled", True)
    assert waiting_period.check(_ctx(_sub(), member=_ped_member(None))).status == "PASS"


# --- Phase 2c: alt-med session cap + registered practitioner ----------------

def _altmed(**kw):
    return _sub(claim_category="ALTERNATIVE_MEDICINE", claimed_amount=4000.0, **kw)


def test_altmed_session_cap_on_blocks_over_limit(monkeypatch):
    monkeypatch.setattr(settings, "alt_med_session_limit_enabled", True)
    v = limits.check(_ctx(_altmed(alt_med_sessions_ytd=20)))  # 21st session > 20
    assert v.status == "FAIL" and v.reason_code == "SESSION_LIMIT_EXCEEDED"


def test_altmed_session_cap_off_by_default_passes():
    assert limits.check(_ctx(_altmed(alt_med_sessions_ytd=99))).status == "PASS"


def test_altmed_practitioner_on_requires_valid_registration(monkeypatch):
    monkeypatch.setattr(settings, "practitioner_registration_check_enabled", True)
    from app.models.schemas import StrField
    bad = [ExtractionResult(file_id="F1", doc_type="PRESCRIPTION",
                            doctor_registration=StrField(value="illegible"))]
    assert limits.check(_ctx(_altmed(), extractions=bad)).reason_code == "PRACTITIONER_NOT_REGISTERED"
    good = [ExtractionResult(file_id="F1", doc_type="PRESCRIPTION",
                             doctor_registration=StrField(value="AYUR/KL/2345/2019"))]
    assert limits.check(_ctx(_altmed(), extractions=good)).status == "PASS"


# --- Phase 2d: pharmacy generic_mandatory ----------------------------------

def test_generic_mandatory_off_by_default_keeps_branded_line():
    items = [LineItem(description="Crocin 650", amount=100, is_branded=True, has_generic_alternative=True)]
    v = coverage_exclusion.check(_ctx(_sub(claim_category="PHARMACY"), items))
    assert v.disallowed_items == []


def test_generic_mandatory_on_disallows_branded_with_generic(monkeypatch):
    monkeypatch.setattr(settings, "generic_mandatory_enabled", True)
    items = [LineItem(description="Crocin 650", amount=100, is_branded=True, has_generic_alternative=True),
             LineItem(description="Paracetamol 650", amount=20, is_branded=False)]
    v = coverage_exclusion.check(_ctx(_sub(claim_category="PHARMACY"), items))
    assert v.disallowed_items == ["Crocin 650"]
