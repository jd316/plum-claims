# Decision-Layer Eval Report (synthetic cases, real rules, no Gemini)

Runs synthetic labeled claim scenarios through the REAL deterministic decision logic (5 rule checks + financial calculator + aggregator), composed exactly as the pipeline's decide stage. No Gemini, so the whole suite runs in seconds. PURE-ADDITIVE: the pipeline and the 12 cases are untouched.

- **Cases:** 630
- **Overall decision accuracy:** 100.0% (630/630)
- **Approved/partial amount MAE:** ₹0.0000 (max error ₹0.0000, n=300)
- **Reason-code accuracy on rejects:** 100.0% (n=290)

## Per-template accuracy

| template | n | correct | accuracy |
|---|---:|---:|---:|
| clean_approval | 240 | 240 | 100.0% |
| dental_partial | 60 | 60 | 100.0% |
| excluded_condition | 100 | 100 | 100.0% |
| high_value | 20 | 20 | 100.0% |
| per_claim_exceeded | 30 | 30 | 100.0% |
| pre_auth_missing | 30 | 30 | 100.0% |
| same_day_fraud | 20 | 20 | 100.0% |
| sub_limit_exceeded | 40 | 40 | 100.0% |
| waiting_period | 90 | 90 | 100.0% |

## Confusion matrix (rows = expected, cols = predicted)

| expected \ predicted | APPROVED | PARTIAL | REJECTED | MANUAL_REVIEW |
|---|---:|---:|---:|---:|
| **APPROVED** | 240 | 0 | 0 | 0 |
| **PARTIAL** | 0 | 60 | 0 | 0 |
| **REJECTED** | 0 | 0 | 290 | 0 |
| **MANUAL_REVIEW** | 0 | 0 | 0 | 40 |

## Per-class precision / recall / F1

| class | support | precision | recall | F1 |
|---|---:|---:|---:|---:|
| APPROVED | 240 | 100.0% | 100.0% | 100.0% |
| PARTIAL | 60 | 100.0% | 100.0% | 100.0% |
| REJECTED | 290 | 100.0% | 100.0% | 100.0% |
| MANUAL_REVIEW | 40 | 100.0% | 100.0% | 100.0% |

## Mismatches

None — every case matched its independently-derived expected outcome.
