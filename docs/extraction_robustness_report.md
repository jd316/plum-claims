# Extraction Robustness Report (real public documents)

Runs the production vision extractor (`extract_document`) on REAL public document images and scores how well it reads the load-bearing fields against ground truth. PURE-ADDITIVE: the decision pipeline and the 12 cases are untouched.

- Total-match tolerance: relative error <= 2%

## CORD v2 receipts (totals + line-item count)

Source: `naver-clova-ix/cord-v2` (CC-BY-4.0), test split. Ground truth = labelled total + menu line-item count.

- n receipts: **15**
- Total-match rate: **73.3%**
- Mean relative total error: **0.266**
- Median relative total error: 0.000
- Mean line-item-count error: 0.800
- Exact line-item-count rate: 53.3%
- Mean total-field confidence: 0.959

| image | extracted total | truth total | rel err | match | items (ext/truth) | conf |
|---|---:|---:|---:|:---:|:---:|---:|
| cord_000.png | 60.00 | 60000.00 | 0.999 | N | 3/1 | 0.90 |
| cord_001.png | 91000.00 | 91000.00 | 0.000 | Y | 3/3 | 1.00 |
| cord_002.png | 28000.00 | 28000.00 | 0.000 | Y | 2/1 | 0.90 |
| cord_003.png | 11000.00 | 11000.00 | 0.000 | Y | 1/1 | 0.95 |
| cord_004.png | 174600.00 | 174600.00 | 0.000 | Y | 4/3 | 0.95 |
| cord_005.png | 31.00 | 31000.00 | 0.999 | N | 2/1 | 0.98 |
| cord_006.png | 46000.00 | 46000.00 | 0.000 | Y | 3/3 | 1.00 |
| cord_007.png | 111000.00 | 111000.00 | 0.000 | Y | 3/1 | 0.90 |
| cord_008.png | 50000.00 | 50000.00 | 0.000 | Y | 4/1 | 0.95 |
| cord_009.png | 17000.00 | 17000.00 | 0.000 | Y | 1/1 | 1.00 |
| cord_010.png | 230000.00 | 230000.00 | 0.000 | Y | 1/1 | 0.95 |
| cord_011.png | 120000.00 | 120000.00 | 0.000 | Y | 2/2 | 0.95 |
| cord_012.png | 51.30 | 51300.00 | 0.999 | N | 2/2 | 0.95 |
| cord_013.png | 281435.00 | 281435.00 | 0.000 | Y | 8/6 | 1.00 |
| cord_014.png | 63.00 | 63000.00 | 0.999 | N | 2/2 | 1.00 |

> Note: CORD is Indonesian (IDR), where `.` / `,` are *thousands* separators (e.g. printed `60.000` means 60000). Most misses are this currency-format ambiguity — the extractor read the printed digits faithfully and with high confidence but interpreted the separator as a decimal point (60.000 -> 60.0). Indian (INR) bills, the product's actual domain, do not use this convention, so this is a dataset-locale artefact rather than a reading failure. The median relative error is near zero.

## RxHandBD handwriting legibility probe

Source: RxHandBD handwritten medicine-word crops (Zenodo, CC-BY). Best-effort: asks the vision model to read each crop, compares to the label (case-insensitive, normalized).

- n crops: **10**
- Read accuracy: **70.0%**

| id | label | reading | match |
|---|---|---|:---:|
| rx_000 | nexcital | Nexcital | Y |
| rx_001 | inderen | Inderer | N |
| rx_002 | indever | Indevern | N |
| rx_003 | losita | Losita | Y |
| rx_004 | rivotril | Rivotril | Y |
| rx_005 | asynta | ASynta | Y |
| rx_006 | napa | Napa | Y |
| rx_007 | econate | Econate | Y |
| rx_008 | exeptim | Exephina | N |
| rx_009 | napa | Napa | Y |
