"""Deterministic "messy variant" generators for the vision pipeline.

These turn a clean rendered document (a PIL image from renderer._render_doc, or any
fresh PIL image) into a realistic messy variant that exercises the extractor's
robustness: rubber stamps over text, phone-photo skew/contrast/shadow, and a
multilingual (Devanagari) header alongside the English fields.

EVERY function is PURE and DETERMINISTIC: same input image -> identical output bytes.
No RNG. This keeps the messy tests reproducible (we assert byte-equality across two
runs) and keeps the existing 12 fixtures + decision pipeline completely untouched —
nothing here is imported by the pipeline; it is opt-in fixture tooling.

It also provides multi-page support: render a hospital bill whose line items are
split across two pages and save the pages as a single real multi-page PDF, so we
can prove the extractor aggregates line items across pages.
"""
from __future__ import annotations
import os
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from app.fixtures.renderer import (
    W, H, _font, _page, _header, _kv, _items, _render_doc,
)

# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

_DEVANAGARI_PATHS = (
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSerifDevanagari-Regular.ttf",
)


def devanagari_font(size: int) -> ImageFont.FreeTypeFont | None:
    """Return a Devanagari-capable font, or None if none is installed."""
    for p in _DEVANAGARI_PATHS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return None


DEVANAGARI_AVAILABLE = devanagari_font(20) is not None


# ---------------------------------------------------------------------------
# Messy variants — each takes a PIL image and returns a NEW PIL image.
# ---------------------------------------------------------------------------

def add_rubber_stamp(img: Image.Image, text: str = "PAID") -> Image.Image:
    """Overlay a semi-transparent rotated rubber stamp (circle + text) partially
    over the document text, mimicking a "PAID"/"ORIGINAL" ink stamp. Deterministic:
    fixed position, angle, colour and alpha."""
    base = img.convert("RGBA")
    # Build the stamp on its own transparent layer, then rotate + composite.
    s = 300
    stamp = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stamp)
    ink = (178, 34, 34, 150)  # firebrick, semi-transparent
    sd.ellipse([10, 10, s - 10, s - 10], outline=ink, width=8)
    sd.ellipse([28, 28, s - 28, s - 28], outline=ink, width=3)
    f = _font(56)
    tb = sd.textbbox((0, 0), text, font=f)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    sd.text(((s - tw) / 2 - tb[0], (s - th) / 2 - tb[1]), text, font=f, fill=ink)
    stamp = stamp.rotate(18, expand=True, resample=Image.Resampling.BICUBIC)
    # Place it over the body text region (deterministic offset).
    pos = (W - stamp.width - 120, 240)
    base.alpha_composite(stamp, dest=pos)
    return base.convert("RGB")


def phone_photo(img: Image.Image) -> Image.Image:
    """Mimic a phone photo of a document: slight rotation/skew, reduced contrast,
    and a soft diagonal shadow gradient. Deterministic — fixed angle and gradient."""
    base = img.convert("RGB")
    # Reduce contrast (phone auto-exposure on white paper).
    base = ImageEnhance.Contrast(base).enhance(0.78)
    base = ImageEnhance.Brightness(base).enhance(1.04)
    # Soft diagonal shadow gradient (darker toward one corner).
    shadow = Image.new("L", base.size, 0)
    sd = ImageDraw.Draw(shadow)
    w, h = base.size
    for x in range(0, w, 4):
        # Linear ramp: brightest top-left, ~45% darkening bottom-right.
        val = int(120 * (x / w))
        sd.line([(x, 0), (x, h)], fill=val)
    shadow = shadow.filter(ImageFilter.GaussianBlur(60))
    dark = Image.new("RGB", base.size, (0, 0, 0))
    base = Image.composite(dark, base, shadow.point(lambda v: int(v * 0.55)))
    # Slight rotation/skew with an off-white fill (paper edge on a desk).
    base = base.rotate(-4, expand=True, resample=Image.Resampling.BICUBIC, fillcolor=(232, 230, 224))
    base = base.filter(ImageFilter.GaussianBlur(0.6))
    return base


def multilingual_header(img: Image.Image) -> Image.Image:
    """Add a Hindi (Devanagari) header/label band alongside the English content.
    The English fields below are left untouched so the extractor still reads them.
    Falls back to a transliterated label if no Devanagari font is installed."""
    base = img.convert("RGB")
    d = ImageDraw.Draw(base)
    band_h = 46
    d.rectangle([0, H - band_h, W, H], fill="#eef3ec")
    dev = devanagari_font(26)
    if dev is not None:
        # "Medical Bill / Receipt" + "Patient" in Hindi.
        d.text((20, H - band_h + 8), "चिकित्सा "
               "बिल / रसीद", font=dev, fill="#1a1a1a")
        d.text((560, H - band_h + 8), "रोगी: ", font=dev, fill="#1a1a1a")
    else:
        f = _font(22)
        d.text((20, H - band_h + 10), "[HI] Chikitsa Bill / Raseed   Rogi:", font=f, fill="#1a1a1a")
    return base


# ---------------------------------------------------------------------------
# Multi-page PDF support
# ---------------------------------------------------------------------------

def to_multipage_pdf(images: list[Image.Image], path: str) -> str:
    """Save a list of page images as a single multi-page PDF. Returns the path."""
    if not images:
        raise ValueError("to_multipage_pdf requires at least one image")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    pages = [im.convert("RGB") for im in images]
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:])
    return path


def _bill_page(name: str, patient: str, date: str, items: list[dict],
               total: float | None, page_label: str) -> Image.Image:
    """Render one page of a hospital bill. total=None omits the total line
    (used for the first page when the total appears only on the last page)."""
    img, d = _page()
    _header(d, name.upper(), f"Bengaluru – 560001  |  BILL / RECEIPT  ({page_label})")
    y = 160
    y = _kv(d, y, "Patient Name", patient)
    y = _kv(d, y, "Date", date)
    y += 16
    if total is None:
        # Items only, no total — reuse the item-table layout sans the total row.
        d.text((40, y), "DESCRIPTION", font=_font(19), fill="#555")
        d.text((680, y), "AMOUNT (Rs.)", font=_font(19), fill="#555"); y += 34
        d.line([40, y, W - 40, y], fill="#bbb", width=1); y += 12
        for it in items:
            d.text((40, y), it["description"], font=_font(24), fill="#111")
            d.text((680, y), f"{it['amount']:.2f}", font=_font(24), fill="#111"); y += 40
        d.text((40, y + 20), "(continued on next page →)", font=_font(19), fill="#666")
    else:
        _items(d, y, items, total)
    return img


def render_multipage_bill_pdf(path: str, *, name: str = "City Clinic, Bengaluru",
                              patient: str = "Rajesh Kumar", date: str = "2024-11-01",
                              page1_items: list[dict] | None = None,
                              page2_items: list[dict] | None = None,
                              total: float = 1500.0) -> str:
    """Render a 2-page hospital bill PDF with line items split across pages.

    Page 1 carries some line items (no total); page 2 carries the rest plus the
    grand total. Used to prove extraction aggregates line items across pages.
    Defaults reproduce the TC004 bill (3 items summing to 1500)."""
    if page1_items is None:
        page1_items = [
            {"description": "Consultation Fee", "amount": 1000},
            {"description": "CBC Test", "amount": 300},
        ]
    if page2_items is None:
        page2_items = [{"description": "Dengue NS1 Test", "amount": 200}]
    p1 = _bill_page(name, patient, date, page1_items, None, "Page 1 of 2")
    p2 = _bill_page(name, patient, date, page2_items, total, "Page 2 of 2")
    return to_multipage_pdf([p1, p2], path)


def render_tc004_bill(case: dict) -> Image.Image:
    """Convenience: render the clean TC004 hospital bill (F008) image via the
    existing renderer, so messy variants can be layered on top of it."""
    doc = next(d for d in case["input"]["documents"] if d["file_id"] == "F008")
    return _render_doc(doc, case["input"])
