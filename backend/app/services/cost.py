"""Sub-feature A: rough per-claim LLM cost estimation.

estimate_cost_inr maps a model id + token counts to an APPROXIMATE rupee cost
using the per-1M-token rates in app.config (which are themselves estimates).
This is observability sugar — never a billing figure, never used by the decision
pipeline. Defensive: unknown model → pro rates; None tokens → treated as 0.
"""
from app.config import settings


def _rates_for(model: str | None) -> tuple[float, float]:
    """(input_usd_per_1m, output_usd_per_1m) for a model id. Flash vs pro by name;
    default to pro (the pricier tier) so we never under-state cost on an unknown."""
    name = (model or "").lower()
    if "flash" in name:
        return (settings.gemini_flash_input_usd_per_1m,
                settings.gemini_flash_output_usd_per_1m)
    return (settings.gemini_pro_input_usd_per_1m,
            settings.gemini_pro_output_usd_per_1m)


def estimate_cost_inr(model: str | None, input_tokens: int | None,
                      output_tokens: int | None) -> float:
    """Approximate INR cost of one LLM call. Estimate only — see app.config."""
    in_tok = input_tokens or 0
    out_tok = output_tokens or 0
    in_rate, out_rate = _rates_for(model)
    usd = (in_tok / 1_000_000) * in_rate + (out_tok / 1_000_000) * out_rate
    return round(usd * settings.usd_to_inr, 4)
