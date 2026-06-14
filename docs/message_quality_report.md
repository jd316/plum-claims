# Message-Quality Eval Report (LLM-as-judge on member-facing messages)

An LLM judge (Gemini Pro, temperature 0, structured output) scores the MEMBER-FACING message of each of the 12 eval cases on a 1-5 rubric. This is the message-quality leg of the eval framework — the decision legs measure accuracy / P-R-F1 / amount-MAE / extraction field-F1; this measures the wording members see (the TC001-003 blocking messages and the rejection / decision messages). PURE-ADDITIVE: no decision is changed; existing text is graded. `overall` is the mean of the five dimensions.

- **Cases graded:** 12 / 12
- **Overall mean:** 4.07 / 5

## Aggregate scores per dimension (mean over graded cases)

| dimension | mean (1-5) |
|---|---:|
| specificity | 4.92 |
| actionability | 3.33 |
| correctness | 4.83 |
| tone | 3.25 |
| jargon_free | 4.00 |
| **overall** | **4.07** |

## Per-case scores

| case | specificity | actionability | correctness | tone | jargon_free | overall |
|---|---:|---:|---:|---:|---:|---:|
| TC001 Wrong Document Uploaded | 5 | 5 | 5 | 3 | 3 | 4.20 |
| TC002 Unreadable Document | 5 | 5 | 5 | 5 | 5 | 5.00 |
| TC003 Documents Belong to Different Patients | 5 | 5 | 5 | 5 | 5 | 5.00 |
| TC004 Clean Consultation — Full Approval | 4 | 3 | 5 | 4 | 5 | 4.20 |
| TC005 Waiting Period — Diabetes | 5 | 3 | 5 | 2 | 4 | 3.80 |
| TC006 Dental Partial Approval — Cosmetic Exclusion | 5 | 2 | 5 | 3 | 4 | 3.80 |
| TC007 MRI Without Pre-Authorization | 5 | 5 | 5 | 3 | 4 | 4.40 |
| TC008 Per-Claim Limit Exceeded | 5 | 1 | 3 | 3 | 5 | 3.40 |
| TC009 Fraud Signal — Multiple Same-Day Claims | 5 | 5 | 5 | 2 | 2 | 3.80 |
| TC010 Network Hospital — Discount Applied | 5 | 2 | 5 | 4 | 5 | 4.20 |
| TC011 Component Failure — Graceful Degradation | 5 | 2 | 5 | 3 | 5 | 4.00 |
| TC012 Excluded Treatment | 5 | 2 | 5 | 2 | 1 | 3.00 |

## Graded messages + rationale

### TC001 — Wrong Document Uploaded
- **Message:** For a CONSULTATION claim we need: PRESCRIPTION, HOSPITAL_BILL. You uploaded: PRESCRIPTION. Missing: HOSPITAL_BILL. Please upload the HOSPITAL_BILL and resubmit — a PRESCRIPTION alone is not sufficient.
- **Scores:** specificity=5, actionability=5, correctness=5, tone=3, jargon_free=3, overall=4.20
- **Judge rationale:** The message is highly specific, actionable, and correct, but uses robotic phrasing and system-like ALL_CAPS enums (e.g., HOSPITAL_BILL) which impacts tone and jargon scores.

### TC002 — Unreadable Document
- **Message:** We could not read your pharmacy bill (The image is extremely blurry, out of focus, and completely illegible.). Your claim is on hold — please re-upload a clear photo or scan of your pharmacy bill. The rest of your documents are fine.
- **Scores:** specificity=5, actionability=5, correctness=5, tone=5, jargon_free=5, overall=5.00
- **Judge rationale:** The message clearly identifies the unreadable pharmacy bill, provides exact next steps, aligns perfectly with the blocked status, and uses plain language.

### TC003 — Documents Belong to Different Patients
- **Message:** Document F006 is for patient 'Arjun Mehta' while other documents are for 'Rajesh Kumar', but this claim was submitted for member 'Rajesh Kumar'. All documents must belong to the same patient. Please upload documents for 'Rajesh Kumar'.
- **Scores:** specificity=5, actionability=5, correctness=5, tone=5, jargon_free=5, overall=5.00
- **Judge rationale:** The message accurately identifies the mismatched document and patient names, provides clear instructions to resolve the issue, and maintains a professional, jargon-free tone.

### TC004 — Clean Consultation — Full Approval
- **Message:** Approved ₹1,350.00.
- **Scores:** specificity=4, actionability=3, correctness=5, tone=4, jargon_free=5, overall=4.20
- **Judge rationale:** The message correctly states the approved amount without jargon, but is extremely brief and lacks details on payout timelines or next steps.

### TC005 — Waiting Period — Diabetes
- **Message:** 'diabetes' has a 90-day waiting period. Member joined 2024-09-01; treatment on 2024-10-15 is day 44. Eligible for diabetes-related claims from 2024-11-30.
- **Scores:** specificity=5, actionability=3, correctness=5, tone=2, jargon_free=4, overall=3.80
- **Judge rationale:** The message is highly specific and factually correct but reads like an internal system note rather than a professional member-facing communication.

### TC006 — Dental Partial Approval — Cosmetic Exclusion
- **Message:** Approved ₹8,000.00. 'Teeth Whitening' (₹4,000) was not approved: Excluded procedure under policy (cosmetic/not covered)
- **Scores:** specificity=5, actionability=2, correctness=5, tone=3, jargon_free=4, overall=3.80
- **Judge rationale:** The message is highly specific and perfectly accurate but reads like a robotic system output and lacks clear next steps or closure for the member.

### TC007 — MRI Without Pre-Authorization
- **Message:** MRI above ₹10,000 requires pre-authorization, and none was submitted with this claim. To proceed: obtain pre-authorization from the insurer (valid 30 days) and resubmit the claim with the pre-auth reference number. Covered amount ₹15,000 exceeds the DIAGNOSTIC sub-limit of ₹10,000.
- **Scores:** specificity=5, actionability=5, correctness=5, tone=3, jargon_free=4, overall=4.40
- **Judge rationale:** The message is highly specific, actionable, and correct, but it reads like a robotic concatenation of internal rules and lacks a conversational tone.

### TC008 — Per-Claim Limit Exceeded
- **Message:** Claimed amount ₹7,500 exceeds the per-claim limit of ₹5,000.
- **Scores:** specificity=5, actionability=1, correctness=3, tone=3, jargon_free=5, overall=3.40
- **Judge rationale:** The message specifies exact amounts but fails to state the claim was rejected, lacks next steps, and has a blunt tone.

### TC009 — Fraud Signal — Multiple Same-Day Claims
- **Message:** Your claim needs a quick manual check by our team. No action is needed from you right now. Unusual pattern detected — routed to manual review. Signals: 4 claims on the same day (2024-10-30) exceeds the limit of 2
- **Scores:** specificity=5, actionability=5, correctness=5, tone=2, jargon_free=2, overall=3.80
- **Judge rationale:** Highly specific and correct, but copy-pasting internal fraud signal text makes the tone accusatory and exposes system jargon.

### TC010 — Network Hospital — Discount Applied
- **Message:** Approved ₹3,240.00.
- **Scores:** specificity=5, actionability=2, correctness=5, tone=4, jargon_free=5, overall=4.20
- **Judge rationale:** The message correctly states the approved amount without jargon but is extremely brief and lacks reassurance about payment timelines or next steps.

### TC011 — Component Failure — Graceful Degradation
- **Message:** Approved ₹4,000.00.
- **Scores:** specificity=5, actionability=2, correctness=5, tone=3, jargon_free=5, overall=4.00
- **Judge rationale:** The message correctly states the approved amount without jargon, but it is overly blunt and lacks information on next steps such as when the payment will be received.

### TC012 — Excluded Treatment
- **Message:** The treatment falls under policy exclusion(s): Obesity and weight loss programs. These are not covered under PLUM_GHI_2024. 'obesity_treatment' has a 365-day waiting period. Member joined 2024-04-01; treatment on 2024-10-18 is day 200. Eligible for obesity_treatment-related claims from 2025-04-01. Claimed amount ₹8,000 exceeds the per-claim limit of ₹5,000.
- **Scores:** specificity=5, actionability=2, correctness=5, tone=2, jargon_free=1, overall=3.00
- **Judge rationale:** The message is highly specific and factually correct but reads like a raw system log with internal formatting ('obesity_treatment'), poor robotic tone, and lacks clear next steps or closure.
