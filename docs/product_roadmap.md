# Product Roadmap — From Assignment to Standout Product

**Author:** Joydip Biswas · **Context:** Plum AI Engineer assignment → production-grade claims platform

This roadmap takes the delivered system (assignment-complete, 12/12 live, fully tested) and lays
out what turns it into a product that *stands out* — both for the technical review (where they
extend it live) and as a real thing Plum could ship toward 10M lives.

---

## North star

> One claims brain that decides like the best human adjudicator — fast, explainable, and honest
> about what it doesn't know — and gets cheaper per claim as volume grows, not more expensive.

## The standout thesis (what we lead with)

The review weights **System Design 30 · Engineering 25 · Observability 20 · AI 15 · Doc-Verif 10**,
and ends with a *live extension*. We win on four bets:

1. **Architecture they can't poke holes in** — "LLM proposes, deterministic code decides." Same
   claim → same decision → same trace. This is already built; we make it *undeniable* with replay.
2. **Observability as a product, not a log** — a trace you can replay, cost/latency per claim,
   and a confidence score that is itself explainable *and* calibrated.
3. **A demo moment they remember** — real-time "shift-left" document verification + a genuinely
   messy document the vision pipeline handles gracefully.
4. **Depth that proves we didn't just pass the 12 tests** — the whole policy enforced, a real eval
   framework, and a credible 10× scale story with a load test behind it.

---

## Where we are (delivered)
- Multi-agent LangGraph pipeline (intake → extract fan-out → doc-gate → semantic-map →
  adjudicate fan-out → financial → decide → verify → explain); 12/12 live; 70 deterministic tests.
- Real Gemini vision extraction; deterministic rules + financial; first-class trace; composite
  confidence; graceful degradation; Plum-branded React UI (submit, review+trace, eval, blocked).
- Dockerized; architecture + contracts + eval-report + demo-script docs.

---

## Tier 0 — Ship it (do first, ~half day; blocks submission)
Non-negotiable; nothing below matters until these are done.
- **Push to GitHub** with the clean history; enable access.
- **Deploy** (EC2 `docker compose up`, expose only port 80) → public URL.
- **Record the demo** from `docs/demo_script.md`. Rotate the Gemini key.

---

## Tier 1 — Standout, achievable before the deadline (recommended build set)
Ranked by (impact × demo-value) ÷ effort. Each keeps the verified 12/12 intact.

| # | Feature | Why it stands out | Criterion | Effort |
|---|---------|-------------------|-----------|--------|
| 1 | **Ops document viewer (split-screen review)** — left pane renders the uploaded docs (zoom/switch), right pane the decision + trace | Turns the review page into a real internal tool; "source of truth beside the machine's conclusion." Files are already stored — just serve + display | Observability, Doc-Verif | M |
| 2 | **Real-time "shift-left" upload verification** — per-category drop-zones + a classify-on-upload endpoint that instantly says "this is a Prescription; we need a Hospital Bill" before submit | The memorable demo moment; directly elevates the 10%-weighted early-detection criterion from "good" to "wow" | Doc-Verif, AI | M–L |
| 3 | **Messy-document robustness** — add fixtures for handwritten Rx, rubber-stamp-over-reg-no, multilingual, and a multi-page PDF; demo the pipeline degrading gracefully (quality flags + confidence drop, not failure) | Proves the extraction handles the *real* mess `sample_documents_guide.md` describes, not just clean renders | AI, Doc-Verif | M |
| 4 | **Observability deepening** — LangSmith tracing on; **decision replay** (re-run the verdict from stored extracted facts to prove determinism); per-claim **token cost + p95 latency** surfaced in the trace and UI | "Reconstruct any decision from the trace" becomes literal and auditable; cost-awareness is a senior signal | Observability, Eng | M |
| 5 | **Whole-policy enforcement** — floater `combined_limit` + annual accumulation from the persisted `claims` table; pharmacy branded-drug co-pay (extraction flags brand vs generic); enforce monthly limit (done) | Shows we enforce the *entire* `policy_terms.json`, not the subset the 12 tests touch — answers the obvious "what about the floater?" question before they ask | System Design | M |

**Recommended minimum to clearly stand out:** **#1 + #2 + #4** (the ops tool, the wow demo, and
the observability depth). Add **#3/#5** if time allows.

---

## Tier 2 — Production-grade (document as roadmap; build 0–1 if time)
- **AuthN/AuthZ + two personas** — member portal vs ops dashboard; scope claim reads to the owner;
  basic auth on the ops side for the deploy. (Security; required for real PHI.)
- **Async at scale** — move processing to a queue (Celery/SQS) + vision worker pool + a status
  endpoint the UI polls; **include a small load test** showing throughput. Makes the 10× story real,
  not hand-waved. (System Design 30%.)
- **Calibrated / risk-controlled confidence** — today's score is an *explainable triage signal*
  (drives auto-approve vs review + queue ranking), deliberately **not** claimed as a probability, so
  there is nothing to mis-calibrate. To make "0.9 = 90% correct" honestly true, fit on the RIGHT domain:
  logged `(composite_confidence, operator_agreed?)` pairs from the human-in-the-loop decision log —
  operator decisions are the labels — **not** extraction-field outcomes (the committed isotonic map is
  extraction-domain, which is why it ships OFF). Use Platt/temperature at low volume (isotonic overfits
  below ~hundreds of samples), graduate to isotonic at scale, and report held-out ECE before enabling.
  Stronger still: **conformal risk control** on the auto-approve gate — a distribution-free guarantee
  ("auto-approved claims have ≤ X% error") that needs only a modest exchangeable calibration set and
  avoids a fragile point-probability claim entirely. (AI maturity.)
- **Eval framework as a product** — expand beyond the 12 cases; report precision/recall per decision
  type; wire a regression gate into CI. (Engineering + AI.)
- **Compliance/audit** — immutable decision log, ECOA-style ranked reason codes (already emitted),
  encryption at rest, retention policy, PII handling. (Regulated-decisioning credibility.)
- **Multi-page PDF** — page-split + line-item aggregation per the document guide.

---

## Tier 3 — Visionary differentiators (discussion / future)
- **Agentic self-correction** — on low-confidence fields, auto re-extract at higher resolution or
  escalate to a Pro model; if a required value is still missing, generate a *specific* member
  follow-up ("we couldn't read the bill total — confirm the amount") instead of a blanket reject.
- **Member assistant** — conversational "why was my claim partial?" grounded in the trace.
- **Fraud ML layer** — learnable model on top of the deterministic signals; provider/network
  anomaly detection; document-tamper model behind the vision advisory.
- **Active learning loop** — every ops-agent correction becomes labeled data that improves
  extraction prompts and confidence calibration over time.
- **Policy-as-code studio** — versioned decision tables ops can edit without a deploy (the rules
  engine is already data-driven; expose it).

---

## Recommended ~3-day execution sequence
- **Day 1 (AM):** Tier 0 — push + deploy + key rotation. **(PM):** Tier 1 #1 document viewer.
- **Day 2:** Tier 1 #2 shift-left upload verification (+ per-category zones). **(PM):** #4 observability (replay + cost/latency).
- **Day 3 (AM):** Tier 1 #3 messy-doc fixtures + #5 one policy-completeness win. **(PM):** re-run full eval, record the demo, polish docs, write the "what I'd do next" section pointing at Tiers 2–3.

Every Tier-1 item ships behind the existing test gates; the 12/12 eval is re-run after each.

---

## How we talk about it in the review (the framing that wins)
- Lead with the **deterministic-core/LLM-edge** invariant and *prove* it with replay.
- Show the **shift-left** catch and the **messy-doc** graceful-degrade back to back — judgment under
  imperfect input is the whole job.
- Walk one **full trace** end to end; show **cost + latency**; show the **confidence components**.
- Close with this roadmap: "here's exactly what production needs, and why I sequenced it this way."
  Knowing what to *defer* and why is the senior signal they're testing for.
</content>
