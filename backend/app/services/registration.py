"""Deterministic validation of Indian medical registration numbers.

The extraction prompt asks the vision model to lower its confidence on a malformed
doctor registration, but a model can still return a malformed (or hallucinated) value
with high confidence. The `sample_documents_guide.md` is explicit that the parser must
"recognize AND validate" these formats, so this module enforces the format
deterministically — independent of model confidence.

Recognised formats (from the guide):
  - State-coded:  <STATE>/<digits>/<year>      e.g. KA/45678/2015, MH/23456/2018
  - Ayurveda:     AYUR/<STATE>/<digits>/<year> e.g. AYUR/KL/2345/2019

We validate STRUCTURE, not a fixed state allow-list: India has ~28 state councils and
the guide lists only a sample, so a two-letter state code is accepted generally (a hard
allow-list would false-reject valid-but-unlisted states). The registration body is 3-6
digits and the year is a plausible 4-digit registration year.
"""

import re

# Two-letter state code / 3-6 digit serial / 4-digit year. The Ayurveda national format
# prefixes a council token (AYUR) before the state code.
_STATE = r"[A-Z]{2}"
_SERIAL = r"\d{3,6}"
_YEAR = r"(?:19|20)\d{2}"
_STATE_CODED = re.compile(rf"^{_STATE}/{_SERIAL}/{_YEAR}$")
_AYURVEDA = re.compile(rf"^AYUR/{_STATE}/{_SERIAL}/{_YEAR}$")

# Confidence a malformed-but-present registration is capped to, so downstream
# explainability reflects that the value did not validate regardless of model certainty.
MALFORMED_CONFIDENCE_CAP = 0.3


def is_valid_registration(value: str | None) -> bool:
    """True iff `value` matches a recognised Indian medical registration format.

    None / empty is treated as 'not present' → not a format error, returns False only
    in the sense that there is nothing to validate; callers should guard on presence
    first (an absent registration is a separate concern from a malformed one)."""
    if not value:
        return False
    v = value.strip().upper()
    return bool(_STATE_CODED.match(v) or _AYURVEDA.match(v))
