# Demo Video Script (8–12 min)

Record on the deployed URL (or `docker compose up` locally at http://localhost). Have the
backend warm and Postgres up. Pre-render the fixture documents so you have real files to
upload: `cd backend && .venv/bin/python scripts/export_fixtures.py` → files land in
`backend/storage/fixtures/<CASE_ID>/`.

The assignment requires three things: (1) a claim stopped early on a document problem with the
error shown, (2) a successful end-to-end approval with the full trace visible, (3) one decision
you're proud of and one you'd change. This script covers all three plus a short framing.

---

## 0. Framing — 45s

> "This is a health-insurance claims processing system for Plum. An employee uploads medical
> documents and basic details; the system verifies the documents, extracts the data, applies the
> member's policy, and returns APPROVED / PARTIAL / REJECTED / MANUAL_REVIEW — with a full,
> reconstructable trace and a confidence score.
>
> The one design principle that drives everything: **the LLM proposes, deterministic code
> decides.** Google Gemini does vision extraction, document classification, and free-text→policy
> mapping. But every number and every verdict — co-pay, sub-limits, waiting periods, the final
> decision — is deterministic Python. Same claim, same decision, same trace, every time. That's
> what makes it auditable, and it's the standard pattern in regulated decisioning."

Show the landing/Submit page briefly — point out the Plum-branded UI.

---

## 1. Document problem caught early — 2 min  (assignment requirement #1)

Use **TC001 (Wrong Document Uploaded)**: a consultation claim with two prescriptions and no
hospital bill.

1. On **Submit**, choose member **Rajesh Kumar (EMP001)**, type **CONSULTATION**, date
   **2024-11-01**, amount **1500**. (Or use "Load a sample case → TC001" to pre-fill.)
2. Upload the two prescription images from `storage/fixtures/TC001/`.
3. Click **Submit claim**. Note: "this runs a live AI pipeline."
4. The **blocked screen** appears. Read the message aloud:
   > "For a CONSULTATION claim we need: PRESCRIPTION, HOSPITAL_BILL. You uploaded: PRESCRIPTION.
   > Missing: HOSPITAL_BILL. Please upload the HOSPITAL_BILL and resubmit — a PRESCRIPTION alone
   > is not sufficient."

> "This is the early-exit gate. Before any adjudication runs, the system classifies each
> document with vision, checks the required types for the claim category against the policy file,
> checks readability, and cross-checks that every document belongs to the same patient. The
> message is specific and actionable — it names exactly what was uploaded and what's missing.
> A generic 'invalid documents' error would be useless to the member."

Optional 20s: mention TC002 (an unreadable bill is flagged and the member is asked to re-upload
that specific file — not rejected) and TC003 (documents for two different patients are caught by
a deterministic name match, naming both people). These are the other two early-exit paths.

---

## 2. Full approval with the complete trace — 3.5 min  (assignment requirement #2)

Use **TC010 (Network Hospital — Discount Applied)** — it's the richest trace to walk because it
exercises the discount-before-co-pay financial logic. (TC004 is the simpler alternative.)

1. Submit member **Deepak Shah (EMP010)**, **CONSULTATION**, hospital **Apollo Hospitals**,
   amount **4500**, upload the TC010 prescription + bill from `storage/fixtures/TC010/`.
2. ~30s live pipeline. Land on the **Decision Review** page.
3. **Verdict**: APPROVED, **₹3,240** (green). Read the member message.
4. **Financial breakdown** — walk it slowly: gross ₹4,500 → **network discount 20% first** =
   −₹900 → ₹3,600 → **co-pay 10% on the post-discount amount** = −₹360 → **₹3,240**.
   > "Order matters here, and the policy is explicit that the network discount comes before
   > co-pay. The LLM never touches these numbers — this is a pure, unit-tested function. If I'd
   > let the model do the arithmetic it would pattern-match a plausible-looking number instead of
   > computing it."
5. **Confidence** — 100% with the four component bars (extraction, rule certainty, completeness,
   verifier agreement). "The score is itself explainable — it's a weighted composite, not a
   number the model made up."
6. **Decision trace** — the centerpiece. Scroll the timeline:
   intake → extract (×2, per document, in parallel) → docgate → semantic_map →
   adjudicate (×5 rules, in parallel: waiting period, coverage/exclusion, pre-auth, limits, fraud)
   → financial → decide → **verify** → explain.
   Expand a couple of steps to show policy refs, the model used, timing, and the verifier's
   reasoning.
   > "This is the observability requirement made literal: an ops reviewer can reconstruct exactly
   > what was checked, what passed, what failed, and why — purely from this trace. Each rule is a
   > specialized agent in a LangGraph fan-out; its verdict is a first-class trace entry."

---

## 3. The Eval — 30s

Open the **Eval** page. Show the 12 cases. Either click **Run all 12 (live)** if you have ~6
minutes of recording budget, or show the pre-generated `docs/eval_report.md` at **12/12**.

> "All twelve assignment test cases pass end-to-end through the real pipeline — real Gemini
> vision on rendered documents, no mocks anywhere, including the tests."

---

## 4. Proud of / would change — 2 min  (assignment requirement #3)

**Proud of — the deterministic core with LLM only at the edges, and the first-class trace.**
> "The architecture cleanly separates the stochastic part from the decision. The LLM is confined
> to what it's genuinely good at — reading messy documents and mapping free text to policy
> concepts — and is wrapped in structured output, validation, and a second-model judge. Every
> decision is deterministic and reproducible, and the trace is a product surface, not a log.
> Show me a graceful-degradation case to prove it:"
>
> Quickly run **TC011** (or show it from the claims list): a component is forced to fail
> mid-pipeline; the claim still completes with APPROVED, the failed component shows as
> ⚠ degraded in the trace, the confidence drops below the clean band, and a 'manual review
> recommended' note appears. "It degrades, it tells you it degraded, and it lowers its own
> confidence — it never crashes."

**Would change — confidence calibration and queue-based scale.**
> "Two things, given more time. First, the confidence score is a principled composite but it
> isn't statistically calibrated — with only 12 cases I can't fit a calibration curve, so a 0.9
> doesn't yet provably mean 90% correct. In production I'd log outcomes and fit an isotonic
> calibration so the score is trustworthy enough to gate auto-approval. Second, the pipeline runs
> synchronously per request, which is fine at today's volume but won't hold at 10x. I'd move
> claim processing onto an async queue with dedicated vision workers, batch the Gemini calls with
> backoff and circuit breakers, and put the whole thing behind an autoscaling group — the
> stateless design already supports that, it's just not wired yet."

---

## Closing — 15s

> "Repo, deployed URL, architecture doc, component contracts, and the 12/12 eval report are all
> in the submission. Happy to go deeper on any component in the technical round."

---

### Recording checklist
- [ ] Backend warm, Postgres up, fixtures rendered.
- [ ] Browser zoomed enough that the trace text is readable on video.
- [ ] Have TC001 (blocked), TC010 (approval), TC011 (degradation) fixtures ready to upload.
- [ ] `docs/eval_report.md` open in a tab as the 12/12 backup.
- [ ] Keep it 8–12 min; the trace walk (section 2) is the heart — don't rush it.
