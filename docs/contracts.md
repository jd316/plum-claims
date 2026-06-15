# Component Contracts

Precise interface for every significant component — input, output, errors raised — derived
from and **verified against the as-built code** (`backend/app/`). Precise enough to reimplement
any single component without reading its source. Types reference the Pydantic models in
`app/models/schemas.py` (summarized in the appendix). Money is `float` rupees; dates are
`datetime.date`.

Convention per entry: **Signature · Input · Output · Errors · Notes.**

---

## 1. PolicyEngine — `app/services/policy_engine.py`

Single source of truth for `policy_terms.json`. No policy value is hardcoded anywhere else; no
network, no LLM.

- **Construct:** `PolicyEngine(path: str)` — loads and holds the parsed policy JSON.
- **Accessors:**
  - `policy_id -> str` (property)
  - `members() -> list[dict]`
  - `member(member_id: str) -> dict` — the member roster entry.
  - `category_rules(category: str) -> dict` — OPD-category sub-config (maps claim category →
    `opd_categories` key).
  - `waiting_days(condition: str) -> int | None` — condition-specific waiting days, or `None`.
  - `initial_waiting_days() -> int`; `waiting_conditions() -> dict`.
  - `exclusion_conditions() -> list[str]`; `is_excluded_condition(name: str) -> bool`.
  - `is_network(hospital: str | None) -> bool` — substring match against `network_hospitals`.
  - `pre_authorization() -> dict`; `document_requirements(category: str) -> dict`.
  - `per_claim_limit() -> float`; `annual_opd_limit() -> float`.
  - `fraud_thresholds() -> dict`; `submission_rules() -> dict`.
- **Errors:** `MemberNotFound(member_id)`, `UnknownCategory(category)`, `PolicyNotFound`
  (defined). File-open / `KeyError` propagate for a malformed policy file.

---

## 2. Intake node — `app/graph/nodes.py::intake`

- **Signature:** `intake(state: ClaimState) -> dict`
- **Input:** `state["submission"]: ClaimSubmission`.
- **Output (partial state):** `{member: dict, problems: list[DocumentProblem],
  trace: [TraceEntry]}`. On a clean intake `problems == []`.
- **Logic:** resolve member via `PolicyEngine.member`; enforce
  `claimed_amount >= submission_rules.minimum_claim_amount`. A missing member or a violated rule
  yields a `DocumentProblem(kind="INTAKE_VIOLATION")` (early-exit), never a raised exception.
- **Errors:** none escape; a missing member is caught and turned into an `INTAKE_VIOLATION`
  problem + `FAIL` trace.

---

## 3. ExtractionAgent — `app/agents/extraction.py` *(LLM, vision)*

- **Signature:** `extract_document(doc: DocumentInput) -> ExtractionResult`
- **Input:** one `DocumentInput` (`file_id`, `file_name`, `stored_path` — a real file on disk).
- **Output:** `ExtractionResult` — `doc_type`, `DocumentQuality{readable, quality_issues,
  overall_confidence}`, and source-bound fields (`patient_name`, `doctor_name`,
  `doctor_registration`, `diagnosis`, `treatment`, `hospital_name`, `document_date` each as a
  `StrField{value, confidence, source_text}`), `line_items: list[LineItem]`,
  `total_amount: NumField`, `fraud_signals: list[str]`. `file_id` is overwritten from the input.
- **Logic:** single Gemini vision call via `generate_structured([image_part(path), PROMPT],
  ExtractionResult)`; `temperature=0`, Pydantic `response_schema`. Classifies by document
  *structure/purpose* (a dental-clinic bill is `HOSPITAL_BILL`, not `DENTAL_REPORT`). On
  illegible input it sets `readable=false` and leaves fields null rather than hallucinate.
- **Errors:** `GeminiError` after 3 failed attempts. In the graph, the `extract_doc` node wraps
  this: on failure it emits a minimal `ExtractionResult(doc_type="UNKNOWN", readable=false)` +
  `ComponentFailure` and the pipeline continues.
- **Safety:** the document is treated as untrusted data; the model holds no tools and is
  instructed never to follow in-document instructions.

---

## 4. Identity matcher — `app/services/identity.py` *(deterministic)*

- **Signatures:**
  - `names_match(a: str, b: str) -> bool` — Jaro-Winkler normalized similarity ≥ `0.85`.
  - `check_patient_consistency(docs: list[ExtractionResult], member_name: str) -> list[DocumentProblem]`
- **Input:** the extraction results + the resolved member name.
- **Output:** a list of `DocumentProblem(kind="PATIENT_MISMATCH")` (empty if consistent). Only
  names the extractor is confident about (`confidence >= 0.5`) are considered; emits **one**
  clear, specific message naming the mismatched document, its patient, and the member.
- **Errors:** none (pure).

---

## 5. DocGate — `app/rules/docgate.py` *(deterministic, early-exit)*

- **Signature:** `check_documents(extractions: list[ExtractionResult], category: str,
  member_name: str, pe: PolicyEngine) -> list[DocumentProblem]`
- **Output:** a list of `DocumentProblem` (empty ⇒ proceed). Checks, **in order**, returning at
  the first failing category:
  1. **Unreadable** — any doc with `quality.readable == false` →
     `UNREADABLE_DOCUMENT`, "re-upload that specific document" (an unreadable doc can't prove its
     type, so this is checked first). *(TC002)*
  2. **Wrong / missing required type** — `document_requirements(category)` (`required` ∪
     `optional`) vs the doc types present. If a required type is missing **and** an
     out-of-category type was uploaded (a type with no role in this category, e.g. a dental
     report for a consultation) → `WRONG_DOCUMENT`, naming the offending type and what is
     needed instead. If the uploaded set is merely incomplete but all-correct types
     (TC001: two prescriptions, no bill) → `MISSING_DOCUMENT`, naming uploaded vs required vs
     missing. An extra out-of-category doc alongside an otherwise-complete set does **not**
     block. *(TC001)*
  3. **Patient mismatch** — delegates to `check_patient_consistency`. *(TC003)*
- **Errors:** none (deterministic).

---

## 6. SemanticMap — `app/agents/semantic_map.py` *(LLM)*

- **Signature:** `map_semantics(category: str, extractions: list[ExtractionResult],
  pe: PolicyEngine) -> SemanticMapping`
- **Input:** claim category + aggregated diagnosis/treatment/line-item text; the policy's
  waiting-condition keys and exclusion strings are injected as the allowed vocabulary.
- **Output:** `SemanticMapping{category_match, mapped_category, exclusion_candidates[],
  waiting_condition, confidence}`. Chooses **only** from the supplied vocabulary;
  `exclusion_candidates` is reserved for whole-claim exclusions (e.g. obesity/bariatric) — NOT
  per-line cosmetic add-ons, which the rules handle as line-item exclusions. **Proposes
  concepts only; the rule agents decide.** `temperature=0`, structured output.
- **Errors:** `GeminiError` on failure; the `semantic_map` node wraps it (degraded → empty
  mapping, rules fall back to exact string match, confidence reduced).

---

## 7. Adjudication rule agents — `app/rules/*.py` *(deterministic, parallel fan-out)*

All five share `RuleContext` (`app/rules/base.py`) and the same shape:

- **Signature:** `check(ctx: RuleContext) -> RuleVerdict`
- **`RuleContext`:** `{submission: ClaimSubmission, member: dict,
  extractions: list[ExtractionResult], semantic: SemanticMapping, pe: PolicyEngine}` with a
  derived `line_items` property (line items from `HOSPITAL_BILL`/`PHARMACY_BILL` docs).
- **`RuleVerdict`:** `{rule, status: PASS|FAIL|FLAG|SKIPPED, reason_code, detail, policy_refs[],
  disallowed_items[], certainty}`.
- **Errors:** none raised in normal operation. Any agent may fail independently in the graph;
  the resilient wrapper records it and substitutes a `SKIPPED` verdict (`certainty=0`).

| Agent | FAIL/FLAG condition | reason_code | Notes |
|-------|--------------------|-------------|-------|
| **waiting_period** | `treatment_date − join_date` < initial waiting period, or < condition waiting days (from SemanticMap) | `WAITING_PERIOD` (FAIL) | detail states the eligible-from date *(TC005, TC012)* |
| **coverage_exclusion** | category not covered → `NOT_COVERED`; whole-claim excluded condition → `EXCLUDED_CONDITION`; else per-line excluded procedures listed in `disallowed_items` (PASS, drives PARTIAL) | `NOT_COVERED` / `EXCLUDED_CONDITION` | line-item exclusions return **PASS** with `disallowed_items` *(TC006, TC012)* |
| **pre_auth** | a `high_value_tests_requiring_pre_auth` test present **and** `claimed_amount > pre_auth_threshold` | `PRE_AUTH_MISSING` (FAIL) | detail tells how to resubmit (validity days) *(TC007)* |
| **limits** | CONSULTATION: `claimed_amount > per_claim_limit`; else covered amount > category `sub_limit`; plus `ytd_claims_amount + amount > annual_opd_limit` when YTD given | `PER_CLAIM_EXCEEDED` / `SUB_LIMIT_EXCEEDED` / `ANNUAL_LIMIT_EXCEEDED` (FAIL) | sub-limit checked against **covered** amount (excluded items don't count) *(TC008)* |
| **fraud_anomaly** | same-day claims > `same_day_claims_limit`; same-calendar-month claims > `monthly_claims_limit`; amount > `high_value_claim_threshold`; line-items don't sum to total; `claimed_amount` doesn't match the extracted bill total; any vision `fraud_signals` | `FRAUD_SIGNALS` (**FLAG**) | only ever routes to MANUAL_REVIEW; lists the specific signals *(TC009)*. `simulate_component_failure` injects a failure here *(TC011)* |

---

## 8. FinancialCalculator — `app/rules/financial.py` *(deterministic, pure)*

- **Signature:** `calculate(pe: PolicyEngine, category: str, is_network: bool,
  items: list[LineItem], disallowed: list[str]) -> FinancialBreakdown`
- **Input:** category rules (via `pe`), network flag, all candidate line items, and the list of
  disallowed item descriptions (from `coverage_exclusion`, case-insensitive).
- **Output:** `FinancialBreakdown{gross, network_discount_pct, network_discount_amount,
  post_discount, copay_pct, copay_amount, line_items: list[LineItemDecision], approved_amount,
  steps[]}`.
- **Order is critical:** `gross` = sum of approved line items; `post_discount = gross −
  gross·network_discount_pct`; **then** `copay_amount = post_discount·copay_pct`;
  `approved = post_discount − copay_amount`. For non-CONSULTATION categories with a `sub_limit`,
  the approved amount is capped at the sub-limit. Every step is appended to `steps[]`.
- **Verified outputs:** TC004 → ₹1,350 (10% copay); TC010 → ₹3,240 (20% network discount **then**
  10% copay); TC006 → ₹8,000 (one line excluded); TC007 → ₹10,000 (capped at DIAGNOSTIC sub-limit).
- **Errors:** none (pure arithmetic).

---

## 9. DecisionAggregator — `app/rules/aggregator.py` *(deterministic)*

- **Signature:** `aggregate(verdicts: list[RuleVerdict], financial: FinancialBreakdown,
  auto_review_above: float) -> Decision`
- **Output:** `Decision` (pre-verifier — `confidence`/`confidence_components` filled later by
  Explain). **Status mapping** (in order):
  1. any `FAIL` → **REJECTED**, `approved_amount=0`, financial zeroed for consistency.
  2. else any `FLAG` → **MANUAL_REVIEW**, amount 0.
  3. else `financial.approved_amount > auto_review_above` → **MANUAL_REVIEW** (HIGH_VALUE).
  4. else some line items unapproved → **PARTIAL**; otherwise **APPROVED**.
  Builds ranked `reason_codes` (≤4, priority order: permanent denials lead so the member message
  is not misleading — EXCLUDED > NOT_COVERED > PRE_AUTH > WAITING > limit codes > FRAUD), the
  member message (the highest-ranked reason's detail), and `recommendations` for any SKIPPED rule.
- **Errors:** none.

---

## 10. confidence.compute — `app/services/confidence.py` *(deterministic)*

- **Signature:** `compute(extraction_quality: float, rule_certainty: float, completeness: float,
  verifier_agreement: float, failures: int) -> ConfidenceScore`
- **Output:** `ConfidenceScore{final: float, components: ConfidenceComponents}`.
  `C_raw = 0.30·extraction_quality + 0.30·rule_certainty + 0.20·completeness +
  0.20·verifier_agreement`; `penalty = 1 − (1 − 0.20)^failures` (0 if no failures);
  `final = round(C_raw · (1 − penalty), 3)`. All components stored for explainability.
- **Errors:** none.

---

## 11. DecisionVerifier — `app/agents/verifier.py` *(LLM-as-judge, Pro model)*

- **Signature:** `verify(decision: Decision, verdicts: list[RuleVerdict]) -> VerifierResult`
- **Input:** the deterministic decision (minus confidence components) and the rule verdicts.
- **Output:** `VerifierResult{verdict: PASS|FAIL, confidence: float, reason: str}`. Judges only
  **internal consistency** (right status for the verdicts, plausible arithmetic) — it **cannot
  recompute amounts**. An APPROVED/PARTIAL alongside any FAIL verdict is a hard contradiction →
  FAIL; a PASS coverage verdict whose detail notes a dropped line item (driving PARTIAL) is
  *consistent*. Uses `gemini_pro_model`.
- **Effect:** in the `explain` node, a verifier FAIL on an APPROVED/PARTIAL forces
  **MANUAL_REVIEW** + a recommendation; it can never flip a rejection to an approval. Feeds
  `verifier_agreement` (its confidence on PASS, else 0).
- **Errors:** `GeminiError`; wrapped (degraded → neutral agreement, confidence penalty).

---

## 12. Graph: state, nodes, build — `app/graph/`

- **`ClaimState`** (`state.py`, `TypedDict`, `total=False`): `submission`, `member`,
  `extractions` *(reducer: `operator.add`)*, `problems`, `semantic`,
  `rule_verdicts` *(reducer)*, `financial`, `decision`, `verifier`, `trace` *(reducer)*,
  `failures` *(reducer)*. Helper `trace(step, agent, status, summary, ...) -> TraceEntry`.
- **`resilient(agent_name, *, critical=False)`** (`nodes.py`): decorator wrapping every node;
  catches exceptions or honors `simulate_component_failure` (injects into `fraud_anomaly`),
  appends a `ComponentFailure` + degraded `TraceEntry`, substitutes a `SKIPPED` verdict for
  rule nodes, and (if `critical`) emits an INTAKE_VIOLATION problem. The pipeline continues.
- **Nodes:** `intake`, `extract_doc` (fan-out target), `docgate` (`defer=True`),
  `semantic_map`, `rule_check` (fan-out target dispatching to the 5 rule nodes),
  `financial_calc` (`defer=True`), `decide`, `verifier_node`, `explain`. Each returns a partial
  state dict and emits exactly one `TraceEntry` (the rule fan-out emits one per rule).
- **Routers:** `fan_out_extraction` (→ one `Send("extract_doc", …)` per document, or `"explain"`
  on intake problems); `route_after_docgate` (→ `"explain"` on a doc problem, else
  `"semantic_map"`); `fan_out_rules` (→ one `Send("rule_check", …)` per rule).
- **`build.py`:** `build_graph() -> CompiledGraph`; `run_claim(submission) -> dict` invokes the
  compiled graph with `max_concurrency=4` and returns the final `ClaimState` dict.

---

## 13. Persistence — `app/services/persistence.py`, `audit.py`, `policy_store.py`, `auth.py`

- **Schema (4 tables, managed by Alembic migrations `0001`–`0007`):**
  - **`claims`** (`0001`/`0002`) — `id` (PK), `created_at` (timestamptz), `member_id`,
    `category`, `blocked`, `status`, `approved_amount`, `confidence`, `submission` (JSON),
    `result` (JSON — full `ClaimResult`: decision + trace + failures). The trace is stored as a
    single immutable JSON document; `0007` adds the per-step trace-entry fields.
  - **`users`** (`0003`) — `id` (PK), `username`, `password_hash`, `role` (`OPS`|`MEMBER`),
    `member_id?`, `created_at`. Backs auth (`app/services/auth.py`).
  - **`audit_log`** (`0004`) — append-only: `id` (PK), `claim_id`, `actor`, `action`,
    `decision_status?`, `approved_amount?`, `reason_codes` (JSON), `created_at`. One row per
    decision / operator-override / correction / outcome-mark (`app/services/audit.py`).
  - **`policy_versions`** (`0005`) — `id` (PK), `version_no`, `label?`, `policy_json` (JSON),
    `policy_text?`, `is_active`, `created_by?`, `created_at`. Backs the policy studio /
    versioning endpoints (`app/services/policy_store.py`); exactly one row has `is_active=true`.
- **Functions (persistence.py):**
  - `init_db() -> None` — create tables (called on FastAPI startup).
  - `save_claim(sub: ClaimSubmission, result: ClaimResult) -> str` — persist, return `claim_id`.
  - `get_claim(claim_id: str) -> dict | None` — the stored `result` JSON, or `None`.
  - `list_claims() -> list[dict]` — newest-first summaries (≤100): claim_id, created_at,
    member_id, category, blocked, status, approved_amount, confidence.
- **Errors:** SQLAlchemy/DB errors propagate (DB is required, not degraded around). The audit
  writer is best-effort (a failed audit insert logs a warning and never fails a completed claim).
- **Note:** the `claims` table denormalizes the trace into one JSON document (the design spec's
  3-table claim split was simplified to keep the trace immutable as a single document); auth,
  audit, and policy-versioning each get their own first-class table.

---

## 14. REST API — `app/main.py` (FastAPI)

**Auth model:** JWT bearer (cookie or `Authorization` header). `USER` = any authenticated
principal (member or operator); `OPS` = operator-only (RBAC enforced via the `require_user` /
`require_ops` dependencies); `opt` = works with or without a token. Members submit and view
their own claims; operators review, correct, and decide — operators do **not** submit (RBAC).
All authenticated routes return **401** when the token is missing/invalid and **403** when the
role is insufficient; these rows are omitted from the per-route Errors column for brevity.

### Infra / health
| Method · Path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `GET /metrics` | — | — | Prometheus exposition text | — |
| `GET /api/health` | — | — | `{status: "ok"}` | — |
| `GET /api/ready` | — | — | `{ready: bool, db, redis}` | **503** if a hard dependency is down |

### Auth — `/api/auth/*`
| Method · Path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `POST /api/auth/login` | — | `{username, password}` | `{token, role, member_id?}` | **401** bad creds; **429** rate-limited |
| `POST /api/auth/logout` | — | bearer token | `{ok: true}` | — (token added to revocation list) |
| `GET /api/auth/me` | opt | — | `{authenticated, role?, member_id?}` | — |
| `GET /api/auth/config` | — | — | `{auth_enabled, wayfinding}` | — |

### Claim submission & intake
| Method · Path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/members` | OPS | — | `[{member_id, name, relationship}]` | — |
| `GET /api/policy/document-requirements` | USER | — | required/optional doc types per category | — |
| `POST /api/documents/classify` | USER | one `file` (multipart) | `{doc_type, quality, status: ok\|wrong\|unreadable\|unknown}` (pre-submission shift-left; LLM hiccup degrades to `unknown` 200, never 500) | **413** too large; **415** bad type |
| `POST /api/claims` | USER | **multipart**: `payload` (Form, JSON `ClaimSubmission` minus `documents`) + `files` (≥1) | `ClaimResult`: `{claim_id, blocked, problems[], decision, trace[], failures[]}` | **413/415** bad upload; **422** bad form/JSON; pipeline failures degrade into the result, not raised. Honors `Idempotency-Key` |
| `POST /api/claims/async` | USER | as `POST /api/claims` | `{job_id}` (202) — falls back to synchronous result if the broker is down | as above |
| `GET /api/jobs/{job_id}` | USER | path `job_id` | `{status: queued\|running\|done\|failed, result?}` | **404** unknown job; **403** not the owner |

### Claim read / explainability (Observability)
| Method · Path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/claims` | USER | — | `list_claims()` summaries (member-scoped for members) | — |
| `GET /api/claims/{id}` | USER | path `claim_id` | stored `ClaimResult` JSON | **404** not found; **403** not owner |
| `GET /api/claims/{id}/documents` | USER | path | `[{file_id, doc_type, file_name, url}]` | **404** |
| `GET /api/claims/{id}/documents/{file_id}` | USER | path | the stored document bytes (decrypted) | **404** |
| `POST /api/claims/{id}/replay` | USER | path | `{reproduced: bool, decision, trace}` — re-runs the deterministic pipeline on the stored facts and checks the result is identical | **404** |
| `GET /api/claims/{id}/counterfactuals` | USER | path | `[{factor, change, would_become}]` — minimal edits that flip the decision | **404** |
| `POST /api/claims/{id}/what-if` | USER | `{overrides}` | recomputed decision under hypothetical facts | **404**; **422** |
| `GET /api/claims/{id}/audit` | OPS | path | the append-only `audit_log` rows for the claim | **404** |

### Human-in-the-loop (operator)
| Method · Path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `POST /api/claims/{id}/correct` | OPS | `{field, value, reason}` | corrected `ClaimResult` (re-adjudicated); writes audit + correction history | **404**; **422** |
| `POST /api/claims/{id}/decision` | OPS | `{status, approved_amount?, note}` | operator final decision/override; writes audit | **404**; **422** |
| `POST /api/claims/{id}/mark-outcome` | OPS | `{outcome}` | records the realized outcome (feeds calibration) | **404**; **422** |

### Assistant / estimation (LLM-backed)
| Method · Path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `POST /api/estimate` | USER | `{category, claimed_amount, hospital_name?, …}` | pre-submission payout estimate with breakdown | **422** |
| `POST /api/claims/{id}/ask` | USER | `{question}` | grounded NL answer about that claim's decision/trace | **404**; **422** |
| `POST /api/policy/ask` | USER | `{question}` | grounded NL answer about policy terms (RAG) | **422** |
| `POST /api/claims/parse` | USER | `{text}` | structured `ClaimSubmission` fields parsed from free-text intake | **422** |

### Ops dashboard
| Method · Path | Auth | Response |
|---|---|---|
| `GET /api/ops/analytics` | OPS | decision/volume/confidence aggregates |
| `GET /api/ops/worklist` | OPS | the manual-review / needs-input queue |
| `GET /api/ops/fraud` | OPS | flagged fraud signals across claims |
| `GET /api/ops/improvement-proposals` | OPS | self-improvement proposals from outcome analysis |

### Eval
| Method · Path | Auth | Request | Response |
|---|---|---|---|
| `GET /api/eval/cases` | OPS | — | the 12 raw test cases |
| `POST /api/eval/run` | OPS | — | `[{case_id, case_name, matched, notes[], result}]`; also writes `docs/eval_report.md` |
| `POST /api/eval/message-quality` | OPS | — | member-message quality scores; writes `docs/message_quality_report.md` |

### Policy studio / versioning
| Method · Path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/policy/current` | OPS | — | the active policy (json + text) | — |
| `GET /api/policy/versions` | OPS | — | `[{id, version_no, label, is_active, created_at}]` | — |
| `GET /api/policy/versions/{id}` | OPS | path | one version's full `policy_json` | **404** |
| `GET /api/policy/versions/{id}/diff/{other_id}` | OPS | path | structured diff between two versions | **404** |
| `POST /api/policy/versions` | OPS | `{policy_json, label}` | created version (inactive) | **422** invalid policy |
| `POST /api/policy/versions/{id}/activate` | OPS | path | activates the version (hot-reload, invalidates engine cache) | **404** |
| `POST /api/policy/preview` | OPS | `{policy_json, claim_id}` | re-adjudicates a stored claim under a candidate policy without activating it | **404**; **422** |

**Notes:** uploaded files are stored under `storage/uploads/{claim_id}/F{nnn}{ext}` with a
**server-generated** filename (path-traversal safe) and **encrypted at rest**; the original
filename is kept only as display metadata. Every claim-mutating operator action
(`/correct`, `/decision`, `/mark-outcome`) writes an `audit_log` row.

---

## 15. Eval runner — `app/evalrunner/runner.py`, `matching.py`

- `run_all(out_dir=None) -> list[dict]` — for each of the 12 cases: render real fixture
  documents, run the **real** pipeline (live vision) via `run_claim`, match against the expected
  outcome, collect `{case_id, case_name, matched, notes[], result}`.
- `state_to_result(state, claim_id) -> ClaimResult` — seq-number the trace and assemble the
  result.
- `match_case(case, result) -> (bool, list[str])` — compare produced decision/blocked outcome to
  the expected outcome (containment-based reason matching, so extra true reasons never hurt).
- `to_markdown(results) -> str` — render the Markdown eval report (`docs/eval_report.md`).

---

## Appendix — core data models (`app/models/schemas.py`)

```python
StrField  { value: str|None,   confidence: float, source_text: str|None }
NumField  { value: float|None, confidence: float, source_text: str|None }
LineItem  { description: str, amount: float, confidence: float = 1.0 }

DocumentInput   { file_id, file_name?, stored_path }
ClaimSubmission { member_id, policy_id, claim_category, treatment_date, claimed_amount,
                  hospital_name?, ytd_claims_amount?, claims_history[], simulate_component_failure,
                  documents[] }
DocumentQuality { readable: bool, quality_issues: list[str], overall_confidence: float }
ExtractionResult{ file_id, doc_type, quality, patient_name, doctor_name, doctor_registration,
                  diagnosis, treatment, hospital_name, document_date, line_items[], total_amount,
                  fraud_signals[] }
SemanticMapping { category_match, mapped_category?, exclusion_candidates[], waiting_condition?,
                  confidence }
RuleVerdict     { rule, status: PASS|FAIL|FLAG|SKIPPED, reason_code?, detail, policy_refs[],
                  disallowed_items[], certainty }
LineItemDecision{ description, amount, approved: bool, reason? }
FinancialBreakdown { gross, network_discount_pct, network_discount_amount, post_discount,
                     copay_pct, copay_amount, line_items[], approved_amount, steps[] }
ReasonCode      { code, detail }
ConfidenceComponents { extraction_quality, rule_certainty, completeness, verifier_agreement,
                       degradation_penalty }
VerifierResult  { verdict: PASS|FAIL, confidence, reason }
Decision        { status, approved_amount, reason_codes[], member_message, confidence,
                  confidence_components?, recommendations[], financial? }
DocumentProblem { kind: WRONG_DOCUMENT|MISSING_DOCUMENT|UNREADABLE_DOCUMENT|PATIENT_MISMATCH|
                        INTAKE_VIOLATION, file_id?, message }
TraceEntry      { seq, step, agent, status: PASS|FAIL|FLAG|SKIPPED|ERROR|INFO, summary, detail{},
                  policy_refs[], model?, input_tokens?, output_tokens?, confidence_delta?,
                  degraded, failure_mode?, duration_ms }
ComponentFailure{ agent, failure_mode, recoverable }
ClaimResult     { claim_id, blocked, problems[], decision?, trace[], failures[] }
```
