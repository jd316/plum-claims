"""Exact monetary arithmetic for the financial engine.

Money flows through `rules/financial.py` as `decimal.Decimal` quantized to 2 places
with ROUND_HALF_UP (the standard "round half up to the nearest paisa" rule), and is
converted back to `float` only at the schema/API edge. The DB and JSON layers stay
`float` — a value already rounded to 2 dp is represented exactly by a float for any
realistic claim amount, so the persisted/serialised numbers are unchanged. What this
buys us is removing the *intermediate* float rounding drift that would otherwise
accumulate across the gross → network-discount → co-pay → sub-limit-cap chain at
volume. See architecture.md §10 (the "decimal money" remediation).

Rationale for industry practice: store/compute in exact units, round only at defined
boundaries, always with an explicit rounding mode — never let binary float represent
in-progress monetary totals.
"""
from decimal import Decimal, ROUND_HALF_UP

CENTS = Decimal("0.01")


def D(x) -> Decimal:
    """Coerce to Decimal via str() so a binary float (e.g. 0.1 → 0.1000000000000000055…)
    does not poison the Decimal with base-2 noise. Ints/Decimals pass through cleanly."""
    return x if isinstance(x, Decimal) else Decimal(str(x))


def money(x) -> Decimal:
    """Quantize to 2 dp using ROUND_HALF_UP. The single rounding boundary the engine
    calls at every monetary step."""
    return D(x).quantize(CENTS, rounding=ROUND_HALF_UP)


def to_float(x) -> float:
    """Edge conversion: a quantized Decimal → float for the Pydantic/JSON/DB layer."""
    return float(money(x))
