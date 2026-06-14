"""Maps free-text diagnosis/treatment to policy concepts. PROPOSES ONLY — rules decide."""
from app.models.schemas import SemanticMapping, ExtractionResult
from app.services.policy_engine import PolicyEngine
from app.services.gemini import generate_structured_with_usage
from app.services.sanitize import sanitize_untrusted_text

PROMPT_TMPL = """You map Indian medical claim text onto fixed policy vocabularies. Respond with JSON.

Claim category: {category}
Diagnosis text: {diagnosis}
Treatment text: {treatment}

Vocabularies (choose ONLY from these; null/empty if no match):
- waiting_condition (waiting_periods keys): {waiting_keys}
- exclusion_candidates (policy exclusion strings that this diagnosis/treatment clearly falls under): {exclusions}
- mapped_category (the OPD category this treatment actually belongs to): CONSULTATION, DIAGNOSTIC, PHARMACY, DENTAL, VISION, ALTERNATIVE_MEDICINE

Notes: medical shorthand is common (HTN=hypertension, T2DM=diabetes). 'Bariatric'/'weight loss'/
'obesity' treatments fall under 'Obesity and weight loss programs'. Panchakarma/Ayurveda are
ALTERNATIVE_MEDICINE. category_match=false if mapped_category differs from the claim category.
confidence = your certainty in this mapping (0-1).

SCOPE OF exclusion_candidates — read carefully:
exclusion_candidates is ONLY for when the claim's PRIMARY diagnosis/treatment as a whole is an
excluded condition (e.g. the reason for the visit is bariatric/obesity treatment, infertility,
substance-abuse treatment). It makes the WHOLE claim ineligible.
Do NOT put an entry here just because one billed line item within an otherwise-covered claim is a
cosmetic add-on. In particular, individual DENTAL cosmetic procedures (Teeth Whitening, Veneers,
Bleaching, Braces) and VISION cosmetic items (LASIK, refractive surgery) are handled as
LINE-ITEM exclusions by the category rules (yielding a PARTIAL approval) — they must NOT be mapped
to the broad 'Cosmetic or aesthetic procedures' condition exclusion. Only map 'Cosmetic or
aesthetic procedures' when the entire treatment is itself a cosmetic procedure (not when a covered
treatment like a Root Canal simply has a cosmetic line item alongside it)."""

def map_semantics_with_usage(category: str, extractions: list[ExtractionResult],
                             pe: PolicyEngine) -> tuple[SemanticMapping, dict]:
    """Sub-feature A: semantic mapping + per-call token usage for the trace."""
    # Reinforced injection sanitization: the diagnosis/treatment strings are UNTRUSTED
    # (vision-extracted from member-supplied documents) and are about to be interpolated
    # into the prompt below. Neutralize role markers / control phrases / structure chars
    # before that. This is a NO-OP on clean medical text (the 12 cases), so the mapping —
    # and the 12/12 eval — is unchanged; it only bites adversarial document content.
    diagnosis = "; ".join(filter(None, (sanitize_untrusted_text(e.diagnosis.value)
                          for e in extractions if e.diagnosis.value))) or "(none)"
    treatment = "; ".join(filter(None, [sanitize_untrusted_text(e.treatment.value) for e in extractions] +
                                       [sanitize_untrusted_text(i.description)
                                        for e in extractions for i in e.line_items])) or "(none)"
    prompt = PROMPT_TMPL.format(category=category, diagnosis=diagnosis, treatment=treatment,
                                waiting_keys=list(pe.waiting_conditions().keys()),
                                exclusions=pe.exclusion_conditions())
    return generate_structured_with_usage([prompt], SemanticMapping)


def map_semantics(category: str, extractions: list[ExtractionResult], pe: PolicyEngine) -> SemanticMapping:
    m, _ = map_semantics_with_usage(category, extractions, pe)
    return m
