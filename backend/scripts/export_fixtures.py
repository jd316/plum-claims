"""Render all 12 test cases to real PNG documents for manual upload during the demo.

Output: backend/storage/fixtures/<CASE_ID>/<FILE_ID>.png

These are the same synthetic-but-real fixtures the eval pipeline renders; exporting
them to disk lets a human (and Claude) drag real documents into the Submit page.
storage/ is gitignored — this script is committed, the generated PNGs are not.

Run from the backend dir:  .venv/bin/python scripts/export_fixtures.py
"""
import os

from app.config import settings
from app.fixtures.loader import load_cases
from app.fixtures.renderer import render_case_documents


def main() -> None:
    out_root = os.path.join(settings.storage_dir, "fixtures")
    cases = load_cases(settings.test_cases_path)
    total_files = 0
    for case in cases:
        case_dir = os.path.join(out_root, case["case_id"])
        paths = render_case_documents(case, case_dir)
        total_files += len(paths)
        print(f"{case['case_id']:<8} {case['case_name']:<34} -> {len(paths)} file(s)")

    print(
        f"\nRendered {total_files} document(s) for {len(cases)} case(s)."
        f"\nOutput dir: {os.path.abspath(out_root)}"
    )


if __name__ == "__main__":
    main()
