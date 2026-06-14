# Confidence Calibration Report

Makes the extractor's confidence score statistically meaningful — so that "0.9 ≈ 90% correct". We fit an **isotonic** calibrator on REAL labelled outcomes from the production vision extractor, then measure **Expected Calibration Error (ECE)** before and after.

> **Wired OFF by default.** `settings.confidence_calibration_enabled = False`, so `confidence.compute(...)` returns the raw composite exactly as before and the 12 live cases' confidences / thresholds are unchanged. Enabling it applies the map below to the final composite.

## Dataset

- **Total labelled (confidence, correct) pairs: 31** (26 correct, 5 incorrect).
- TC004 legibility-spectrum variants: **16** (clean + rubber-stamp + phone-photo + multilingual + blur/low-contrast ramps + combinations) on a bill whose ground-truth total is **1500**. `correct = round(total_amount.value) == 1500`.
- CORD v2 receipts (folded in, best-effort): **15** (`correct = total within 2% of labelled total`).

## Calibration quality (ECE, 10 bins)

| metric | value |
|---|---:|
| ECE before calibration | **0.1442** |
| ECE after isotonic fit | **0.0000** |
| absolute improvement | 0.1442 |

Lower ECE = better calibrated. ECE is the count-weighted average gap between each confidence bin's mean confidence and its empirical accuracy.

## Reliability table (pre-calibration)

| confidence bin | mean confidence | accuracy | count |
|---|---:|---:|---:|
| [0.6, 0.7] | 0.700 | 1.000 | 1 |
| [0.8, 0.9] | 0.800 | 1.000 | 1 |
| [0.9, 1.0] | 0.964 | 0.828 | 29 |

## Per-item outcomes

| item | confidence | extracted total | correct |
|---|---:|---:|:---:|
| clean | 1.000 | 1500 | Y |
| stamp_paid | 0.950 | 1500 | Y |
| stamp_original | 0.950 | 1500 | Y |
| phone_photo | 1.000 | 1500 | Y |
| multilingual | 1.000 | 1500 | Y |
| stamp_phone | 0.900 | 1500 | Y |
| multilingual_stamp | 1.000 | 1500 | Y |
| multilingual_phone | 0.990 | 1500 | Y |
| blur_1 | 1.000 | 1500 | Y |
| blur_2 | 1.000 | 1500 | Y |
| blur_3 | 0.900 | 1500 | Y |
| blur_4.5 | 0.700 | 1500 | Y |
| blur_6 | 0.950 | 2200 | N |
| lowcontrast_0.55 | 1.000 | 1500 | Y |
| lowcontrast_0.4 | 1.000 | 1500 | Y |
| worst_case | 0.800 | 1500 | Y |
| cord:cord_000.png | 0.900 | 60 | N |
| cord:cord_001.png | 0.950 | 91000 | Y |
| cord:cord_002.png | 0.900 | 28000 | Y |
| cord:cord_003.png | 0.950 | 11000 | Y |
| cord:cord_004.png | 0.950 | 174600 | Y |
| cord:cord_005.png | 0.980 | 31 | N |
| cord:cord_006.png | 1.000 | 46000 | Y |
| cord:cord_007.png | 0.900 | 111000 | Y |
| cord:cord_008.png | 0.950 | 50000 | Y |
| cord:cord_009.png | 1.000 | 17000 | Y |
| cord:cord_010.png | 0.950 | 230000 | Y |
| cord:cord_011.png | 0.950 | 120000 | Y |
| cord:cord_012.png | 0.950 | 51 | N |
| cord:cord_013.png | 1.000 | 281435 | Y |
| cord:cord_014.png | 1.000 | 63 | N |

## Limitations (honest)

- **Small sample (n = 31).** This is a demonstration fit on a narrow, synthetic legibility spectrum around a single known total, not a production calibration. The isotonic map will overfit at this scale.
- **Coarse labels.** `correct` is binary on the bill total only; it does not capture partial-field correctness or other extracted fields.
- **Production path:** calibrate on **logged real outcomes at volume** (adjudicated claims where the final confidence can be checked against whether the decision held), refit periodically, and monitor ECE drift. Hold out a test split and report ECE on it (here ECE-after is in-sample).
- The calibrator is committed at `backend/calibration_map.json` and stays **inert unless explicitly enabled** in settings.
