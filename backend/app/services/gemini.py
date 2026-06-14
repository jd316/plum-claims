"""Live Gemini client. Structured output via response_schema; manual retry on invalid output."""
import random, time
from typing import TypeVar, cast
from google import genai
from google.genai import types
from pydantic import BaseModel
from app.config import settings
from app.services.resilience import guarded_call, call_with_model_fallback

# Schema models are pydantic BaseModels; the helpers are generic over the concrete
# model so callers get the exact type back (not a bare BaseModel).
_M = TypeVar("_M", bound=BaseModel)

class GeminiError(Exception): pass

_client: genai.Client | None = None
def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=settings.gemini_timeout_ms),
        )
    return _client

def _usage_dict(resp) -> dict:
    """Sub-feature A: pull token counts off a Gemini response.usage_metadata.
    Defensive — any missing/None field yields None so callers never crash and
    can simply leave TraceEntry tokens unset."""
    um = getattr(resp, "usage_metadata", None)
    if um is None:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    return {
        "input_tokens": getattr(um, "prompt_token_count", None),
        "output_tokens": getattr(um, "candidates_token_count", None),
        "total_tokens": getattr(um, "total_token_count", None),
    }


def _generate_one_model(
        prompt_parts: list, schema: type[_M], model: str | None,
        attempts: int, backoff_base: float) -> tuple[_M, dict]:
    """Single-model structured generation with the existing backoff retry loop.

    The actual network call is routed through `guarded_call` (global concurrency
    semaphore + shared circuit breaker). On success this is transparent; on repeated
    infra failure the breaker opens and the inner call fails fast with CircuitOpenError,
    which (like any other failure here) is rolled up into a GeminiError after retries.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = guarded_call(lambda: client().models.generate_content(
                model=model or settings.gemini_model,
                contents=prompt_parts,
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=schema,
                )))
            usage = _usage_dict(resp)
            if resp.parsed is not None:
                # google-genai types `parsed` broadly; the response_schema guarantees
                # it is an instance of `schema` (an _M) on the happy path.
                return cast(_M, resp.parsed), usage
            return schema.model_validate_json(resp.text or ""), usage
        except Exception as e:          # invalid JSON, API hiccup, 429 → backoff + retry
            last = e
            # Exponential backoff with jitter, only between attempts (not after the last).
            # A 429 needs the request window to roll over before the next try; instant retry
            # would just hit the same window. Note: permanent errors (e.g. a bad API key)
            # still consume all retries — a known, acceptable trade-off here.
            if attempt < attempts - 1:
                time.sleep(backoff_base * 2 ** attempt + random.uniform(0, 0.5))
    raise GeminiError(f"structured generation failed after {attempts} attempts: {last}")


def generate_structured_with_usage(
        prompt_parts: list, schema: type[_M], model: str | None = None,
        attempts: int = 3, backoff_base: float = 1.0,
        fallback_models: list[str] | None = None) -> tuple[_M, dict]:
    """Like generate_structured but ALSO returns a usage dict
    {input_tokens, output_tokens, total_tokens} (values may be None if the API
    omitted usage_metadata). LLM nodes use this to record per-step token cost.

    `fallback_models` (optional): if the primary model fails with an INFRA error after
    its retries, each fallback model is tried once (flash → pro). Callers that omit it
    behave exactly as before — on clean runs the fallback is never invoked."""
    primary = model or settings.gemini_model
    if not fallback_models:
        return _generate_one_model(prompt_parts, schema, primary, attempts, backoff_base)

    # Try primary then each fallback; escalate only on infra failure (validation
    # errors raise immediately — a fallback model would fail the same way).
    return call_with_model_fallback(
        lambda model: _generate_one_model(prompt_parts, schema, model, attempts, backoff_base),
        [primary, *fallback_models])


def generate_structured(prompt_parts: list, schema: type[_M], model: str | None = None,
                        attempts: int = 3, backoff_base: float = 1.0,
                        fallback_models: list[str] | None = None) -> _M:
    """Backwards-compatible wrapper: same behaviour as before, usage discarded."""
    obj, _ = generate_structured_with_usage(prompt_parts, schema, model=model,
                                            attempts=attempts, backoff_base=backoff_base,
                                            fallback_models=fallback_models)
    return obj

def generate_text(prompt: str, model: str | None = None,
                  system_instruction: str | None = None) -> str:
    """Plain text generation (no structured schema). Used by the read-only claim
    chat assistant. Routed through the same guarded_call (concurrency semaphore +
    circuit breaker) as structured generation. temperature=0 for stable, grounded
    answers. Raises GeminiError on repeated failure."""
    try:
        cfg_kwargs: dict = {"temperature": 0}
        if system_instruction:
            cfg_kwargs["system_instruction"] = system_instruction
        resp = guarded_call(lambda: client().models.generate_content(
            model=model or settings.gemini_model,
            contents=[prompt],
            config=types.GenerateContentConfig(**cfg_kwargs)))
        return (resp.text or "").strip()
    except Exception as e:  # noqa: BLE001
        raise GeminiError(f"text generation failed: {e}")


def read_handwritten_word(path: str) -> str | None:
    """Best-effort single-word OCR helper for the handwriting legibility probe
    (eval-only; not on the decision path). Asks the model to read ONE handwritten
    word from a crop and return just that word as plain text. Returns the stripped
    reading, or None if the model returns nothing."""
    prompt = ("This image is a crop of a single HANDWRITTEN word (a medicine name). "
              "Read it and reply with ONLY that one word, in plain text, no "
              "punctuation or explanation. If illegible, reply with your single best guess.")
    resp = client().models.generate_content(
        model=settings.gemini_model,
        contents=[image_part(path), prompt],
        config=types.GenerateContentConfig(temperature=0))
    text = (resp.text or "").strip()
    return text or None


def image_part(path: str) -> types.Part:
    # Decrypt-aware read: an at-rest-encrypted source document is transparently
    # decrypted before going to the vision model; plaintext/legacy files pass through.
    from app.services.crypto import read_file_decrypted
    data = read_file_decrypted(path)
    lower = path.lower()
    if lower.endswith(".pdf"):
        mime = "application/pdf"
    elif lower.endswith((".jpg", ".jpeg")):
        mime = "image/jpeg"
    else:
        mime = "image/png"
    return types.Part.from_bytes(data=data, mime_type=mime)
