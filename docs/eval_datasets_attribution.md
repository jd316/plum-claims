# Eval Datasets — Attribution & Licenses

The real-document extraction-robustness eval (`backend/app/evalrunner/extraction_eval.py`,
fetched by `backend/scripts/download_eval_datasets.py`) scores our production vision
extractor on REAL public document images. The raw images are **NOT committed** (they are
downloaded to `backend/eval_datasets/`, which is gitignored); only the harness, the
download script, and the generated report are committed.

**No PHI / no personal health data.** These are public, license-cleared research datasets
of receipts and handwritten medicine words. They contain no real patient health
information from our system or members. They are used solely to measure how well the
extractor reads load-bearing fields (totals, line-item counts, handwritten words) on
real-world document images.

## CORD v2 (primary set)

- **What:** Consolidated Receipt Dataset — real receipt/bill photos with ground-truth
  line items and totals.
- **Source:** Hugging Face `naver-clova-ix/cord-v2` (test split, first ~15 examples).
  https://huggingface.co/datasets/naver-clova-ix/cord-v2
- **License:** CC-BY-4.0. Public, no authentication required.
- **Use here:** Best fit — full bills with labelled totals and menu line items. We compare
  the extractor's `total_amount.value` and line-item count against the labelled
  `total.total_price` and `menu` count.
- **Locale note:** CORD receipts are Indonesian (IDR); `.`/`,` are thousands separators
  (`60.000` = 60000). See the report's CORD note for how this affects total-match scoring.

## RxHandBD (handwriting legibility probe)

- **What:** Handwritten prescription word-image dataset (medicine names).
- **Source:** Zenodo record `18478741` — "RxHandBD: A Handwritten Prescription Word Image
  Dataset". https://zenodo.org/records/18478741
  (`RxHandBD.zip` → `Test_Set/*.jpg` word crops + `Test_Labels.csv` labels.)
- **License:** CC-BY (Creative Commons Attribution).
- **Use here:** Best-effort HANDWRITING legibility probe only — we ask the vision model to
  read each handwritten word crop and compare to its label (case-insensitive, normalized).
