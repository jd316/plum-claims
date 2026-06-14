// Typed API client for the Plum Claims backend.
// Base URL is '' — the Vite dev proxy forwards /api to http://localhost:8000.

import type {
  ClaimDocument,
  ClaimCategory,
  ClaimResult,
  ClaimSummary,
  ClassifyResult,
  DocumentRequirements,
  EvalCase,
  EvalCaseResult,
  FraudClaim,
  Member,
  OpsAnalytics,
  ReplayResult,
  WorklistFilters,
  WorklistItem,
} from "./types";

const BASE = "";

const DEFAULT_TIMEOUT_MS = 120_000;

// --- Auth plumbing (Round 4) -----------------------------------------------
// Token storage + an authHeader() helper. The backend auth is OFF by default,
// so the current no-auth flow is unchanged: the bearer header is ATTACHED only
// when a token actually exists in localStorage. Login pages / UI gating are
// Round 5; this is purely the client plumbing.

const TOKEN_KEY = "plum.access_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // localStorage unavailable (SSR / privacy mode) → behave as anon
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // best-effort; a storage failure must not break the caller
  }
}

export function clearToken(): void {
  setToken(null);
}

// Returns { Authorization: "Bearer <token>" } when a token exists, else {} so
// requests made without a token are byte-identical to the pre-auth client.
export function authHeader(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  // Attach the bearer token when present (no-op when no token → unchanged flow).
  const headers = { ...authHeader(), ...(init?.headers ?? {}) };

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers, signal: controller.signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(
        "Request timed out — the pipeline is taking longer than expected. Please retry.",
        { cause: err }
      );
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) {
        detail = `${res.status}: ${
          Array.isArray(body.detail)
            ? body.detail
                .map((e: { msg?: string }) => e?.msg ?? JSON.stringify(e))
                .join("; ")
            : body.detail
        }`;
      }
    } catch {
      // response had no JSON body; keep the status-line detail
    }
    throw new Error(`Request to ${path} failed — ${detail}`);
  }
  return (await res.json()) as T;
}

// --- Auth API --------------------------------------------------------------

export interface LoginResponse {
  access_token: string;
  token_type: string;
  role: "member" | "ops";
  member_id: string | null;
}

export interface MeResponse {
  username: string;
  role: "member" | "ops";
  member_id: string | null;
}

// Logs in and persists the returned token in localStorage, returning the body.
export async function login(
  username: string,
  password: string
): Promise<LoginResponse> {
  const body = await request<LoginResponse>(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    },
    30_000
  );
  setToken(body.access_token);
  return body;
}

export function getMe(): Promise<MeResponse> {
  return request<MeResponse>("/api/auth/me");
}

export interface AuthConfig {
  auth_enabled: boolean;
  // Optional, wayfinding-only: show the Operator|Member toggle on the login page.
  show_role_help?: boolean;
}

// Public probe — no token required. Tells the UI whether to show the login wall +
// role gating. When auth_enabled is false (default), the app renders openly as today.
export function getAuthConfig(): Promise<AuthConfig> {
  return request<AuthConfig>("/api/auth/config", undefined, 15_000);
}

export function getMembers(): Promise<Member[]> {
  return request<Member[]>("/api/members");
}

export function submitClaim(
  payload: object,
  files: File[]
): Promise<ClaimResult> {
  const form = new FormData();
  form.append("payload", JSON.stringify(payload));
  for (const file of files) {
    form.append("files", file);
  }
  return request<ClaimResult>(
    "/api/claims",
    {
      method: "POST",
      body: form,
    },
    120_000
  );
}

// --- Asynchronous claim processing (production path) -----------------------
// In production, claims are processed off the request thread by a Celery worker
// pool backed by Redis: submitClaimAsync enqueues the claim and returns a job_id
// immediately; getJob polls until the job is completed and carries the result.
// The Submit UI currently stays on the synchronous submitClaim() path to avoid
// risk; these client functions expose the async API for when the UI moves to
// background processing. If the broker is down the server falls back to sync and
// returns { status: "completed", result } directly from submitClaimAsync.

export interface JobAck {
  job_id: string | null;
  claim_id: string;
  status: "queued" | "completed";
  result?: ClaimResult;
  fallback?: "sync";
}

export interface JobStatus {
  job_id: string;
  status: "queued" | "started" | "completed" | "failed";
  claim_id: string | null;
  result?: ClaimResult | null;
  error?: string;
}

export function submitClaimAsync(
  payload: object,
  files: File[]
): Promise<JobAck> {
  const form = new FormData();
  form.append("payload", JSON.stringify(payload));
  for (const file of files) {
    form.append("files", file);
  }
  return request<JobAck>(
    "/api/claims/async",
    { method: "POST", body: form },
    120_000
  );
}

export function getJob(jobId: string): Promise<JobStatus> {
  return request<JobStatus>(`/api/jobs/${encodeURIComponent(jobId)}`, undefined, 30_000);
}

// Per-category document-requirements map — used to build the Submit drop-zones.
export function getDocumentRequirements(): Promise<DocumentRequirements> {
  return request<DocumentRequirements>("/api/policy/document-requirements");
}

// Live shift-left classification of a single uploaded file. Non-authoritative —
// the server-side pipeline remains the source of truth on submit.
export function classifyDocument(file: File): Promise<ClassifyResult> {
  const form = new FormData();
  form.append("file", file);
  return request<ClassifyResult>(
    "/api/documents/classify",
    { method: "POST", body: form },
    120_000
  );
}

// --- Member-facing additive features ---------------------------------------
// Pre-submission payout estimate (deterministic, no LLM) + a read-only per-claim
// chat assistant. Neither touches the decision pipeline.

export interface PayoutEstimate {
  estimated_payout: number;
  network_discount_amount: number;
  copay_amount: number;
  is_network: boolean;
  breakdown_steps: string[];
  note: string;
}

// Deterministic estimate — mirrors the financial calc the pipeline uses. The
// caller debounces this; a 422 (unknown/invalid input) surfaces as a thrown error.
export function estimatePayout(body: {
  claim_category: string;
  claimed_amount: number;
  hospital_name?: string;
}): Promise<PayoutEstimate> {
  return request<PayoutEstimate>(
    "/api/estimate",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    30_000
  );
}

// Read-only, grounded per-claim chat — answers only from this claim's stored data.
export function askClaim(
  claimId: string,
  question: string
): Promise<{ answer: string }> {
  return request<{ answer: string }>(
    `/api/claims/${encodeURIComponent(claimId)}/ask`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    },
    60_000
  );
}

// --- Natural-language features (additive, no pipeline run) ------------------
// 1. RAG over the policy — ask in plain English, get a grounded answer + cited
//    source passage titles. 2. NL claim intake — parse a free-text description
//    into a draft claim that pre-fills the Submit form. Neither decides anything.

export interface PolicyAnswer {
  answer: string;
  sources: string[];
}

// Grounded policy Q&A. Returns an answer plus the cited source passage titles
// (empty when the policy doesn't cover the question).
export function askPolicy(question: string): Promise<PolicyAnswer> {
  return request<PolicyAnswer>(
    "/api/policy/ask",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    },
    60_000
  );
}

export interface ClaimDraft {
  member_hint: string | null;
  claim_category: ClaimCategory | null;
  claimed_amount: number | null;
  hospital_name: string | null;
  treatment_date: string | null;
  notes: string;
}

// Parse a free-text claim description into a draft for pre-filling the form.
// Returns only what it can infer (nulls otherwise); never submits or decides.
export function parseClaim(text: string): Promise<ClaimDraft> {
  return request<ClaimDraft>(
    "/api/claims/parse",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    },
    60_000
  );
}

export function listClaims(): Promise<ClaimSummary[]> {
  return request<ClaimSummary[]>("/api/claims");
}

export function getClaim(id: string): Promise<ClaimResult> {
  return request<ClaimResult>(`/api/claims/${encodeURIComponent(id)}`);
}

// Deterministic decision replay (Sub-feature B) — re-runs the rules from stored
// facts with no Gemini and reports whether the verdict reproduces identically.
export function replayClaim(id: string): Promise<ReplayResult> {
  return request<ReplayResult>(
    `/api/claims/${encodeURIComponent(id)}/replay`,
    { method: "POST" },
    30_000
  );
}

// --- Explainability: counterfactuals + what-if simulator -------------------
// Both run on the DETERMINISTIC layer (no Gemini) over the stored claim facts,
// so they are exact and instant. Read-only; never mutate stored data.

export interface Counterfactual {
  reason: string;
  change: string;
  resulting_decision: string;
  resulting_amount: number | null;
  achievable: boolean;
}

export interface CounterfactualsResponse {
  claim_id: string;
  base: {
    claimed_amount: number;
    treatment_date: string;
    is_network: boolean;
    category: string;
  };
  counterfactuals: Counterfactual[];
}

export interface WhatIfOverrides {
  claimed_amount?: number;
  hospital_name?: string;
  is_network?: boolean;
  treatment_date?: string;
  category?: string;
  line_allow?: Record<string, boolean>;
  candidate_policy?: PolicyJson;
}

export interface WhatIfDecision {
  status: string;
  approved_amount: number;
  reason_codes: { code: string; detail: string }[];
}

export interface WhatIfResult {
  before: WhatIfDecision;
  after: WhatIfDecision;
  diff: {
    status_changed: boolean;
    amount_delta: number;
    changed_rules: {
      rule: string;
      before: { status: string; reason_code: string | null };
      after: { status: string; reason_code: string | null };
    }[];
  };
}

// The minimal changes that would flip a non-approved / partial decision.
export function getCounterfactuals(id: string): Promise<CounterfactualsResponse> {
  return request<CounterfactualsResponse>(
    `/api/claims/${encodeURIComponent(id)}/counterfactuals`,
    undefined,
    30_000
  );
}

// Apply overrides to a copy of the stored facts and re-decide (before/after).
export function whatIf(
  id: string,
  overrides: WhatIfOverrides
): Promise<WhatIfResult> {
  return request<WhatIfResult>(
    `/api/claims/${encodeURIComponent(id)}/what-if`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(overrides),
    },
    30_000
  );
}

// --- Ops inline field correction -------------------------------------------
// An operator corrects a low-confidence EXTRACTED field; the backend re-runs the
// DETERMINISTIC decision (no Gemini) on the corrected facts and PERSISTS the new
// outcome with an append-only audit trail. Ops-only (open when auth off).

export interface FieldCorrection {
  file_id: string;
  field: string; // "total_amount" | "patient_name" | "diagnosis" | "line_items" | ...
  value: unknown;
}

export interface CorrectionResult {
  before: { status: string; amount: number };
  after: { status: string; amount: number };
  changed_fields: Record<string, unknown>[];
  changed_rules: {
    rule: string;
    before: { status: string; reason_code: string | null };
    after: { status: string; reason_code: string | null };
  }[];
  persisted: boolean;
}

// Apply ops field corrections and re-decide. Returns before/after + what changed.
export function correctClaim(
  id: string,
  corrections: FieldCorrection[],
  actor?: string
): Promise<CorrectionResult> {
  return request<CorrectionResult>(
    `/api/claims/${encodeURIComponent(id)}/correct`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corrections, actor }),
    },
    30_000
  );
}

// --- Operator final decision (human-in-the-loop) ---------------------------
// The AI auto-adjudicates; an operator makes the FINAL call — resolve a
// MANUAL_REVIEW or override the AI — with a required note. Persisted + audited.
// Ops-only (open when auth off).

export interface OperatorDecisionResult {
  claim_id: string;
  before: { status: string; amount: number };
  after: { status: string; amount: number };
  decided_by: string;
  decided_at: string;
  persisted: boolean;
}

export function operatorDecision(
  id: string,
  body: {
    status: "APPROVED" | "PARTIAL" | "REJECTED";
    approved_amount?: number;
    note: string;
  }
): Promise<OperatorDecisionResult> {
  return request<OperatorDecisionResult>(
    `/api/claims/${encodeURIComponent(id)}/decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    30_000
  );
}

// --- Operator outcome label (ops) ------------------------------------------
// Label whether the AUTOMATED decision was correct. This is the training signal
// for confidence calibration / conformal risk control (operator agreement on the
// final decision), stored alongside the decision's own confidence.
export interface MarkOutcomeResult {
  claim_id: string;
  labeled: boolean;
  correct: boolean;
  confidence: number;
  audit_row: string | null;
}

export function markOutcome(id: string, correct: boolean): Promise<MarkOutcomeResult> {
  return request<MarkOutcomeResult>(
    `/api/claims/${encodeURIComponent(id)}/mark-outcome`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ correct }),
    },
    15_000
  );
}

// --- Append-only audit trail (ops) -----------------------------------------
// One row per decision + per ops correction: actor, action, the decision status
// + approved_amount AFTER that event, reason codes, and a timestamp. Oldest first.
// The backend degrades to [] when the audit_log table/row is missing.

export interface AuditEntry {
  // Backend uses a hex string id; typed loosely to tolerate either.
  id: string | number;
  claim_id: string;
  actor: string | null;
  // "DECISION" | "CORRECTION" (uppercase from the backend).
  action: string;
  decision_status: string | null;
  approved_amount: number | null;
  // Shape varies by action: a DECISION row carries a list of reason-code strings;
  // a CORRECTION row carries a { changed_fields, before, after } object. The UI
  // normalizes both, so this is intentionally permissive.
  reason_codes: string[] | Record<string, unknown>;
  created_at: string | null;
}

// Ops-only: the append-only decision & correction history for a claim.
export function getClaimAudit(id: string): Promise<AuditEntry[]> {
  return request<AuditEntry[]>(
    `/api/claims/${encodeURIComponent(id)}/audit`,
    undefined,
    30_000
  );
}

export function getClaimDocuments(id: string): Promise<ClaimDocument[]> {
  return request<ClaimDocument[]>(
    `/api/claims/${encodeURIComponent(id)}/documents`
  );
}

// The raw file path — used directly as an <img>/<embed> src (hits the proxy/nginx).
export function documentFileUrl(claimId: string, fileId: string): string {
  return `/api/claims/${encodeURIComponent(claimId)}/documents/${encodeURIComponent(fileId)}`;
}

// --- Ops dashboard (read-only analytics) -----------------------------------

export function getOpsAnalytics(): Promise<OpsAnalytics> {
  return request<OpsAnalytics>("/api/ops/analytics");
}

export function getWorklist(filters: WorklistFilters = {}): Promise<WorklistItem[]> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.category) params.set("category", filters.category);
  if (filters.q) params.set("q", filters.q);
  if (filters.sort) params.set("sort", filters.sort);
  const qs = params.toString();
  return request<WorklistItem[]>(`/api/ops/worklist${qs ? `?${qs}` : ""}`);
}

export function getFraudQueue(): Promise<FraudClaim[]> {
  return request<FraudClaim[]>("/api/ops/fraud");
}

export interface ImprovementProposal {
  area: string;
  observation: string;
  proposed_change: string;
  rationale: string;
  risk: string;
  auto_applicable: boolean;
}

export interface ImprovementProposals {
  findings: Record<string, unknown>;
  proposals: ImprovementProposal[];
  error?: string;
}

// System self-assessment (advisory only): the system reads its own eval outputs
// and proposes improvements. Nothing here changes a decision; purely informational.
export function getImprovementProposals(): Promise<ImprovementProposals> {
  return request<ImprovementProposals>("/api/ops/improvement-proposals");
}

export function getEvalCases(): Promise<EvalCase[]> {
  return request<EvalCase[]>("/api/eval/cases");
}

export function runEval(): Promise<EvalCaseResult[]> {
  return request<EvalCaseResult[]>(
    "/api/eval/run",
    { method: "POST" },
    660_000
  );
}

// --- Message-quality eval (LLM-as-judge) -----------------------------------
// Grades the MEMBER-FACING message of each of the 12 eval cases on a 1-5 rubric
// (specificity / actionability / correctness / tone / jargon_free + overall) via
// an LLM judge. A live call (~12 judge invocations, ~1-2 min). Ops-only when auth
// is on. Additive: grades existing text — no decision is ever changed.

export interface MessageQualityCase {
  case_id: string;
  case_name: string;
  errored?: boolean;
  specificity?: number;
  actionability?: number;
  correctness?: number;
  tone?: number;
  jargon_free?: number;
  overall?: number;
  message?: string;
}

export interface MessageQualityResult {
  n: number;
  n_total: number;
  aggregate: {
    specificity: number;
    actionability: number;
    correctness: number;
    tone: number;
    jargon_free: number;
    overall: number;
  };
  per_case: MessageQualityCase[];
}

export function gradeMessageQuality(): Promise<MessageQualityResult> {
  return request<MessageQualityResult>(
    "/api/eval/message-quality",
    { method: "POST" },
    300_000
  );
}

// --- Policy-as-code studio (ops-only) --------------------------------------
// Manages POLICY VERSIONS. The active version is v1 == the original policy_terms.json
// until an operator explicitly activates another. Preview is read-only.

// A policy document is free-form JSON (coverage tables, limits, rules, …).
// We treat values as `unknown` and narrow at the (few) use sites that read them.
export type PolicyJson = Record<string, unknown>;

export interface PolicyVersionMeta {
  id: string;
  version_no: number;
  label: string | null;
  is_active: boolean;
  created_by: string | null;
  created_at: string | null;
}

export interface PolicyVersionFull extends PolicyVersionMeta {
  policy_json: PolicyJson;
}

export interface PolicyDiffChange {
  path: string;
  before: unknown;
  after: unknown;
  change: "added" | "removed" | "changed";
}

export interface PolicyDiff {
  a: { id: string; version_no: number; label: string | null };
  b: { id: string; version_no: number; label: string | null };
  changes: PolicyDiffChange[];
}

export interface PolicyDecisionSummary {
  status: string;
  approved_amount: number;
  reason_codes: { code: string; detail: string }[];
}

export interface PolicyPreview {
  sample: {
    label: string;
    source: string;
    member_id: string;
    category: string;
    claimed_amount: number;
    hospital_name: string | null;
    line_items: { description: string; amount: number }[];
  };
  before: PolicyDecisionSummary;
  after: PolicyDecisionSummary;
  changed: boolean;
}

export function getCurrentPolicy(): Promise<PolicyVersionFull> {
  return request<PolicyVersionFull>("/api/policy/current");
}

export function listPolicyVersions(): Promise<PolicyVersionMeta[]> {
  return request<PolicyVersionMeta[]>("/api/policy/versions");
}

export function createPolicyVersion(
  policy_json: PolicyJson,
  label: string
): Promise<PolicyVersionFull> {
  return request<PolicyVersionFull>("/api/policy/versions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ policy_json, label }),
  });
}

export function activatePolicyVersion(id: string): Promise<PolicyVersionMeta> {
  return request<PolicyVersionMeta>(
    `/api/policy/versions/${encodeURIComponent(id)}/activate`,
    { method: "POST" },
    30_000
  );
}

export function policyDiff(aId: string, bId: string): Promise<PolicyDiff> {
  return request<PolicyDiff>(
    `/api/policy/versions/${encodeURIComponent(aId)}/diff/${encodeURIComponent(bId)}`
  );
}

export function previewPolicy(
  policy_json: PolicyJson,
  opts: { test_case_id?: string; sample?: PolicyJson }
): Promise<PolicyPreview> {
  return request<PolicyPreview>(
    "/api/policy/preview",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ policy_json, ...opts }),
    },
    30_000
  );
}
