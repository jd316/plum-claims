"""Build a deterministic decision-eval `SyntheticCase` from a preview sample.

The Policy Studio's impact preview runs a sample claim through `decide_from_facts`
(the real rules, no Gemini). A `SampleSpec` describes that sample two ways:

  * a TEST-CASE id (e.g. "TC004") — facts are reconstructed from test_cases.json's
    embedded `content` (line items, hospital, patient), exactly the structured facts a
    clean extraction would yield, so the deterministic decision is faithful; or
  * an INLINE claim — category, claimed_amount, optional hospital + line items — for a
    quick built-in sample without a test-case file.

It deliberately mirrors the fact shape the eval's `synthetic.py` builds: a
ClaimSubmission + one HOSPITAL_BILL/PHARMACY_BILL ExtractionResult (+ a high-confidence
SemanticMapping), which is all the deterministic rules + financial calculator read.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from app.config import settings
from app.evalrunner.synthetic import SyntheticCase
from typing import cast
from app.models.schemas import (ClaimSubmission, ExtractionResult, SemanticMapping,
                                LineItem, NumField, StrField, ClaimCategory)
from app.services.policy_engine import PolicyEngine

# A treatment date well outside every waiting period (members joined 2024-04-01;
# EMP005 joined 2024-09-01) so a preview of a covered claim isn't rejected on timing.
_ELIGIBLE_DATE = date(2025, 6, 1)
# Bill doc types whose line items the financial calculator reads.
_BILL_TYPES = ("HOSPITAL_BILL", "PHARMACY_BILL")


@dataclass
class SampleSpec:
    """A preview sample. `to_case(engine)` produces a SyntheticCase for a given engine
    so the SAME sample can be run under the active and the candidate policy."""
    label: str
    member_id: str
    category: str
    claimed_amount: float
    hospital_name: str | None
    line_items: list[tuple[str, float]]
    source: str  # "test_case:<id>" | "inline"

    def to_case(self, pe: PolicyEngine) -> SyntheticCase:
        member = pe.member(self.member_id)
        items = [LineItem(description=d, amount=float(a)) for d, a in self.line_items]
        if not items and self.claimed_amount:
            items = [LineItem(description="Claimed amount", amount=float(self.claimed_amount))]
        total = round(sum(i.amount for i in items), 2)
        doc_type = "PHARMACY_BILL" if self.category == "PHARMACY" else "HOSPITAL_BILL"
        bill = ExtractionResult(
            file_id="PREVIEW-BILL", doc_type=doc_type,
            patient_name=StrField(value=member.get("name"), confidence=0.97),
            hospital_name=(StrField(value=self.hospital_name, confidence=0.95)
                           if self.hospital_name else StrField()),
            line_items=items,
            total_amount=NumField(value=total, confidence=0.96),
        )
        submission = ClaimSubmission(
            member_id=self.member_id, policy_id=pe.policy_id,
            claim_category=cast(ClaimCategory, self.category), treatment_date=_ELIGIBLE_DATE,
            claimed_amount=float(self.claimed_amount or total),
            hospital_name=self.hospital_name, documents=[],
        )
        return SyntheticCase(
            case_id=f"preview-{self.source}", template="policy_preview",
            submission=submission, extractions=[bill],
            semantic=SemanticMapping(category_match=True, confidence=0.95),
            expected={}, note=self.label,
        )

    def describe(self) -> dict:
        return {
            "label": self.label, "source": self.source, "member_id": self.member_id,
            "category": self.category, "claimed_amount": self.claimed_amount,
            "hospital_name": self.hospital_name,
            "line_items": [{"description": d, "amount": a} for d, a in self.line_items],
        }


def _load_test_cases() -> list[dict]:
    with open(settings.test_cases_path) as f:
        data = json.load(f)
    return data.get("test_cases", []) if isinstance(data, dict) else data


def from_test_case(case_id: str) -> SampleSpec:
    """Reconstruct a SampleSpec from a test_cases.json case's embedded content.

    Pulls the first bill document's line items + hospital from `content`, so the sample
    reflects the real claim. Raises KeyError if the case id is unknown."""
    case = next((c for c in _load_test_cases() if c.get("case_id") == case_id), None)
    if case is None:
        raise KeyError(f"unknown test case {case_id}")
    inp = case.get("input", {})
    line_items: list[tuple[str, float]] = []
    hospital: str | None = None
    for doc in inp.get("documents", []):
        content = doc.get("content") or {}
        if content.get("hospital_name") and hospital is None:
            hospital = content["hospital_name"]
        for li in content.get("line_items", []) or []:
            desc = li.get("description")
            amt = li.get("amount")
            if desc is not None and amt is not None:
                line_items.append((desc, float(amt)))
    return SampleSpec(
        label=case.get("case_name", case_id),
        member_id=inp.get("member_id", "EMP001"),
        category=inp.get("claim_category", "CONSULTATION"),
        claimed_amount=float(inp.get("claimed_amount") or 0),
        hospital_name=hospital,
        line_items=line_items,
        source=f"test_case:{case_id}",
    )


def from_inline(spec: dict) -> SampleSpec:
    """Build a SampleSpec from an inline claim dict:
        {member_id, claim_category, claimed_amount, hospital_name?, line_items?}
    where line_items is a list of {description, amount}."""
    line_items = [(li["description"], float(li["amount"]))
                  for li in spec.get("line_items", []) if "amount" in li]
    return SampleSpec(
        label=spec.get("label", "Inline sample"),
        member_id=spec.get("member_id", "EMP001"),
        category=spec.get("claim_category", "CONSULTATION"),
        claimed_amount=float(spec.get("claimed_amount") or 0),
        hospital_name=spec.get("hospital_name"),
        line_items=line_items,
        source="inline",
    )
