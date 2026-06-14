"""Reinforced prompt-injection sanitization for UNTRUSTED extracted free-text.

The system is already proven immune to injection at the decision layer (deterministic
rules, no tools, untrusted-doc prompt). This is defence-in-depth applied to the extracted
diagnosis/treatment strings BEFORE they are interpolated into the semantic_map prompt:
neutralize role markers ("system:", "assistant:"), control phrases ("ignore previous
instructions"), prompt-structure characters (backticks, braces), and cap length.

CRITICAL INVARIANT: this MUST be a no-op on clean medical text. The 12 eval cases carry
clean diagnoses ("Type 2 Diabetes Mellitus", "Obesity", etc.) which contain none of the
neutralized markers, so sanitization leaves them byte-identical and the 12/12 eval is
unchanged. Verified by test_phi.py (clean-text no-op) and the live eval.
"""
from __future__ import annotations

import re

# Cap on a single extracted free-text field. Clean diagnoses/treatments are short
# (a few words); a multi-hundred-char field is itself a red flag. Generous enough to
# never truncate real medical text.
MAX_LEN = 600

# Role / channel markers an injected document might use to impersonate a turn.
# Matched at a word boundary, case-insensitive, only when followed by ':' so ordinary
# prose ("system of care", "user reported") is untouched.
_ROLE_MARKERS = re.compile(
    r"\b(system|assistant|user|developer|tool|function)\s*:",
    re.IGNORECASE,
)

# Classic injection control phrases. Kept tight so clean medical text never matches.
_CONTROL_PHRASES = re.compile(
    r"(ignore\s+(all\s+|the\s+|any\s+)?(previous|prior|above|preceding)\s+(instructions?|prompts?|context)"
    r"|disregard\s+(all\s+|the\s+|any\s+)?(previous|prior|above)\b"
    r"|forget\s+(everything|all\s+previous|the\s+above)"
    r"|new\s+instructions?\s*:"
    r"|you\s+are\s+now\b"
    r"|override\s+(the\s+)?(system|previous|prior)\b)",
    re.IGNORECASE,
)

# Prompt-structure characters: backticks and braces are how our templates delimit /
# format. Neutralize them so an extracted value can't break out of the slot. Angle
# brackets too (pseudo role tags like <system>). Replaced with a space.
_STRUCTURE_CHARS = re.compile(r"[`{}<>]")

# Collapse runs of whitespace (incl. newlines an injection might use to fake turns).
_WS = re.compile(r"\s+")


def sanitize_untrusted_text(s: str | None) -> str | None:
    """Neutralize prompt-injection vectors in an untrusted extracted string.

    Returns None/empty unchanged. On clean medical text this is a no-op (returns an
    equal string). On adversarial text it strips role markers / control phrases /
    structure characters and caps length.
    """
    if not s:
        return s
    out = s
    # Drop newlines/tabs that could fake new turns; collapse whitespace.
    if "\n" in out or "\t" in out or "\r" in out:
        out = _WS.sub(" ", out)
    out = _ROLE_MARKERS.sub("", out)
    out = _CONTROL_PHRASES.sub("", out)
    out = _STRUCTURE_CHARS.sub(" ", out)
    # Re-collapse any whitespace introduced by removals, and trim.
    if out != s:
        out = _WS.sub(" ", out).strip()
    if len(out) > MAX_LEN:
        out = out[:MAX_LEN].rstrip()
    return out
