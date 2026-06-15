"""Tests for messy-document robustness fixtures.

Deterministic tests (default, no Gemini) verify the messy generators produce valid
images, write readable multi-page PDFs, differ from the clean original, and are
byte-for-byte reproducible.

Live tests (@pytest.mark.live) prove the REAL extractor still reads key fields off
a stamped + phone-photo bill (with lower confidence / populated quality_issues) and
aggregates line items across a real multi-page PDF. Kept to 2 Gemini calls.
"""
import io
import re
import pytest
from PIL import Image

from app.fixtures.loader import load_cases
from app.fixtures.messy import (
    W, H, add_rubber_stamp, phone_photo, multilingual_header,
    to_multipage_pdf, render_multipage_bill_pdf, render_tc004_bill,
    DEVANAGARI_AVAILABLE,
)
from tests.conftest import REPO_ROOT

CASES = {c["case_id"]: c for c in load_cases(str(REPO_ROOT / "test_cases.json"))}


def _clean_bill():
    return render_tc004_bill(CASES["TC004"])


def _pixel_stats(img: Image.Image):
    data = img.convert("L").tobytes()
    return (sum(data) / len(data), min(data), max(data))


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Deterministic tests (no Gemini)                                             #
# --------------------------------------------------------------------------- #

def test_stamp_returns_valid_image_same_size():
    out = add_rubber_stamp(_clean_bill())
    assert isinstance(out, Image.Image) and out.mode == "RGB"
    assert out.size == (W, H)


def test_multilingual_header_same_size():
    out = multilingual_header(_clean_bill())
    assert out.size == (W, H) and out.mode == "RGB"


def test_phone_photo_returns_valid_image():
    out = phone_photo(_clean_bill())
    assert isinstance(out, Image.Image) and out.mode == "RGB"
    # Rotation with expand makes it at least as large as the original.
    assert out.width >= W and out.height >= H


def test_stamp_differs_from_original():
    clean, stamped = _clean_bill(), add_rubber_stamp(_clean_bill())
    assert _pixel_stats(clean) != _pixel_stats(stamped)


def test_phone_photo_reduces_contrast():
    clean, photo = _clean_bill(), phone_photo(_clean_bill())
    # Phone photo darkens / lowers contrast: mean luminance should drop.
    assert _pixel_stats(photo)[0] < _pixel_stats(clean)[0]


def test_multilingual_header_differs_from_original():
    clean, ml = _clean_bill(), multilingual_header(_clean_bill())
    assert _pixel_stats(clean) != _pixel_stats(ml)


@pytest.mark.parametrize("fn", [add_rubber_stamp, phone_photo, multilingual_header])
def test_messy_functions_are_deterministic(fn):
    a = _png_bytes(fn(_clean_bill()))
    b = _png_bytes(fn(_clean_bill()))
    assert a == b, f"{fn.__name__} is not deterministic"


def test_composed_variants_are_deterministic():
    def pipeline():
        return add_rubber_stamp(phone_photo(multilingual_header(_clean_bill())))
    assert _png_bytes(pipeline()) == _png_bytes(pipeline())


def _pdf_page_count(path: str) -> int:
    """Count pages in a PDF by its /Type /Page objects (PIL can write PDFs but
    cannot re-open them as images without an external renderer, so we read the
    file structure directly)."""
    with open(path, "rb") as f:
        data = f.read()
    assert data.startswith(b"%PDF"), "not a PDF file"
    # Match "/Type /Page" but not "/Type /Pages" (the tree node).
    return len(re.findall(rb"/Type\s*/Page(?![s])", data))


def test_to_multipage_pdf_writes_multipage(tmp_path):
    pages = [_clean_bill(), add_rubber_stamp(_clean_bill())]
    path = str(tmp_path / "doc.pdf")
    to_multipage_pdf(pages, path)
    assert _pdf_page_count(path) >= 2


def test_to_multipage_pdf_rejects_empty(tmp_path):
    with pytest.raises(ValueError):
        to_multipage_pdf([], str(tmp_path / "empty.pdf"))


def test_render_multipage_bill_pdf_is_two_pages(tmp_path):
    path = render_multipage_bill_pdf(str(tmp_path / "bill.pdf"))
    assert _pdf_page_count(path) == 2


@pytest.mark.skipif(not DEVANAGARI_AVAILABLE,
                    reason="Noto Sans Devanagari not installed here; multilingual_header "
                           "falls back to transliteration (acceptable — the other multilingual "
                           "render tests still pass). Environment probe, not a correctness check.")
def test_devanagari_font_present():
    # Informational: when the environment ships Noto Sans Devanagari, multilingual
    # rendering uses the real script; otherwise it transliterates and this test skips.
    assert DEVANAGARI_AVAILABLE


# --------------------------------------------------------------------------- #
# Live tests (real Gemini) — kept to 2 calls                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.live
def test_stamped_phone_photo_bill_still_extracts_but_lower_quality(tmp_path):
    from app.models.schemas import DocumentInput
    from app.agents.extraction import extract_document

    # Clean baseline (one call) for confidence comparison.
    clean_path = str(tmp_path / "clean.png")
    _clean_bill().save(clean_path)
    clean = extract_document(DocumentInput(file_id="F008", stored_path=clean_path))

    # Messy: phone photo + rubber stamp over the bill (one call).
    messy_img = add_rubber_stamp(phone_photo(_clean_bill()), text="PAID")
    messy_path = str(tmp_path / "messy.png")
    messy_img.save(messy_path)
    messy = extract_document(DocumentInput(file_id="F008", stored_path=messy_path))

    # Key field still read.
    assert messy.total_amount.value == 1500, f"got {messy.total_amount.value}"
    # Robustness signal: either quality issues are flagged OR confidence dropped.
    lowered = (messy.total_amount.confidence < clean.total_amount.confidence
               or messy.quality.overall_confidence < clean.quality.overall_confidence)
    assert messy.quality.quality_issues or lowered, (
        f"expected quality_issues or lower confidence; "
        f"issues={messy.quality.quality_issues} "
        f"conf={messy.total_amount.confidence} vs clean {clean.total_amount.confidence}")
    print("STAMPED quality_issues:", messy.quality.quality_issues)
    print("STAMPED total conf:", messy.total_amount.confidence,
          "clean total conf:", clean.total_amount.confidence)


@pytest.mark.live
def test_multipage_pdf_aggregates_line_items(tmp_path):
    from app.models.schemas import DocumentInput
    from app.agents.extraction import extract_document

    path = render_multipage_bill_pdf(str(tmp_path / "multi.pdf"))
    r = extract_document(DocumentInput(file_id="MP1", stored_path=path))

    assert r.doc_type == "HOSPITAL_BILL", r.doc_type
    items_sum = sum(li.amount for li in r.line_items)
    print("MULTIPAGE line_items:", [(li.description, li.amount) for li in r.line_items])
    print("MULTIPAGE items_sum:", items_sum, "total:", r.total_amount.value)
    # Items from BOTH pages must be aggregated.
    assert len(r.line_items) >= 3, f"expected >=3 aggregated items, got {len(r.line_items)}"
    assert abs(items_sum - 1500) <= 1, f"line items sum {items_sum} != 1500"
    assert r.total_amount.value is not None and abs(r.total_amount.value - 1500) <= 1
