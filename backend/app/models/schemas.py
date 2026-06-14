from __future__ import annotations
from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel

ClaimCategory = Literal["CONSULTATION","DIAGNOSTIC","PHARMACY","DENTAL","VISION","ALTERNATIVE_MEDICINE"]
DocType = Literal["PRESCRIPTION","HOSPITAL_BILL","PHARMACY_BILL","LAB_REPORT","DIAGNOSTIC_REPORT","DENTAL_REPORT","DISCHARGE_SUMMARY","UNKNOWN"]
DecisionStatus = Literal["APPROVED","PARTIAL","REJECTED","MANUAL_REVIEW"]

class ClaimHistoryItem(BaseModel):
    claim_id: str; date: date; amount: float; provider: str | None = None

class DocumentInput(BaseModel):
    file_id: str
    file_name: str | None = None
    stored_path: str            # real file on disk (upload or rendered fixture)

class ClaimSubmission(BaseModel):
    member_id: str; policy_id: str
    claim_category: ClaimCategory
    treatment_date: date
    # When the claim was actually submitted. Used ONLY by the gated submission-deadline
    # check (settings.submission_deadline_enabled); when absent it falls back to today().
    # The eval runner never sets it, so the 12 cases never trigger the deadline rule.
    submission_date: date | None = None
    claimed_amount: float
    hospital_name: str | None = None
    ytd_claims_amount: float | None = None
    # Count of approved ALTERNATIVE_MEDICINE sessions YTD for this member, attached at
    # the API layer (like ytd_claims_amount). None → the gated session-cap rule is skipped.
    alt_med_sessions_ytd: int | None = None
    # Family-floater utilisation (sum of approved claims across the member + their
    # covered family) computed at the API layer from persisted history and attached
    # BEFORE the pipeline. None means "not supplied" → the floater check is skipped.
    # The eval runner never sets this, so the 12 cases never trigger the floater rule.
    floater_used_amount: float | None = None
    claims_history: list[ClaimHistoryItem] = []
    simulate_component_failure: bool = False
    documents: list[DocumentInput]

class StrField(BaseModel):
    value: str | None = None; confidence: float = 0.0; source_text: str | None = None
class NumField(BaseModel):
    value: float | None = None; confidence: float = 0.0; source_text: str | None = None
class LineItem(BaseModel):
    description: str; amount: float; confidence: float = 1.0
    # PHARMACY only: True = brand-name drug (30% branded copay), False = generic
    # (standard 0% copay), None = unknown / not a pharmacy line (treated as generic).
    is_branded: bool | None = None
    # PHARMACY only: True = a generic substitute exists for this branded drug (set by a
    # formulary lookup). Used ONLY by the gated generic_mandatory rule; None → not enforced.
    has_generic_alternative: bool | None = None

class DocumentQuality(BaseModel):
    readable: bool = True
    quality_issues: list[str] = []
    overall_confidence: float = 1.0

class ExtractionResult(BaseModel):
    file_id: str = ""
    doc_type: DocType = "UNKNOWN"
    quality: DocumentQuality = DocumentQuality()
    patient_name: StrField = StrField()
    doctor_name: StrField = StrField()
    doctor_registration: StrField = StrField()
    diagnosis: StrField = StrField()
    treatment: StrField = StrField()
    hospital_name: StrField = StrField()
    document_date: StrField = StrField()
    line_items: list[LineItem] = []
    total_amount: NumField = NumField()
    fraud_signals: list[str] = []

class SemanticMapping(BaseModel):
    category_match: bool = True
    mapped_category: Optional[ClaimCategory] = None
    exclusion_candidates: list[str] = []
    waiting_condition: str | None = None
    confidence: float = 0.0

RuleName = Literal["waiting_period","coverage_exclusion","pre_auth","limits","fraud_anomaly"]
class RuleVerdict(BaseModel):
    rule: RuleName
    status: Literal["PASS","FAIL","FLAG","SKIPPED"]
    reason_code: str | None = None
    detail: str = ""
    policy_refs: list[str] = []
    disallowed_items: list[str] = []
    certainty: float = 1.0

class LineItemDecision(BaseModel):
    description: str; amount: float; approved: bool; reason: str | None = None
class FinancialBreakdown(BaseModel):
    gross: float
    network_discount_pct: float = 0.0; network_discount_amount: float = 0.0; post_discount: float = 0.0
    copay_pct: float = 0.0; copay_amount: float = 0.0
    line_items: list[LineItemDecision] = []
    approved_amount: float = 0.0
    steps: list[str] = []

class ReasonCode(BaseModel):
    code: str; detail: str
class ConfidenceComponents(BaseModel):
    extraction_quality: float; rule_certainty: float; completeness: float; verifier_agreement: float
    degradation_penalty: float
class VerifierResult(BaseModel):
    verdict: Literal["PASS","FAIL"] = "PASS"; confidence: float = 0.5; reason: str = ""

class Decision(BaseModel):
    status: DecisionStatus
    approved_amount: float = 0.0
    reason_codes: list[ReasonCode] = []
    member_message: str = ""
    confidence: float = 0.0
    confidence_components: ConfidenceComponents | None = None
    recommendations: list[str] = []
    financial: FinancialBreakdown | None = None

class DocumentProblem(BaseModel):
    kind: Literal["WRONG_DOCUMENT","MISSING_DOCUMENT","UNREADABLE_DOCUMENT","PATIENT_MISMATCH","INTAKE_VIOLATION","NEEDS_MEMBER_INPUT"]
    file_id: str | None = None
    message: str

class TraceEntry(BaseModel):
    seq: int = 0
    step: str; agent: str
    status: Literal["PASS","FAIL","FLAG","SKIPPED","ERROR","INFO"]
    summary: str
    detail: dict = {}
    policy_refs: list[str] = []
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    confidence_delta: float | None = None
    degraded: bool = False
    failure_mode: str | None = None
    duration_ms: int = 0

class ComponentFailure(BaseModel):
    agent: str; failure_mode: str; recoverable: bool = True

class ClaimResult(BaseModel):
    claim_id: str
    blocked: bool = False
    problems: list[DocumentProblem] = []
    decision: Decision | None = None
    trace: list[TraceEntry] = []
    failures: list[ComponentFailure] = []
    # --- Sub-feature A: per-claim cost + latency meta (additive, defaulted) ---
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_latency_ms: int = 0
    estimated_cost_inr: float = 0.0
    # --- Sub-feature B: stored extracted facts for deterministic replay -------
    # The exact LLM-proposed facts the deterministic rules decided on. Persisting
    # them lets us re-run the rules with NO Gemini and prove same-facts→same-decision.
    extractions: list[ExtractionResult] = []
    semantic: SemanticMapping | None = None
    member: dict | None = None
    # --- Ops inline field correction (additive, defaulted) --------------------
    # An operator may correct a low-confidence EXTRACTED field; the deterministic
    # decision is re-run on the corrected facts and the corrected outcome becomes
    # the new state. These fields are append-only audit metadata: the ORIGINAL
    # decision is preserved in correction_history (never lost). Defaults keep every
    # existing/legacy row valid and unchanged.
    corrected_by: str | None = None
    corrected_at: str | None = None
    correction_history: list[dict] = []
    # --- Operator final decision (human-in-the-loop resolution / override) -----
    # Set when an operator makes the final call (resolve a MANUAL_REVIEW or override
    # the AI). The decision above reflects it; this is provenance for the UI badge.
    # The full before→after + note is in correction_history and the audit log.
    decided_by: str | None = None
    decided_at: str | None = None
