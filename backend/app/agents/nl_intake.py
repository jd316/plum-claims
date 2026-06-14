"""Natural-language claim intake — a member describes their claim in a sentence and
we extract a DRAFT claim that PRE-FILLS the submission form.

It NEVER submits and NEVER decides — it only infers what it can from free text and
returns nulls for the rest. The member still uploads documents and reviews every
field. Purely additive; does not touch the decision pipeline or the 12 cases.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.schemas import ClaimCategory
from app.services.gemini import generate_structured
from app.services.sanitize import sanitize_untrusted_text


class ClaimDraft(BaseModel):
    """A DRAFT claim parsed from free text. Every field is optional — the parser
    returns only what it can confidently infer and leaves the rest null."""
    member_hint: str | None = Field(
        None, description="Any name/person the member referred to, if mentioned (e.g. 'for my son'). Null otherwise.")
    claim_category: ClaimCategory | None = Field(
        None, description="One of the fixed categories if clearly implied, else null.")
    claimed_amount: float | None = Field(
        None, description="The bill/claim amount in rupees as a plain number (no symbols/commas), else null.")
    hospital_name: str | None = Field(
        None, description="The hospital/clinic/provider name if mentioned, else null.")
    treatment_date: str | None = Field(
        None, description="The treatment date as YYYY-MM-DD if an explicit date is given, else null.")
    notes: str = Field(
        "", description="A one-line plain summary of what the member described.")


_PROMPT = """You extract a DRAFT health-insurance claim from a member's free-text description.
Respond with JSON only. You ONLY pre-fill a form — you never submit or decide anything.
Return null for anything you cannot confidently infer; do NOT guess.

Map the described treatment to ONE of these fixed categories (or null if unclear):
- CONSULTATION — seeing a doctor/physician, a check-up, fever/cold/general visit, OPD consultation.
- PHARMACY — medicines, medication, pharmacy/chemist/drugstore, buying prescribed drugs.
- DIAGNOSTIC — MRI, CT/PET scan, X-ray, ultrasound, blood test, lab test, pathology, any scan/test.
- DENTAL — dentist, tooth/teeth, root canal, filling, extraction, scaling, crown, gum treatment.
- VISION — eye, glasses/spectacles, contact lenses, eye exam, optometrist, cataract.
- ALTERNATIVE_MEDICINE — Ayurveda, Homeopathy, Unani, Siddha, Naturopathy, Panchakarma.

Amount rules: convert any money mention to a plain number of rupees.
  "₹1,500" -> 1500 ; "1500 rupees" -> 1500 ; "Rs. 8000" -> 8000 ; "two thousand" -> 2000.
If no amount is mentioned, return null.

hospital_name: only the provider/clinic name if explicitly mentioned (e.g. "Apollo", "Fortis").
treatment_date: only if an explicit calendar date is given; format YYYY-MM-DD. Relative words
  like "yesterday"/"last week" -> null (the member sets the date themselves).
notes: a short, neutral one-line summary of what they described.

MEMBER DESCRIPTION:
{text}
"""


def parse_claim_text(text: str) -> dict:
    """Parse free-text into a draft claim dict {member_hint?, claim_category?,
    claimed_amount?, hospital_name?, treatment_date?, notes}.

    The text is member-supplied and UNTRUSTED, so it is neutralised against prompt
    injection before interpolation. Returns a plain dict for the API layer."""
    safe = sanitize_untrusted_text(text or "")
    draft: ClaimDraft = generate_structured(
        [_PROMPT.format(text=safe)], ClaimDraft)
    return draft.model_dump()
