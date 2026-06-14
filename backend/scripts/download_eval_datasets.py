"""Download a SMALL sample of REAL public document datasets for the extraction
robustness eval. Output goes to backend/eval_datasets/ (gitignored).

This is PURE-ADDITIVE tooling: it never touches the decision pipeline or the 12
cases. The harness in app/evalrunner/extraction_eval.py scores whatever this
script manages to fetch — so this script is deliberately ROBUST: every dataset is
wrapped in try/except, any failure prints a warning and continues, and a summary
of what was fetched is printed at the end. It NEVER crashes the whole run because
one source was unreachable.

Datasets (all license-safe, see docs/eval_datasets_attribution.md):
  - CORD v2 (receipts w/ ground-truth line-items + total) — HF naver-clova-ix/cord-v2,
    CC-BY-4.0, public/no-auth. PRIMARY set: full bills with labelled totals.
  - RxHandBD (handwritten medicine-word crops) — Zenodo, CC-BY. Best-effort
    HANDWRITING legibility probe only.

Run from the backend dir:  .venv/bin/python scripts/download_eval_datasets.py
"""
from __future__ import annotations

import io
import json
import pathlib
import re
import zipfile

# eval_datasets/ lives at backend/eval_datasets (gitignored).
_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
EVAL_ROOT = _BACKEND_DIR / "eval_datasets"

# Keep the live sample small to bound downstream Gemini cost.
CORD_N = 15
RXHANDBD_N = 10


# --------------------------------------------------------------------------- #
# CORD parsing helpers                                                         #
# --------------------------------------------------------------------------- #

def parse_cord_total(total_price: str | None) -> float | None:
    """Parse a CORD `total.total_price` string into a number.

    CORD receipts are Indonesian (IDR): BOTH '.' and ',' are thousands
    separators, so '60.000' == 60000 and '28,000' == 28000. We strip every
    non-digit separator and read the remaining digits as an integer amount.
    Returns None if unparseable."""
    if not total_price:
        return None
    s = str(total_price).strip()
    # Drop currency words/symbols, keep digits and separators.
    s = re.sub(r"[^0-9.,]", "", s)
    if not s:
        return None
    digits = re.sub(r"[.,]", "", s)
    if not digits.isdigit():
        return None
    return float(digits)


def cord_line_item_count(menu) -> int:
    """CORD `menu` is a dict (single item) or a list (multiple items)."""
    if isinstance(menu, list):
        return len(menu)
    if isinstance(menu, dict):
        return 1
    return 0


def cord_ground_truth(ground_truth_str: str) -> dict | None:
    """From a CORD `ground_truth` JSON string, pull total + line-item count.
    Returns None if the parse has no usable total."""
    try:
        gt = json.loads(ground_truth_str)["gt_parse"]
    except (KeyError, json.JSONDecodeError):
        return None
    total = parse_cord_total((gt.get("total") or {}).get("total_price"))
    if total is None:
        return None
    return {
        "total": total,
        "line_item_count": cord_line_item_count(gt.get("menu")),
        "source": "CORD v2 (naver-clova-ix/cord-v2) test split",
    }


# --------------------------------------------------------------------------- #
# CORD download                                                               #
# --------------------------------------------------------------------------- #

def download_cord(n: int = CORD_N) -> int:
    """Pull the first ~n usable CORD test examples. Saves image as
    cord/<id>.png and ground-truth as cord/<id>.json. Returns count fetched."""
    out = EVAL_ROOT / "cord"
    out.mkdir(parents=True, exist_ok=True)
    try:
        from datasets import load_dataset
    except ImportError:
        print("  [CORD] skipped: `datasets` not installed (pip install datasets)")
        return 0

    try:
        ds = load_dataset("naver-clova-ix/cord-v2", split="test", streaming=True)
    except Exception as e:  # network / HF outage / rate limit
        print(f"  [CORD] skipped: unable to load dataset ({type(e).__name__}: {e})")
        return 0

    fetched = 0
    idx = 0
    try:
        for ex in ds:
            if fetched >= n:
                break
            idx += 1
            gt = cord_ground_truth(ex.get("ground_truth", ""))
            if gt is None:
                continue  # skip examples without a usable total
            ex_id = f"cord_{fetched:03d}"
            try:
                img = ex["image"].convert("RGB")
                img.save(out / f"{ex_id}.png")
                (out / f"{ex_id}.json").write_text(json.dumps(gt, indent=2))
                fetched += 1
            except Exception as e:
                print(f"  [CORD] warning: failed to save example {idx} ({e})")
                continue
    except Exception as e:
        print(f"  [CORD] warning: stream interrupted after {fetched} ({type(e).__name__}: {e})")

    print(f"  [CORD] fetched {fetched} receipt(s) -> {out}")
    return fetched


# --------------------------------------------------------------------------- #
# RxHandBD download (best-effort handwriting probe)                            #
# --------------------------------------------------------------------------- #

# Zenodo record for RxHandBD (handwritten prescription word-image dataset). The
# archive contains Test_Set/<file>.jpg word crops plus Test_Labels.csv mapping each
# file to its medicine word. We resolve the .zip via the Zenodo API (so a record
# revision doesn't break us), then extract a few labelled crops. Best-effort only.
_RXHANDBD_ZENODO_RECORD = "18478741"
_RXHANDBD_LABEL_CSVS = ("Test_Labels.csv", "Train_Label.csv")


def _http_get(url: str, timeout: int = 30, tag: str = "RxHandBD") -> bytes | None:
    try:
        import requests
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            return r.content
        print(f"  [{tag}] warning: GET {url} -> HTTP {r.status_code}")
    except Exception as e:
        print(f"  [{tag}] warning: GET {url} failed ({type(e).__name__}: {e})")
    return None


def _parse_label_csv(raw: bytes) -> dict[str, str]:
    """Parse a RxHandBD label CSV ('Images,Text') -> {filename: word}."""
    import csv
    out: dict[str, str] = {}
    text = raw.decode("utf-8", "replace")
    reader = csv.reader(text.splitlines())
    next(reader, None)  # skip header row 'Images,Text'
    for row in reader:
        if len(row) >= 2 and row[0].strip():
            out[row[0].strip()] = row[1].strip()
    return out


def download_rxhandbd(n: int = RXHANDBD_N) -> int:
    """Best-effort: fetch ~n handwritten word-crop images + labels from the RxHandBD
    Zenodo record. Writes rxhandbd/<id>.png and rxhandbd/labels.json. Prints a clear
    'skipped: unavailable' and returns 0 if the record can't be reached."""
    out = EVAL_ROOT / "rxhandbd"
    out.mkdir(parents=True, exist_ok=True)

    meta_raw = _http_get(
        f"https://zenodo.org/api/records/{_RXHANDBD_ZENODO_RECORD}", timeout=60)
    if not meta_raw:
        print("  [RxHandBD] skipped: unavailable (Zenodo record not reachable)")
        return 0
    try:
        files = json.loads(meta_raw).get("files", [])
    except json.JSONDecodeError:
        print("  [RxHandBD] skipped: unavailable (record metadata unparseable)")
        return 0
    # Prefer the labelled word-crop archive (RxHandBD.zip), else any zip.
    zips = [(f.get("key", ""), f.get("links", {}).get("self"))
            for f in files if str(f.get("key", "")).lower().endswith(".zip")]
    zips.sort(key=lambda kv: 0 if "rxhandbd" in kv[0].lower() else 1)
    zip_url = next((url for _, url in zips if url), None)
    if not zip_url:
        print("  [RxHandBD] skipped: unavailable (no .zip in record)")
        return 0

    archive = _http_get(zip_url, timeout=180)
    if not archive:
        print("  [RxHandBD] skipped: unavailable (archive download failed)")
        return 0

    labels_out: dict[str, str] = {}
    fetched = 0
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            names = zf.namelist()
            # Build filename -> word from the label CSV(s).
            file_to_word: dict[str, str] = {}
            for member in names:
                base = pathlib.PurePosixPath(member).name
                if base in _RXHANDBD_LABEL_CSVS:
                    file_to_word.update(_parse_label_csv(zf.read(member)))
            imgs = [m for m in names
                    if m.lower().endswith((".png", ".jpg", ".jpeg"))]
            for member in imgs:
                if fetched >= n:
                    break
                base = pathlib.PurePosixPath(member).name
                word = file_to_word.get(base)
                if not word:
                    continue  # only keep labelled crops
                try:
                    data = zf.read(member)
                    from PIL import Image
                    Image.open(io.BytesIO(data)).verify()
                    ex_id = f"rx_{fetched:03d}"
                    (out / f"{ex_id}.png").write_bytes(data)
                    labels_out[ex_id] = word.lower()
                    fetched += 1
                except Exception:
                    continue
    except zipfile.BadZipFile:
        print("  [RxHandBD] skipped: unavailable (downloaded archive not a valid zip)")
        return 0

    if labels_out:
        (out / "labels.json").write_text(json.dumps(labels_out, indent=2))
    print(f"  [RxHandBD] fetched {fetched} labelled word-crop(s) -> {out}")
    return fetched


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #

def main() -> None:
    EVAL_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"Downloading eval datasets -> {EVAL_ROOT}")
    summary: dict[str, int] = {}

    for name, fn in (("CORD", download_cord),
                     ("RxHandBD", download_rxhandbd)):
        try:
            summary[name] = fn()
        except Exception as e:  # absolute backstop — never crash the whole run
            print(f"  [{name}] warning: unexpected error ({type(e).__name__}: {e})")
            summary[name] = 0

    print("\nSummary:")
    for name, count in summary.items():
        state = f"{count} item(s)" if count else "skipped/unavailable"
        print(f"  {name:10s}: {state}")


if __name__ == "__main__":
    main()
