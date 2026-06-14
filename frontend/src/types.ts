// TypeScript types mirroring the backend Pydantic schemas
// (backend/app/models/schemas.py). Keep in sync with the API.

export type ClaimCategory =
  | "CONSULTATION"
  | "DIAGNOSTIC"
  | "PHARMACY"
  | "DENTAL"
  | "VISION"
  | "ALTERNATIVE_MEDICINE";

export type DocType =
  | "PRESCRIPTION"
  | "HOSPITAL_BILL"
  | "PHARMACY_BILL"
  | "LAB_REPORT"
  | "DIAGNOSTIC_REPORT"
  | "DENTAL_REPORT"
  | "DISCHARGE_SUMMARY"
  | "UNKNOWN";

export type DecisionStatus =
  | "APPROVED"
  | "PARTIAL"
  | "REJECTED"
  | "MANUAL_REVIEW";

export type RuleStatus = "PASS" | "FAIL" | "FLAG" | "SKIPPED";

export type TraceStatus =
  | "PASS"
  | "FAIL"
  | "FLAG"
  | "SKIPPED"
  | "ERROR"
  | "INFO";

export type ProblemKind =
  | "WRONG_DOCUMENT"
  | "MISSING_DOCUMENT"
  | "UNREADABLE_DOCUMENT"
  | "PATIENT_MISMATCH"
  | "INTAKE_VIOLATION"
  | "NEEDS_MEMBER_INPUT";

export interface ClaimHistoryItem {
  claim_id: string;
  date: string; // ISO date
  amount: number;
  provider?: string | null;
}

export interface DocumentInput {
  file_id: string;
  file_name?: string | null;
  stored_path: string;
}

export interface ClaimSubmission {
  member_id: string;
  policy_id: string;
  claim_category: ClaimCategory;
  treatment_date: string; // ISO date
  claimed_amount: number;
  hospital_name?: string | null;
  ytd_claims_amount?: number | null;
  claims_history?: ClaimHistoryItem[];
  simulate_component_failure?: boolean;
  documents?: DocumentInput[];
}

export interface LineItemDecision {
  description: string;
  amount: number;
  approved: boolean;
  reason?: string | null;
}

export interface FinancialBreakdown {
  gross: number;
  network_discount_pct: number;
  network_discount_amount: number;
  post_discount: number;
  copay_pct: number;
  copay_amount: number;
  line_items: LineItemDecision[];
  approved_amount: number;
  steps: string[];
}

export interface ReasonCode {
  code: string;
  detail: string;
}

export interface ConfidenceComponents {
  extraction_quality: number;
  rule_certainty: number;
  completeness: number;
  verifier_agreement: number;
  degradation_penalty: number;
}

export interface Decision {
  status: DecisionStatus;
  approved_amount: number;
  reason_codes: ReasonCode[];
  member_message: string;
  confidence: number;
  confidence_components?: ConfidenceComponents | null;
  recommendations: string[];
  financial?: FinancialBreakdown | null;
}

export interface DocumentProblem {
  kind: ProblemKind;
  file_id?: string | null;
  message: string;
}

export interface TraceEntry {
  seq: number;
  step: string;
  agent: string;
  status: TraceStatus;
  summary: string;
  detail: Record<string, unknown>;
  policy_refs: string[];
  model?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  confidence_delta?: number | null;
  degraded: boolean;
  failure_mode?: string | null;
  duration_ms: number;
}

export interface ComponentFailure {
  agent: string;
  failure_mode: string;
  recoverable: boolean;
}

// Extraction field shapes (subset used by the ops correction panel).
export interface StrField {
  value?: string | null;
  confidence: number;
  source_text?: string | null;
}
export interface NumField {
  value?: number | null;
  confidence: number;
  source_text?: string | null;
}
export interface ExtractionLineItem {
  description: string;
  amount: number;
  confidence?: number;
  is_branded?: boolean | null;
}
export interface ExtractionResult {
  file_id: string;
  doc_type: DocType;
  patient_name: StrField;
  doctor_name: StrField;
  doctor_registration: StrField;
  diagnosis: StrField;
  treatment: StrField;
  hospital_name: StrField;
  document_date: StrField;
  line_items: ExtractionLineItem[];
  total_amount: NumField;
  fraud_signals: string[];
}

// One append-only entry in a claim's correction history (ops inline correction).
export interface CorrectionHistoryEntry {
  corrected_at: string;
  corrected_by: string;
  changed_fields: Record<string, unknown>[];
  before: { status: string | null; amount: number | null };
  after: { status: string | null; amount: number | null };
  changed_rules: {
    rule: string;
    before: { status: string; reason_code: string | null };
    after: { status: string; reason_code: string | null };
  }[];
}

export interface ClaimResult {
  claim_id: string;
  blocked: boolean;
  problems: DocumentProblem[];
  decision?: Decision | null;
  trace: TraceEntry[];
  failures: ComponentFailure[];
  // Sub-feature A: per-claim cost + latency meta (defaulted server-side).
  total_input_tokens?: number;
  total_output_tokens?: number;
  total_latency_ms?: number;
  estimated_cost_inr?: number;
  // Sub-feature B + ops correction: stored extracted facts + correction metadata.
  extractions?: ExtractionResult[];
  corrected_by?: string | null;
  corrected_at?: string | null;
  correction_history?: CorrectionHistoryEntry[];
  // Operator final decision (human-in-the-loop resolution / override).
  decided_by?: string | null;
  decided_at?: string | null;
}

// POST /api/claims/:id/replay — deterministic decision replay (Sub-feature B).
export interface ReplayResult {
  replayable: boolean;
  reason?: string;
  original_status?: DecisionStatus;
  replayed_status?: DecisionStatus;
  original_amount?: number;
  replayed_amount?: number;
  matches?: boolean;
  replayed_trace_summary?: {
    rule: string;
    status: string;
    detail: string;
  }[];
}

// --- API list/summary shapes (not Pydantic models, but stable API shapes) ---

export interface Member {
  member_id: string;
  name: string;
  relationship?: string;
}

export interface ClaimSummary {
  claim_id: string;
  created_at: string;
  member_id: string;
  category: string;
  blocked: boolean;
  status?: string | null;
  approved_amount?: number | null;
  confidence?: number | null;
}

export interface ClaimDocument {
  file_id: string;
  file_name?: string | null;
  doc_type: DocType;
  content_type: string;
}

export interface EvalCaseResult {
  case_id: string;
  case_name: string;
  matched: boolean;
  notes: string[];
  result: ClaimResult;
}

// --- Shift-left document checks ---------------------------------------------

// GET /api/policy/document-requirements → { category: { required, optional } }
export interface DocumentRequirement {
  required: DocType[];
  optional: DocType[];
}
export type DocumentRequirements = Record<ClaimCategory, DocumentRequirement>;

// POST /api/documents/classify → live single-file classification summary.
export interface ClassifyResult {
  doc_type: DocType;
  readable: boolean;
  quality_issues?: string[];
  patient_name?: string | null;
  confidence?: number;
  error?: string;
}

// --- Ops dashboard shapes (GET /api/ops/*) ----------------------------------

export interface OpsCategoryStat {
  category: string;
  count: number;
  total_approved: number;
}

export interface OpsAnalytics {
  total_claims: number;
  by_status: Record<string, number>;
  decided_count: number;
  blocked_count: number;
  flagged_fraud_count: number;
  approval_rate: number;
  blocked_rate: number;
  manual_review_rate: number;
  total_approved_amount: number;
  avg_approved_amount: number;
  avg_confidence: number;
  estimated_total_cost_inr: number;
  avg_latency_ms: number;
  by_category: OpsCategoryStat[];
}

export interface WorklistItem extends ClaimSummary {
  needs_review: boolean;
}

export interface WorklistFilters {
  status?: string;
  category?: string;
  q?: string;
  sort?: "created_at" | "amount" | "confidence";
}

export interface FraudReason {
  code: string | null;
  detail: string | null;
}

export interface FraudRuleSignal {
  status: string | null;
  summary: string | null;
  policy_refs: string[];
}

export interface FraudClaim {
  claim_id: string;
  created_at: string | null;
  member_id: string;
  category: string;
  status: string;
  approved_amount?: number | null;
  confidence?: number | null;
  reasons: FraudReason[];
  recommendations: string[];
  extraction_signals: string[];
  fraud_rule: FraudRuleSignal | null;
}

// Raw eval case definition (from GET /api/eval/cases — test_cases.json shape)
export interface EvalCase {
  case_id: string;
  case_name: string;
  description?: string;
  input: Record<string, unknown>;
  expected: Record<string, unknown>;
}
