"""PII masking for log RECORDS (never for API responses or the in-app trace, which are
authorized views). A logging.Filter that redacts likely PII from formatted log messages:

  * long digit runs (>=6) — member ids embedded in numbers, phone/policy/aadhaar-like
  * email-like tokens
  * the policy roster member NAMES (loaded once from policy)

Always on (installed at startup regardless of the encryption flag) — logs must never
leak PII. Cheap: precompiled regexes + a single alternation over the (small) name list.
"""
from __future__ import annotations

import json
import logging
import re

_MASK = "***"

# >=6 consecutive digits (member/policy/phone/id numbers). Word-bounded so it doesn't
# eat parts of larger alphanumerics unintentionally at the edges.
_DIGITS = re.compile(r"\b\d{6,}\b")

# Pragmatic email matcher (not RFC-perfect; good enough to redact logged addresses).
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def _load_member_names() -> list[str]:
    """Best-effort load of roster member names from policy. Failure → empty list
    (digit/email masking still applies). Imported lazily so a missing/locked policy
    file can never break logging setup."""
    try:
        from app.config import settings
        from app.services.policy_engine import get_policy_engine
        pe = get_policy_engine(settings.policy_path)
        names = [m.get("name", "") for m in pe.members() if m.get("name")]
        # Longest-first so multi-word names match before any single token.
        return sorted({n for n in names if n.strip()}, key=len, reverse=True)
    except Exception:  # noqa: BLE001 — logging must never fail to initialize
        return []


def _build_name_regex(names: list[str]) -> re.Pattern | None:
    if not names:
        return None
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(rf"\b(?:{alt})\b")


class PiiMaskingFilter(logging.Filter):
    """Redacts member names, long digit runs, and emails from a log record's rendered
    message. Mutates record.msg/args to the masked text so any handler/formatter emits
    the redacted form. A masking error never drops the log line (returns True)."""

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        self._name_re = _build_name_regex(_load_member_names())

    def _mask(self, text: str) -> str:
        text = _EMAIL.sub(_MASK, text)
        text = _DIGITS.sub(_MASK, text)
        if self._name_re is not None:
            text = self._name_re.sub(_MASK, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Render with args first (so masking sees the final interpolated string),
            # then clear args to avoid double-formatting downstream.
            msg = record.getMessage()
            masked = self._mask(msg)
            if masked != msg:
                record.msg = masked
                record.args = None
        except Exception:  # noqa: BLE001 — never lose a log line to a masking bug
            pass
        return True


class JsonLogFormatter(logging.Formatter):
    """One JSON object per log line. `getMessage()` is read AFTER the PII filter has
    masked record.msg, so emitted fields are already redacted. Exceptions are attached
    as a string field, never a raw multi-line traceback that breaks line-delimited JSON."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


def configure_json_logging() -> None:
    """Switch the root logger to line-delimited JSON output. Idempotent. Call once at
    startup when settings.json_logs is True; PII masking is installed separately and
    runs first, so JSON fields are already redacted."""
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for h in root.handlers:
        h.setFormatter(JsonLogFormatter())


def install_pii_masking(*logger_names: str) -> PiiMaskingFilter:
    """Install a single shared PiiMaskingFilter on the named loggers and the root
    logger's handlers. Idempotent per logger. Returns the filter."""
    flt = PiiMaskingFilter()
    targets = list(logger_names) + [""]  # "" == root
    seen_handlers: set[int] = set()
    for name in targets:
        lg = logging.getLogger(name)
        if not any(isinstance(f, PiiMaskingFilter) for f in lg.filters):
            lg.addFilter(flt)
        # Also attach to handlers so records that bypass the logger's own filters
        # (e.g. propagated to root handlers) are still masked at emit time.
        for h in lg.handlers:
            if id(h) in seen_handlers:
                continue
            seen_handlers.add(id(h))
            if not any(isinstance(f, PiiMaskingFilter) for f in h.filters):
                h.addFilter(flt)
    return flt
