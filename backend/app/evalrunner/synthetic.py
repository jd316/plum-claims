"""Synthetic labeled claim-case generator for the decision-layer eval.

Produces hundreds of reproducible, labeled claim scenarios. Each case bundles:
  * structured facts: a ``ClaimSubmission`` + a list of ``ExtractionResult`` with the
    decision-relevant fields populated + a ``SemanticMapping`` (the output the real
    LLM SemanticMap agent would produce — supplied here so the deterministic rules
    fire without Gemini), and
  * an INDEPENDENT ``expected`` outcome derived purely by the template's construction
    (status, optional reason_code, optional amount) — NOT by calling our rules.

The expected APPROVED/PARTIAL amount is computed by a SMALL reference formula
(`reference_payout`) that is intentionally separate from ``app.rules.financial`` so
the harness genuinely cross-checks the production arithmetic.

PURE-ADDITIVE: this module never touches the pipeline, the 12 cases, or any rule.

Determinism: cases are produced by fixed enumeration over (template, member,
category, amount) — no unseeded randomness — so the generated set is reproducible.

The matching facts mirror what the real pipeline feeds the rules:
  * `ClaimSubmission.claimed_amount` is what `limits`/`fraud`/`pre_auth` read.
  * line items on a HOSPITAL_BILL/PHARMACY_BILL `ExtractionResult` are what
    `financial`/`coverage_exclusion`/`limits` read via `ctx.line_items`.
  * `SemanticMapping.waiting_condition` / `.exclusion_candidates` drive the
    `waiting_period` / `coverage_exclusion` rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import cast

from app.models.schemas import (
    ClaimSubmission, ExtractionResult, SemanticMapping, LineItem, NumField,
    StrField, ClaimHistoryItem, ClaimCategory, DocType,
)
from app.services.policy_engine import PolicyEngine

# A network hospital (triggers the network discount) and a non-network one.
NETWORK_HOSPITAL = "Apollo Hospitals"
NON_NETWORK_HOSPITAL = "City Care Clinic"


@dataclass
class SyntheticCase:
    """One labeled scenario: structured facts + an independently-derived expected outcome."""
    case_id: str
    template: str
    submission: ClaimSubmission
    extractions: list[ExtractionResult]
    semantic: SemanticMapping
    expected: dict  # {status, [reason_code], [expected_amount]}
    note: str = ""


# --------------------------------------------------------------------------- #
# Independent reference payout (deliberately NOT app.rules.financial)          #
# --------------------------------------------------------------------------- #

def reference_payout(pe: PolicyEngine, category: str, is_network: bool,
                     covered_gross: float) -> float:
    """Small independent reference: gross -> (network? x(1-discount%)) -> x(1-copay%)
    -> cap at sub-limit for non-CONSULTATION categories. Mirrors the policy intent
    but is written separately from financial.py so the harness truly cross-checks it."""
    cat = pe.category_rules(category)
    amount = covered_gross
    if is_network:
        disc = float(cat.get("network_discount_percent", 0))
        amount = amount * (1 - disc / 100)
    copay = float(cat.get("copay_percent", 0))
    amount = amount * (1 - copay / 100)
    sub_limit = cat.get("sub_limit")
    if category != "CONSULTATION" and sub_limit is not None and amount > sub_limit:
        amount = float(sub_limit)
    return round(amount, 2)


# --------------------------------------------------------------------------- #
# Fact builders                                                                #
# --------------------------------------------------------------------------- #

def _bill(file_id: str, items: list[LineItem], hospital: str | None,
          patient: str, doc_type: str = "HOSPITAL_BILL") -> ExtractionResult:
    total = round(sum(i.amount for i in items), 2)
    return ExtractionResult(
        file_id=file_id, doc_type=cast(DocType, doc_type),
        patient_name=StrField(value=patient, confidence=0.97),
        hospital_name=StrField(value=hospital, confidence=0.95) if hospital else StrField(),
        line_items=items,
        total_amount=NumField(value=total, confidence=0.96),
    )


def _submission(member: dict, category: str, treatment_date: date, claimed: float,
                hospital: str | None, documents=None, history=None,
                ytd: float | None = None) -> ClaimSubmission:
    return ClaimSubmission(
        member_id=member["member_id"], policy_id="PLUM_GHI_2024",
        claim_category=cast(ClaimCategory, category), treatment_date=treatment_date,
        claimed_amount=round(claimed, 2), hospital_name=hospital,
        ytd_claims_amount=ytd, claims_history=history or [],
        documents=documents or [],
    )


# --------------------------------------------------------------------------- #
# Template builders — each yields a list[SyntheticCase]                        #
# --------------------------------------------------------------------------- #

# An eligible treatment date well outside every waiting period (members joined
# 2024-04-01; EMP005 joined 2024-09-01). 2025-06-01 is >270 days after both.
ELIGIBLE_DATE = date(2025, 6, 1)


def _clean_approval(pe: PolicyEngine, members: list[dict]) -> list[SyntheticCase]:
    """Covered category, within limits, network and non-network -> APPROVED.

    Amount kept small enough that post-policy payout < auto_manual_review_above and
    claimed_amount < high_value/same-day thresholds (no fraud flag)."""
    cases: list[SyntheticCase] = []
    # (category, gross) pairs chosen to stay well under sub-limits and the per-claim
    # limit (CONSULTATION). Gross is the bill's covered total == claimed_amount.
    plan = [
        ("CONSULTATION", 1800.0), ("CONSULTATION", 900.0),
        ("DIAGNOSTIC", 4000.0), ("DIAGNOSTIC", 7500.0),
        ("PHARMACY", 3000.0), ("PHARMACY", 9000.0),
        ("DENTAL", 5000.0), ("DENTAL", 2500.0),
        ("VISION", 2000.0), ("VISION", 3500.0),
        ("ALTERNATIVE_MEDICINE", 4000.0), ("ALTERNATIVE_MEDICINE", 6000.0),
    ]
    i = 0
    for member in members:
        for category, gross in plan:
            for is_network in (True, False):
                hospital = NETWORK_HOSPITAL if is_network else NON_NETWORK_HOSPITAL
                items = [LineItem(description=f"{category.title()} service", amount=gross)]
                bill = _bill(f"BILL-{i}", items, hospital, member["name"])
                sub = _submission(member, category, ELIGIBLE_DATE, gross, hospital)
                expected_amount = reference_payout(pe, category, is_network, gross)
                cases.append(SyntheticCase(
                    case_id=f"clean-{i:04d}", template="clean_approval",
                    submission=sub, extractions=[bill],
                    semantic=SemanticMapping(category_match=True, confidence=0.95),
                    expected={"status": "APPROVED", "expected_amount": expected_amount},
                    note=f"{category} {'network' if is_network else 'non-network'} gross={gross}",
                ))
                i += 1
    return cases


def _waiting_period(pe: PolicyEngine, members: list[dict]) -> list[SyntheticCase]:
    """Specific-condition waiting period -> REJECTED / WAITING_PERIOD.

    Treatment is AFTER the initial 30-day window (so the initial-waiting branch does
    not fire) but BEFORE the condition's waiting period elapses. The SemanticMap
    output sets waiting_condition to the matching condition (the LLM's job)."""
    cases: list[SyntheticCase] = []
    initial = pe.initial_waiting_days()
    conditions = list(pe.waiting_conditions().items())
    i = 0
    for member in members:
        join = date.fromisoformat(member["join_date"])
        for condition, wdays in conditions:
            # Pick a treatment day strictly inside (initial, wdays): day = initial + 5,
            # clamped below wdays. All specific conditions here have wdays >= 90 > 35.
            day_offset = min(initial + 5, wdays - 1)
            tdate = join + timedelta(days=day_offset)
            gross = 3000.0
            items = [LineItem(description="Consultation service", amount=gross)]
            bill = _bill(f"WP-{i}", items, NETWORK_HOSPITAL, member["name"])
            sub = _submission(member, "CONSULTATION", tdate, gross, NETWORK_HOSPITAL)
            cases.append(SyntheticCase(
                case_id=f"wait-{i:04d}", template="waiting_period",
                submission=sub, extractions=[bill],
                semantic=SemanticMapping(category_match=True, waiting_condition=condition,
                                         confidence=0.9),
                expected={"status": "REJECTED", "reason_code": "WAITING_PERIOD"},
                note=f"{condition} wdays={wdays} day={day_offset}",
            ))
            i += 1
    return cases


def _excluded_condition(pe: PolicyEngine, members: list[dict]) -> list[SyntheticCase]:
    """Diagnosis/treatment maps to a policy exclusion -> REJECTED / EXCLUDED_CONDITION.

    SemanticMap supplies the exclusion_candidates the LLM would propose."""
    cases: list[SyntheticCase] = []
    exclusions = pe.exclusion_conditions()
    i = 0
    for member in members:
        for excl in exclusions:
            gross = 4000.0
            items = [LineItem(description="Treatment service", amount=gross)]
            bill = _bill(f"EXC-{i}", items, NETWORK_HOSPITAL, member["name"])
            sub = _submission(member, "CONSULTATION", ELIGIBLE_DATE, gross, NETWORK_HOSPITAL)
            cases.append(SyntheticCase(
                case_id=f"excl-{i:04d}", template="excluded_condition",
                submission=sub, extractions=[bill],
                semantic=SemanticMapping(category_match=True,
                                         exclusion_candidates=[excl], confidence=0.9),
                expected={"status": "REJECTED", "reason_code": "EXCLUDED_CONDITION"},
                note=f"exclusion={excl}",
            ))
            i += 1
    return cases


def _per_claim_exceeded(pe: PolicyEngine, members: list[dict]) -> list[SyntheticCase]:
    """CONSULTATION amount > per_claim_limit -> REJECTED / PER_CLAIM_EXCEEDED.

    Amount kept below the high-value (25000) threshold so no fraud flag is raised."""
    cases: list[SyntheticCase] = []
    limit = pe.per_claim_limit()
    i = 0
    for member in members:
        for over in (limit + 500, limit + 3000, limit + 9000):
            items = [LineItem(description="Consultation service", amount=over)]
            bill = _bill(f"PCL-{i}", items, NETWORK_HOSPITAL, member["name"])
            sub = _submission(member, "CONSULTATION", ELIGIBLE_DATE, over, NETWORK_HOSPITAL)
            cases.append(SyntheticCase(
                case_id=f"pcl-{i:04d}", template="per_claim_exceeded",
                submission=sub, extractions=[bill],
                semantic=SemanticMapping(category_match=True, confidence=0.95),
                expected={"status": "REJECTED", "reason_code": "PER_CLAIM_EXCEEDED"},
                note=f"amount={over} limit={limit}",
            ))
            i += 1
    return cases


def _sub_limit_exceeded(pe: PolicyEngine, members: list[dict]) -> list[SyntheticCase]:
    """Non-consultation covered amount > category sub_limit -> REJECTED / SUB_LIMIT_EXCEEDED.

    Amount kept below the high-value (25000) threshold for categories whose sub-limit
    is below it, so no fraud flag fires. DIAGNOSTIC/PHARMACY sub-limits (10000/15000)
    leave room under 25000."""
    cases: list[SyntheticCase] = []
    i = 0
    # Use categories whose sub_limit + headroom stays under 25000 (high-value/fraud).
    for category in ("DIAGNOSTIC", "PHARMACY", "VISION", "ALTERNATIVE_MEDICINE"):
        sub_limit = pe.category_rules(category)["sub_limit"]
        for member in members:
            over = sub_limit + 500
            if over >= 25000:  # never push into fraud high-value territory
                over = sub_limit + 200
            items = [LineItem(description=f"{category.title()} service", amount=over)]
            bill = _bill(f"SLE-{i}", items, NON_NETWORK_HOSPITAL, member["name"])
            sub = _submission(member, category, ELIGIBLE_DATE, over, NON_NETWORK_HOSPITAL)
            cases.append(SyntheticCase(
                case_id=f"sle-{i:04d}", template="sub_limit_exceeded",
                submission=sub, extractions=[bill],
                semantic=SemanticMapping(category_match=True, confidence=0.95),
                expected={"status": "REJECTED", "reason_code": "SUB_LIMIT_EXCEEDED"},
                note=f"{category} amount={over} sub_limit={sub_limit}",
            ))
            i += 1
    return cases


def _pre_auth_missing(pe: PolicyEngine, members: list[dict]) -> list[SyntheticCase]:
    """DIAGNOSTIC high-value test (MRI/CT/PET) > pre_auth_threshold -> REJECTED / PRE_AUTH_MISSING.

    pre_auth ranks ABOVE sub-limit in the aggregator, so even though the amount also
    exceeds the DIAGNOSTIC sub-limit, the expected reason is PRE_AUTH_MISSING.
    Amount kept under 25000 to avoid the fraud high-value flag."""
    cases: list[SyntheticCase] = []
    cat = pe.category_rules("DIAGNOSTIC")
    threshold = cat["pre_auth_threshold"]
    tests = cat["high_value_tests_requiring_pre_auth"]
    i = 0
    for member in members:
        for test in tests:
            amount = threshold + 2000  # 12000 > 10000 threshold, < 25000 fraud
            items = [LineItem(description=f"{test} scan", amount=amount)]
            bill = _bill(f"PA-{i}", items, NETWORK_HOSPITAL, member["name"])
            sub = _submission(member, "DIAGNOSTIC", ELIGIBLE_DATE, amount, NETWORK_HOSPITAL)
            # The treatment field also carries the test name (pre_auth reads line items + treatment).
            bill.treatment = StrField(value=f"{test} scan", confidence=0.95)
            cases.append(SyntheticCase(
                case_id=f"pa-{i:04d}", template="pre_auth_missing",
                submission=sub, extractions=[bill],
                semantic=SemanticMapping(category_match=True, confidence=0.95),
                expected={"status": "REJECTED", "reason_code": "PRE_AUTH_MISSING"},
                note=f"{test} amount={amount} threshold={threshold}",
            ))
            i += 1
    return cases


def _dental_partial(pe: PolicyEngine, members: list[dict]) -> list[SyntheticCase]:
    """DENTAL with one covered + one excluded_procedure line -> PARTIAL.

    Expected amount = covered line (DENTAL copay 0%, non-network) capped at sub-limit.
    The excluded line is dropped by coverage_exclusion (disallowed_items) and financial."""
    cases: list[SyntheticCase] = []
    dental = pe.category_rules("DENTAL")
    covered_procs = dental["covered_procedures"]
    excluded_procs = dental["excluded_procedures"]
    i = 0
    for member in members:
        for cov_proc in covered_procs[:3]:
            for exc_proc in excluded_procs[:2]:
                covered_amt = 3000.0
                excluded_amt = 4000.0
                items = [
                    LineItem(description=cov_proc, amount=covered_amt),
                    LineItem(description=exc_proc, amount=excluded_amt),
                ]
                total = covered_amt + excluded_amt
                bill = _bill(f"DEN-{i}", items, NON_NETWORK_HOSPITAL, member["name"])
                sub = _submission(member, "DENTAL", ELIGIBLE_DATE, total, NON_NETWORK_HOSPITAL)
                # Reference payout on the COVERED line only (non-network, copay 0).
                expected_amount = reference_payout(pe, "DENTAL", False, covered_amt)
                cases.append(SyntheticCase(
                    case_id=f"den-{i:04d}", template="dental_partial",
                    submission=sub, extractions=[bill],
                    semantic=SemanticMapping(category_match=True, confidence=0.92),
                    expected={"status": "PARTIAL", "expected_amount": expected_amount},
                    note=f"covered={cov_proc} excluded={exc_proc}",
                ))
                i += 1
    return cases


def _same_day_fraud(pe: PolicyEngine, members: list[dict]) -> list[SyntheticCase]:
    """claims_history with > same_day_claims_limit same-day claims -> MANUAL_REVIEW.

    `same_day_claims_limit` is 2, and the rule counts len(same_day)+1 (this claim), so
    2 prior same-day history items -> 3 > 2 -> FLAG. Amounts are kept matched (bill
    total == claimed) and modest (< high-value) so the ONLY fraud signal is same-day."""
    cases: list[SyntheticCase] = []
    limit = pe.fraud_thresholds()["same_day_claims_limit"]
    i = 0
    for member in members:
        for n_prior in (limit, limit + 1):  # limit prior + this claim = limit+1 > limit
            gross = 1500.0
            items = [LineItem(description="Consultation service", amount=gross)]
            bill = _bill(f"FRD-{i}", items, NETWORK_HOSPITAL, member["name"])
            history = [ClaimHistoryItem(claim_id=f"H{i}-{k}", date=ELIGIBLE_DATE,
                                        amount=1000.0, provider="Apollo Hospitals")
                       for k in range(n_prior)]
            sub = _submission(member, "CONSULTATION", ELIGIBLE_DATE, gross,
                              NETWORK_HOSPITAL, history=history)
            cases.append(SyntheticCase(
                case_id=f"frd-{i:04d}", template="same_day_fraud",
                submission=sub, extractions=[bill],
                semantic=SemanticMapping(category_match=True, confidence=0.95),
                expected={"status": "MANUAL_REVIEW"},
                note=f"{n_prior} prior same-day (limit={limit})",
            ))
            i += 1
    return cases


def _high_value(pe: PolicyEngine, members: list[dict]) -> list[SyntheticCase]:
    """High-value claim above the fraud high-value threshold -> MANUAL_REVIEW.

    The fraud rule's `high_value_claim_threshold` and the aggregator's
    `auto_manual_review_above` are both 25000. A claimed amount above 25000 raises a
    fraud FLAG, and the aggregator routes any FLAG to MANUAL_REVIEW. To ensure the
    claim isn't REJECTED first by the limits rule (FAILs outrank FLAGs), the covered
    line items must stay UNDER the category sub-limit. PHARMACY (sub_limit 15000) is
    used with covered line items at 14000 (< sub-limit -> limits PASS) while the
    claimed amount is >25000 -> fraud high-value FLAG -> MANUAL_REVIEW."""
    cases: list[SyntheticCase] = []
    threshold = pe.fraud_thresholds()["high_value_claim_threshold"]
    sub_limit = pe.category_rules("PHARMACY")["sub_limit"]
    covered = sub_limit - 1000.0  # 14000 < 15000 -> limits PASS
    i = 0
    for member in members:
        for claimed in (threshold + 2000, threshold + 10000):
            items = [LineItem(description="Pharmacy service", amount=covered)]
            bill = _bill(f"HV-{i}", items, NON_NETWORK_HOSPITAL, member["name"])
            sub = _submission(member, "PHARMACY", ELIGIBLE_DATE, claimed, NON_NETWORK_HOSPITAL)
            cases.append(SyntheticCase(
                case_id=f"hv-{i:04d}", template="high_value",
                submission=sub, extractions=[bill],
                semantic=SemanticMapping(category_match=True, confidence=0.95),
                expected={"status": "MANUAL_REVIEW"},
                note=f"claimed={claimed} threshold={threshold}",
            ))
            i += 1
    return cases


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

_TEMPLATES = [
    _clean_approval, _waiting_period, _excluded_condition, _per_claim_exceeded,
    _sub_limit_exceeded, _pre_auth_missing, _dental_partial, _same_day_fraud,
    _high_value,
]


def generate_cases(pe: PolicyEngine | None = None,
                   members: list[dict] | None = None) -> list[SyntheticCase]:
    """Generate the full reproducible set of labeled synthetic cases.

    `members` defaults to the EMPxxx primary members from the policy roster (the
    decision rules key off join_date / member_id, all present on the roster)."""
    from app.config import settings
    pe = pe or PolicyEngine(settings.policy_path)
    if members is None:
        members = [m for m in pe.members() if m["member_id"].startswith("EMP")]
    cases: list[SyntheticCase] = []
    for template_fn in _TEMPLATES:
        cases.extend(template_fn(pe, members))
    return cases
