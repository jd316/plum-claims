# Eval Report — 12 Test Cases (live pipeline)

**Matched: 12/12**


## TC001 — Wrong Document Uploaded — ✅ MATCH
**Outcome:** BLOCKED — For a CONSULTATION claim we need: PRESCRIPTION, HOSPITAL_BILL. You uploaded: PRESCRIPTION. Missing: HOSPITAL_BILL. Please upload the HOSPITAL_BILL and resubmit — a PRESCRIPTION alone is not sufficient.

<details><summary>Full trace</summary>

- `[01] intake/intake` **PASS** — member Rajesh Kumar resolved; submission rules satisfied
- `[02] extract/extraction` **PASS** — F001 → PRESCRIPTION; readable=True; patient=None (conf 0.00)
- `[03] extract/extraction_self_correction` **INFO** — F001: low confidence on ['diagnosis', 'patient_name'] → re-extracted with gemini-pro-latest; improved ['doctor_name', 'hospital_name', 'document_date']
- `[04] extract/extraction` **PASS** — F002 → PRESCRIPTION; readable=True; patient=None (conf 0.00)
- `[05] extract/extraction_self_correction` **INFO** — F002: low confidence on ['diagnosis', 'patient_name'] → re-extracted with gemini-pro-latest; improved ['doctor_name', 'hospital_name', 'document_date']
- `[06] docgate/doc_verification` **FAIL** — For a CONSULTATION claim we need: PRESCRIPTION, HOSPITAL_BILL. You uploaded: PRESCRIPTION. Missing: HOSPITAL_BILL. Please upload the HOSPITAL_BILL and resubmit — a PRESCRIPTION alone is not sufficient.
- `[07] explain/explainer` **FAIL** — Claim stopped before decision: For a CONSULTATION claim we need: PRESCRIPTION, HOSPITAL_BILL. You uploaded: PRESCRIPTION. Missing: HOSPITAL_BILL. Please upload the HOSPITAL_BILL and resubmit — a PRESCRIPTION alone is not sufficient.

</details>

## TC002 — Unreadable Document — ✅ MATCH
**Outcome:** BLOCKED — We could not read your pharmacy bill (The image is extremely blurry, out of focus, and completely illegible.). Your claim is on hold — please re-upload a clear photo or scan of your pharmacy bill. The rest of your documents are fine.

<details><summary>Full trace</summary>

- `[01] intake/intake` **PASS** — member Sneha Reddy resolved; submission rules satisfied
- `[02] extract/extraction` **PASS** — F003 → PRESCRIPTION; readable=True; patient=None (conf 0.99)
- `[03] extract/extraction_self_correction` **INFO** — F003: low confidence on ['diagnosis', 'patient_name'] → re-extracted with gemini-pro-latest; improved ['patient_name', 'doctor_registration', 'diagnosis', 'treatment', 'hospital_name', 'total_amount']
- `[04] extract/extraction` **FLAG** — F004 → UNKNOWN; readable=False; patient=None (conf 0.00)
- `[05] docgate/doc_verification` **FAIL** — We could not read your pharmacy bill (The image is extremely blurry, out of focus, and completely illegible.). Your claim is on hold — please re-upload a clear photo or scan of your pharmacy bill. The rest of your documents are fine.
- `[06] explain/explainer` **FAIL** — Claim stopped before decision: We could not read your pharmacy bill (The image is extremely blurry, out of focus, and completely illegible.). Your claim is on hold — please re-upload a clear photo or scan of your pharmacy bill. The rest of your documents are fine.

</details>

## TC003 — Documents Belong to Different Patients — ✅ MATCH
**Outcome:** BLOCKED — Document F006 is for patient 'Arjun Mehta' while other documents are for 'Rajesh Kumar', but this claim was submitted for member 'Rajesh Kumar'. All documents must belong to the same patient. Please upload documents for 'Rajesh Kumar'.

<details><summary>Full trace</summary>

- `[01] intake/intake` **PASS** — member Rajesh Kumar resolved; submission rules satisfied
- `[02] extract/extraction` **PASS** — F005 → PRESCRIPTION; readable=True; patient='Rajesh Kumar' (conf 0.99)
- `[03] extract/extraction_self_correction` **INFO** — F005: low confidence on ['diagnosis'] → re-extracted with gemini-pro-latest; improved ['doctor_registration', 'diagnosis', 'treatment', 'hospital_name', 'total_amount']
- `[04] extract/extraction` **PASS** — F006 → HOSPITAL_BILL; readable=True; patient='Arjun Mehta' (conf 1.00)
- `[05] docgate/doc_verification` **FAIL** — Document F006 is for patient 'Arjun Mehta' while other documents are for 'Rajesh Kumar', but this claim was submitted for member 'Rajesh Kumar'. All documents must belong to the same patient. Please upload documents for 'Rajesh Kumar'.
- `[06] explain/explainer` **FAIL** — Claim stopped before decision: Document F006 is for patient 'Arjun Mehta' while other documents are for 'Rajesh Kumar', but this claim was submitted for member 'Rajesh Kumar'. All documents must belong to the same patient. Please upload documents for 'Rajesh Kumar'.

</details>

## TC004 — Clean Consultation — Full Approval — ✅ MATCH
**Decision:** APPROVED · approved ₹1350.0 · confidence 0.96
**Message:** Approved ₹1,350.00.

<details><summary>Full trace</summary>

- `[01] intake/intake` **PASS** — member Rajesh Kumar resolved; submission rules satisfied
- `[02] extract/extraction` **PASS** — F007 → PRESCRIPTION; readable=True; patient='Rajesh Kumar' (conf 1.00)
- `[03] extract/extraction` **PASS** — F008 → HOSPITAL_BILL; readable=True; patient='Rajesh Kumar' (conf 1.00)
- `[04] docgate/doc_verification` **PASS** — All required documents present, readable, and belong to the member
- `[05] semantic_map/semantic_map` **PASS** — waiting_condition=None, exclusions=[], conf=0.95
- `[06] adjudicate/supervisor` **INFO** — Adaptive routing: invoked ['waiting_period', 'coverage_exclusion', 'limits', 'fraud_anomaly']; skipped pre_auth (pre_auth not applicable to CONSULTATION (no pre-auth-gated high-value tests configured for this category) — provably PASS, skipped.)
- `[07] adjudicate/waiting_period` **PASS** — Outside all applicable waiting periods (day 214 since joining).
- `[08] adjudicate/coverage_exclusion` **PASS** — Treatment and all line items are covered.
- `[09] adjudicate/limits` **PASS** — Within applicable limits.
- `[10] adjudicate/fraud_anomaly` **PASS** — No fraud signals.
- `[11] financial/financial_calculator` **PASS** — Covered line items total ₹1,500.00 (3/3 items approved) | Co-pay 10% applied on post-discount amount: −₹150.00 | Approved amount: ₹1,350.00
- `[12] decide/decision_aggregator` **INFO** — Approved ₹1,350.00.
- `[13] verify/decision_verifier` **PASS** — judge: PASS (conf 1.00) — All rule verdicts are PASS, the decision status is APPROVED, and the arithmetic for the 10% copay is correct.
- `[14] explain/explainer` **INFO** — final=APPROVED amount=₹1,350.00 confidence=0.96

</details>

## TC005 — Waiting Period — Diabetes — ✅ MATCH
**Decision:** REJECTED · approved ₹0.0 · confidence 0.96
**Message:** 'diabetes' has a 90-day waiting period. Member joined 2024-09-01; treatment on 2024-10-15 is day 44. Eligible for diabetes-related claims from 2024-11-30.

<details><summary>Full trace</summary>

- `[01] intake/intake` **PASS** — member Vikram Joshi resolved; submission rules satisfied
- `[02] extract/extraction` **PASS** — F009 → PRESCRIPTION; readable=True; patient='Vikram Joshi' (conf 1.00)
- `[03] extract/extraction` **PASS** — F010 → HOSPITAL_BILL; readable=True; patient='Vikram Joshi' (conf 1.00)
- `[04] docgate/doc_verification` **PASS** — All required documents present, readable, and belong to the member
- `[05] semantic_map/semantic_map` **PASS** — waiting_condition='diabetes', exclusions=[], conf=0.95
- `[06] adjudicate/supervisor` **INFO** — Adaptive routing: invoked ['waiting_period', 'coverage_exclusion', 'limits', 'fraud_anomaly']; skipped pre_auth (pre_auth not applicable to CONSULTATION (no pre-auth-gated high-value tests configured for this category) — provably PASS, skipped.)
- `[07] adjudicate/waiting_period` **FAIL** — 'diabetes' has a 90-day waiting period. Member joined 2024-09-01; treatment on 2024-10-15 is day 44. Eligible for diabetes-related claims from 2024-11-30.
- `[08] adjudicate/coverage_exclusion` **PASS** — Treatment and all line items are covered.
- `[09] adjudicate/limits` **PASS** — Within applicable limits.
- `[10] adjudicate/fraud_anomaly` **PASS** — No fraud signals.
- `[11] financial/financial_calculator` **PASS** — Covered line items total ₹3,000.00 (1/1 items approved) | Co-pay 10% applied on post-discount amount: −₹300.00 | Approved amount: ₹2,700.00
- `[12] decide/decision_aggregator` **INFO** — 'diabetes' has a 90-day waiting period. Member joined 2024-09-01; treatment on 2024-10-15 is day 44. Eligible for diabetes-related claims from 2024-11-30.
- `[13] verify/decision_verifier` **PASS** — judge: PASS (conf 1.00) — The decision status is REJECTED, which correctly aligns with the FAIL verdict for the waiting period rule, and the approved amount is appropriately zero.
- `[14] explain/explainer` **INFO** — final=REJECTED amount=₹0.00 confidence=0.96

</details>

## TC006 — Dental Partial Approval — Cosmetic Exclusion — ✅ MATCH
**Decision:** PARTIAL · approved ₹8000.0 · confidence 0.96
**Message:** Approved ₹8,000.00. 'Teeth Whitening' (₹4,000) was not approved: Excluded procedure under policy (cosmetic/not covered)

<details><summary>Full trace</summary>

- `[01] intake/intake` **PASS** — member Priya Singh resolved; submission rules satisfied
- `[02] extract/extraction` **PASS** — F011 → HOSPITAL_BILL; readable=True; patient='Priya Singh' (conf 1.00)
- `[03] docgate/doc_verification` **PASS** — All required documents present, readable, and belong to the member
- `[04] semantic_map/semantic_map` **PASS** — waiting_condition=None, exclusions=[], conf=0.95
- `[05] adjudicate/supervisor` **INFO** — Adaptive routing: invoked ['waiting_period', 'coverage_exclusion', 'limits', 'fraud_anomaly']; skipped pre_auth (pre_auth not applicable to DENTAL (no pre-auth-gated high-value tests configured for this category) — provably PASS, skipped.)
- `[06] adjudicate/waiting_period` **PASS** — Outside all applicable waiting periods (day 197 since joining).
- `[07] adjudicate/coverage_exclusion` **PASS** — 'Teeth Whitening' is an excluded procedure for this category.
- `[08] adjudicate/limits` **PASS** — Within applicable limits.
- `[09] adjudicate/fraud_anomaly` **PASS** — No fraud signals.
- `[10] financial/financial_calculator` **PASS** — Covered line items total ₹8,000.00 (1/2 items approved) | Approved amount: ₹8,000.00
- `[11] decide/decision_aggregator` **INFO** — Approved ₹8,000.00. 'Teeth Whitening' (₹4,000) was not approved: Excluded procedure under policy (cosmetic/not covered)
- `[12] verify/decision_verifier` **PASS** — judge: PASS (conf 1.00) — The PARTIAL decision correctly approves the covered line item and denies the excluded line item, which is consistent with all rule verdicts being PASS.
- `[13] explain/explainer` **INFO** — final=PARTIAL amount=₹8,000.00 confidence=0.96

</details>

## TC007 — MRI Without Pre-Authorization — ✅ MATCH
**Decision:** REJECTED · approved ₹0.0 · confidence 1.0
**Message:** MRI above ₹10,000 requires pre-authorization, and none was submitted with this claim. To proceed: obtain pre-authorization from the insurer (valid 30 days) and resubmit the claim with the pre-auth reference number.

<details><summary>Full trace</summary>

- `[01] intake/intake` **PASS** — member Suresh Patil resolved; submission rules satisfied
- `[02] extract/extraction` **PASS** — F012 → PRESCRIPTION; readable=True; patient=None (conf 0.99)
- `[03] extract/extraction_self_correction` **INFO** — F012: low confidence on ['patient_name'] → re-extracted with gemini-pro-latest; improved ['patient_name', 'total_amount']
- `[04] extract/extraction` **PASS** — F013 → LAB_REPORT; readable=True; patient=None (conf 0.95)
- `[05] extract/extraction_self_correction` **INFO** — F013: low confidence on ['patient_name', 'diagnosis'] → re-extracted with gemini-pro-latest; improved ['patient_name', 'doctor_name', 'doctor_registration', 'diagnosis', 'treatment', 'hospital_name', 'document_date', 'total_amount']
- `[06] extract/extraction` **PASS** — F014 → HOSPITAL_BILL; readable=True; patient=None (conf 0.00)
- `[07] extract/extraction_self_correction` **INFO** — F014: low confidence on ['patient_name'] → re-extracted with gemini-pro-latest; improved []
- `[08] docgate/doc_verification` **PASS** — All required documents present, readable, and belong to the member
- `[09] semantic_map/semantic_map` **PASS** — waiting_condition=None, exclusions=[], conf=0.95
- `[10] adjudicate/supervisor` **INFO** — Adaptive routing: all 5 rules applicable — none skippable.
- `[11] adjudicate/waiting_period` **PASS** — Outside all applicable waiting periods (day 215 since joining).
- `[12] adjudicate/coverage_exclusion` **PASS** — Treatment and all line items are covered.
- `[13] adjudicate/pre_auth` **FAIL** — MRI above ₹10,000 requires pre-authorization, and none was submitted with this claim. To proceed: obtain pre-authorization from the insurer (valid 30 days) and resubmit the claim with the pre-auth reference number.
- `[14] adjudicate/limits` **FAIL** — Covered amount ₹15,000 exceeds the DIAGNOSTIC sub-limit of ₹10,000.
- `[15] adjudicate/fraud_anomaly` **PASS** — No fraud signals.
- `[16] financial/financial_calculator` **PASS** — Covered line items total ₹15,000.00 (1/1 items approved) | Capped at DIAGNOSTIC sub-limit ₹10,000 | Approved amount: ₹10,000.00
- `[17] decide/decision_aggregator` **INFO** — MRI above ₹10,000 requires pre-authorization, and none was submitted with this claim. To proceed: obtain pre-authorization from the insurer (valid 30 days) and resubmit the claim with the pre-auth reference number.
- `[18] verify/decision_verifier` **PASS** — judge: PASS (conf 1.00) — The REJECTED status and reason codes correctly reflect the FAIL verdicts for pre-authorization and sub-limits, with an approved amount of 0.0.
- `[19] explain/explainer` **INFO** — final=REJECTED amount=₹0.00 confidence=1.0

</details>

## TC008 — Per-Claim Limit Exceeded — ✅ MATCH
**Decision:** REJECTED · approved ₹0.0 · confidence 0.96
**Message:** Claimed amount ₹7,500 exceeds the per-claim limit of ₹5,000.

<details><summary>Full trace</summary>

- `[01] intake/intake` **PASS** — member Amit Verma resolved; submission rules satisfied
- `[02] extract/extraction` **PASS** — F015 → PRESCRIPTION; readable=True; patient=None (conf 0.00)
- `[03] extract/extraction_self_correction` **INFO** — F015: low confidence on ['patient_name'] → re-extracted with gemini-pro-latest; improved []
- `[04] extract/extraction` **PASS** — F016 → HOSPITAL_BILL; readable=True; patient=None (conf 0.99)
- `[05] extract/extraction_self_correction` **INFO** — F016: low confidence on ['patient_name'] → re-extracted with gemini-pro-latest; improved ['patient_name', 'doctor_name', 'doctor_registration', 'diagnosis', 'treatment']
- `[06] docgate/doc_verification` **PASS** — All required documents present, readable, and belong to the member
- `[07] semantic_map/semantic_map` **PASS** — waiting_condition=None, exclusions=[], conf=0.95
- `[08] adjudicate/supervisor` **INFO** — Adaptive routing: invoked ['waiting_period', 'coverage_exclusion', 'limits', 'fraud_anomaly']; skipped pre_auth (pre_auth not applicable to CONSULTATION (no pre-auth-gated high-value tests configured for this category) — provably PASS, skipped.)
- `[09] adjudicate/waiting_period` **PASS** — Outside all applicable waiting periods (day 202 since joining).
- `[10] adjudicate/coverage_exclusion` **PASS** — Treatment and all line items are covered.
- `[11] adjudicate/limits` **FAIL** — Claimed amount ₹7,500 exceeds the per-claim limit of ₹5,000.
- `[12] adjudicate/fraud_anomaly` **PASS** — No fraud signals.
- `[13] financial/financial_calculator` **PASS** — Covered line items total ₹7,500.00 (2/2 items approved) | Co-pay 10% applied on post-discount amount: −₹750.00 | Approved amount: ₹6,750.00
- `[14] decide/decision_aggregator` **INFO** — Claimed amount ₹7,500 exceeds the per-claim limit of ₹5,000.
- `[15] verify/decision_verifier` **PASS** — judge: PASS (conf 1.00) — The decision status REJECTED and the reason code PER_CLAIM_EXCEEDED are perfectly consistent with the FAIL verdict from the limits rule.
- `[16] explain/explainer` **INFO** — final=REJECTED amount=₹0.00 confidence=0.96

</details>

## TC009 — Fraud Signal — Multiple Same-Day Claims — ✅ MATCH
**Decision:** MANUAL_REVIEW · approved ₹0.0 · confidence 0.95
**Message:** Your claim needs a quick manual check by our team. No action is needed from you right now.

<details><summary>Full trace</summary>

- `[01] intake/intake` **PASS** — member Ravi Menon resolved; submission rules satisfied
- `[02] extract/extraction` **PASS** — F017 → PRESCRIPTION; readable=True; patient=None (conf 0.99)
- `[03] extract/extraction_self_correction` **INFO** — F017: low confidence on ['patient_name'] → re-extracted with gemini-pro-latest; improved ['patient_name', 'doctor_registration', 'treatment', 'hospital_name', 'total_amount']
- `[04] extract/extraction` **PASS** — F018 → HOSPITAL_BILL; readable=True; patient=None (conf 0.99)
- `[05] extract/extraction_self_correction` **INFO** — F018: low confidence on ['patient_name'] → re-extracted with gemini-pro-latest; improved ['patient_name', 'doctor_name', 'doctor_registration', 'diagnosis', 'treatment']
- `[06] docgate/doc_verification` **PASS** — All required documents present, readable, and belong to the member
- `[07] semantic_map/semantic_map` **PASS** — waiting_condition=None, exclusions=[], conf=0.95
- `[08] adjudicate/supervisor` **INFO** — Adaptive routing: invoked ['waiting_period', 'coverage_exclusion', 'limits', 'fraud_anomaly']; skipped pre_auth (pre_auth not applicable to CONSULTATION (no pre-auth-gated high-value tests configured for this category) — provably PASS, skipped.)
- `[09] adjudicate/waiting_period` **PASS** — Outside all applicable waiting periods (day 212 since joining).
- `[10] adjudicate/coverage_exclusion` **PASS** — Treatment and all line items are covered.
- `[11] adjudicate/limits` **PASS** — Within applicable limits.
- `[12] adjudicate/fraud_anomaly` **FLAG** — Unusual pattern detected — routed to manual review. Signals: 4 claims on the same day (2024-10-30) exceeds the limit of 2
- `[13] financial/financial_calculator` **PASS** — Covered line items total ₹4,800.00 (1/1 items approved) | Co-pay 10% applied on post-discount amount: −₹480.00 | Approved amount: ₹4,320.00
- `[14] decide/decision_aggregator` **INFO** — Your claim needs a quick manual check by our team. No action is needed from you right now.
- `[15] verify/decision_verifier` **PASS** — judge: PASS (conf 1.00) — The decision correctly reflects the FLAG verdict from the fraud_anomaly rule by setting the status to MANUAL_REVIEW and the approved amount to 0.0.
- `[16] explain/explainer` **INFO** — final=MANUAL_REVIEW amount=₹0.00 confidence=0.95

</details>

## TC010 — Network Hospital — Discount Applied — ✅ MATCH
**Decision:** APPROVED · approved ₹3240.0 · confidence 0.96
**Message:** Approved ₹3,240.00.

<details><summary>Full trace</summary>

- `[01] intake/intake` **PASS** — member Deepak Shah resolved; submission rules satisfied
- `[02] extract/extraction` **PASS** — F019 → PRESCRIPTION; readable=True; patient='Deepak Shah' (conf 1.00)
- `[03] extract/extraction` **PASS** — F020 → HOSPITAL_BILL; readable=True; patient='Deepak Shah' (conf 1.00)
- `[04] docgate/doc_verification` **PASS** — All required documents present, readable, and belong to the member
- `[05] semantic_map/semantic_map` **PASS** — waiting_condition=None, exclusions=[], conf=0.95
- `[06] adjudicate/supervisor` **INFO** — Adaptive routing: invoked ['waiting_period', 'coverage_exclusion', 'limits', 'fraud_anomaly']; skipped pre_auth (pre_auth not applicable to CONSULTATION (no pre-auth-gated high-value tests configured for this category) — provably PASS, skipped.)
- `[07] adjudicate/waiting_period` **PASS** — Outside all applicable waiting periods (day 216 since joining).
- `[08] adjudicate/coverage_exclusion` **PASS** — Treatment and all line items are covered.
- `[09] adjudicate/limits` **PASS** — Within applicable limits.
- `[10] adjudicate/fraud_anomaly` **PASS** — No fraud signals.
- `[11] financial/financial_calculator` **PASS** — Covered line items total ₹4,500.00 (2/2 items approved) | Network discount 20% applied first: −₹900.00 → ₹3,600.00 | Co-pay 10% applied on post-discount amount: −₹360.00 | Approved amount: ₹3,240.00
- `[12] decide/decision_aggregator` **INFO** — Approved ₹3,240.00.
- `[13] verify/decision_verifier` **PASS** — judge: PASS (conf 1.00) — The decision is internally consistent, with all rule verdicts passing and the arithmetic for the approved amount correctly calculated.
- `[14] explain/explainer` **INFO** — final=APPROVED amount=₹3,240.00 confidence=0.96

</details>

## TC011 — Component Failure — Graceful Degradation — ✅ MATCH
**Decision:** APPROVED · approved ₹4000.0 · confidence 0.736
**Message:** Approved ₹4,000.00.

<details><summary>Full trace</summary>

- `[01] intake/intake` **PASS** — member Kavita Nair resolved; submission rules satisfied
- `[02] extract/extraction` **PASS** — F021 → PRESCRIPTION; readable=True; patient=None (conf 0.99)
- `[03] extract/extraction_self_correction` **INFO** — F021: low confidence on ['patient_name'] → re-extracted with gemini-pro-latest; improved ['patient_name', 'hospital_name', 'total_amount']
- `[04] extract/extraction` **PASS** — F022 → HOSPITAL_BILL; readable=True; patient=None (conf 0.99)
- `[05] extract/extraction_self_correction` **INFO** — F022: low confidence on ['patient_name'] → re-extracted with gemini-pro-latest; improved ['patient_name', 'doctor_name', 'doctor_registration', 'diagnosis', 'treatment']
- `[06] docgate/doc_verification` **PASS** — All required documents present, readable, and belong to the member
- `[07] semantic_map/semantic_map` **PASS** — waiting_condition=None, exclusions=[], conf=0.95
- `[08] adjudicate/supervisor` **INFO** — Adaptive routing: invoked ['waiting_period', 'coverage_exclusion', 'limits', 'fraud_anomaly']; skipped pre_auth (pre_auth not applicable to ALTERNATIVE_MEDICINE (no pre-auth-gated high-value tests configured for this category) — provably PASS, skipped.)
- `[09] adjudicate/waiting_period` **PASS** — Outside all applicable waiting periods (day 210 since joining).
- `[10] adjudicate/coverage_exclusion` **PASS** — Treatment and all line items are covered.
- `[11] adjudicate/limits` **PASS** — Within applicable limits.
- `[12] adjudicate/fraud_anomaly` **ERROR** ⚠ degraded — Simulated component failure — skipped, pipeline continues
- `[13] financial/financial_calculator` **PASS** — Covered line items total ₹4,000.00 (2/2 items approved) | Approved amount: ₹4,000.00
- `[14] decide/decision_aggregator` **INFO** — Approved ₹4,000.00.
- `[15] verify/decision_verifier` **PASS** — judge: PASS (conf 1.00) — The decision is internally consistent, with all line items approved and no FAIL verdicts contradicting the APPROVED status.
- `[16] explain/explainer` **INFO** — final=APPROVED amount=₹4,000.00 confidence=0.736

</details>

## TC012 — Excluded Treatment — ✅ MATCH
**Decision:** REJECTED · approved ₹0.0 · confidence 0.956
**Message:** The treatment falls under policy exclusion(s): Obesity and weight loss programs. These are not covered under PLUM_GHI_2024.

<details><summary>Full trace</summary>

- `[01] intake/intake` **PASS** — member Anita Desai resolved; submission rules satisfied
- `[02] extract/extraction` **PASS** — F023 → PRESCRIPTION; readable=True; patient=None (conf 0.00)
- `[03] extract/extraction_self_correction` **INFO** — F023: low confidence on ['patient_name'] → re-extracted with gemini-pro-latest; improved []
- `[04] extract/extraction` **PASS** — F024 → HOSPITAL_BILL; readable=True; patient=None (conf 0.99)
- `[05] extract/extraction_self_correction` **INFO** — F024: low confidence on ['patient_name'] → re-extracted with gemini-pro-latest; improved ['patient_name', 'doctor_name', 'doctor_registration', 'diagnosis', 'treatment']
- `[06] docgate/doc_verification` **PASS** — All required documents present, readable, and belong to the member
- `[07] semantic_map/semantic_map` **PASS** — waiting_condition='obesity_treatment', exclusions=['Obesity and weight loss programs'], conf=0.95
- `[08] adjudicate/supervisor` **INFO** — Adaptive routing: invoked ['waiting_period', 'coverage_exclusion', 'limits', 'fraud_anomaly']; skipped pre_auth (pre_auth not applicable to CONSULTATION (no pre-auth-gated high-value tests configured for this category) — provably PASS, skipped.)
- `[09] adjudicate/waiting_period` **FAIL** — 'obesity_treatment' has a 365-day waiting period. Member joined 2024-04-01; treatment on 2024-10-18 is day 200. Eligible for obesity_treatment-related claims from 2025-04-01.
- `[10] adjudicate/coverage_exclusion` **FAIL** — The treatment falls under policy exclusion(s): Obesity and weight loss programs. These are not covered under PLUM_GHI_2024.
- `[11] adjudicate/limits` **FAIL** — Claimed amount ₹8,000 exceeds the per-claim limit of ₹5,000.
- `[12] adjudicate/fraud_anomaly` **PASS** — No fraud signals.
- `[13] financial/financial_calculator` **PASS** — Covered line items total ₹8,000.00 (2/2 items approved) | Co-pay 10% applied on post-discount amount: −₹800.00 | Approved amount: ₹7,200.00
- `[14] decide/decision_aggregator` **INFO** — The treatment falls under policy exclusion(s): Obesity and weight loss programs. These are not covered under PLUM_GHI_2024.
- `[15] verify/decision_verifier` **PASS** — judge: PASS (conf 1.00) — The decision status REJECTED and the approved amount of 0.0 are perfectly consistent with the multiple FAIL verdicts (waiting period, coverage exclusion, and limits).
- `[16] explain/explainer` **INFO** — final=REJECTED amount=₹0.00 confidence=0.956

</details>