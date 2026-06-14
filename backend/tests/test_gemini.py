"""Tests for app/services/gemini.py.

Deterministic test: image_part() mime detection (no live API, no mocks).
Live test:          generate_structured() end-to-end with real Gemini API.
"""
from __future__ import annotations

import pytest
from google.genai import types
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Deterministic — pure mime-type detection in image_part()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("suffix,expected_mime", [
    (".png",  "image/png"),
    (".jpg",  "image/jpeg"),
    (".jpeg", "image/jpeg"),
    (".pdf",  "application/pdf"),
])
def test_image_part_mime_detection(tmp_path, suffix, expected_mime):
    """image_part() must assign the correct MIME type based on file extension."""
    from app.services.gemini import image_part

    # Write a few real bytes so the open() call inside image_part succeeds.
    test_file = tmp_path / f"sample{suffix}"
    test_file.write_bytes(b"\x89PNG\r\n\x1a\n" if suffix == ".png" else b"%PDF-1.4" if suffix == ".pdf" else b"\xff\xd8\xff")

    part = image_part(str(test_file))

    assert isinstance(part, types.Part), "image_part() must return a google.genai types.Part"
    assert part.inline_data.mime_type == expected_mime, (
        f"Expected mime {expected_mime!r} for {suffix!r}, "
        f"got {part.inline_data.mime_type!r}"
    )


def test_image_part_png_and_pdf_differ(tmp_path):
    """Sanity check: .png and .pdf parts must have different mime types."""
    from app.services.gemini import image_part

    png_file = tmp_path / "a.png"
    pdf_file = tmp_path / "b.pdf"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    pdf_file.write_bytes(b"%PDF-1.4")

    png_part = image_part(str(png_file))
    pdf_part = image_part(str(pdf_file))

    assert png_part.inline_data.mime_type != pdf_part.inline_data.mime_type


# ---------------------------------------------------------------------------
# Live — generate_structured() against real Gemini API
# ---------------------------------------------------------------------------

class Sum(BaseModel):
    answer: int


@pytest.mark.live
def test_generate_structured_simple_arithmetic():
    """generate_structured() must return a parsed Pydantic model for a trivial prompt."""
    from app.services.gemini import generate_structured

    result = generate_structured(
        prompt_parts=["Return JSON with answer = 2 + 2."],
        schema=Sum,
    )

    assert isinstance(result, Sum), f"Expected Sum instance, got {type(result)}"
    assert result.answer == 4, f"Expected answer=4, got {result.answer}"
