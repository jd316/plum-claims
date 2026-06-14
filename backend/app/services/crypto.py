"""Transparent PHI encryption helpers (Fernet / AES-128-CBC + HMAC).

Additive and defensive. The key is derived from settings.phi_encryption_key when set;
otherwise a dev key is derived from jwt_secret (with a one-time warning) so the helpers
are usable in dev/test without extra config. Encryption is only ENGAGED by persistence
when settings.phi_encryption_enabled is True — these helpers existing changes nothing
on their own.

`decrypt_*` is tolerant of already-plaintext (legacy / unencrypted) values: anything that
is not a valid Fernet token for our key is returned as-is. That tolerance is what makes
flipping the encryption flag on a populated DB safe — old plaintext rows still read.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

log = logging.getLogger("plum.crypto")

_warned_dev_key = False


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a urlsafe-base64 32-byte Fernet key deterministically from a secret.
    Fernet requires a 32-byte urlsafe-base64 key; we SHA-256 the secret to 32 bytes."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=4)
def _fernet_for(key_material: str) -> Fernet:
    return Fernet(_derive_fernet_key(key_material))


def _fernet() -> Fernet:
    """The Fernet instance for the active PHI key. Prefers settings.phi_encryption_key;
    falls back to a dev key derived from jwt_secret with a single clear warning."""
    global _warned_dev_key
    key_material = (getattr(settings, "phi_encryption_key", "") or "").strip()
    if not key_material:
        if not _warned_dev_key:
            log.warning(
                "phi_encryption_key is unset — deriving a DEV PHI key from jwt_secret. "
                "Set PHI_ENCRYPTION_KEY (and a strong JWT_SECRET) in production.")
            _warned_dev_key = True
        key_material = "phi-dev::" + settings.jwt_secret
    return _fernet_for(key_material)


# --- text helpers -----------------------------------------------------------

def encrypt_text(s: str) -> str:
    """Encrypt a string to a Fernet token (str)."""
    return _fernet().encrypt(s.encode("utf-8")).decode("ascii")


def decrypt_text(s: str) -> str:
    """Decrypt a Fernet token. If `s` is not a valid token for our key (legacy
    plaintext, or a value encrypted under a different key), return it unchanged —
    EXCEPT we never silently 'succeed' on a wrong-key token: an InvalidToken means
    either plaintext (return as-is) or wrong key. We cannot distinguish, so we
    return as-is to preserve mixed-row tolerance. Callers that need fail-closed
    semantics use the envelope path in persistence, which only attempts decrypt on
    values it KNOWS were enveloped by us."""
    if not isinstance(s, str):
        return s
    try:
        return _fernet().decrypt(s.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return s


# --- json helpers -----------------------------------------------------------

def encrypt_json(obj) -> str:
    """Serialize `obj` to compact JSON and encrypt it to a Fernet token (str)."""
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    return _fernet().encrypt(raw.encode("utf-8")).decode("ascii")


def decrypt_json(s):
    """Decrypt a Fernet token produced by `encrypt_json` back to the original object.

    Mixed-row tolerance: if `s` is already a plaintext value (a dict/list/None, or a
    string that is not a valid token for our key), it is returned unchanged. This lets
    a reader transparently handle both encrypted and legacy-plaintext rows.
    """
    if not isinstance(s, str):
        return s  # already a decoded JSON object (legacy plaintext row)
    try:
        raw = _fernet().decrypt(s.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # Not our token: could be plaintext JSON text, or a wrong-key token.
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return s
    return json.loads(raw)


def decrypt_json_strict(s):
    """Fail-closed variant used by tests: a value we KNOW is a token must decrypt
    with our key or raise. Returns the object on success, raises InvalidToken on a
    wrong key. (Used to prove wrong-key fails closed.)"""
    raw = _fernet().decrypt(s.encode("utf-8")).decode("utf-8")
    return json.loads(raw)
