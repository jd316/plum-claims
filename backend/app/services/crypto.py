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
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

log = logging.getLogger("plum.crypto")

_warned_dev_key = False


# Static application salt for the key-stretching KDF. A deterministic key derivation
# needs a fixed salt (we must derive the SAME key every boot to decrypt prior data).
# Its job here is iteration-count stretching to harden a low-entropy key, not per-row
# uniqueness; a high-entropy PHI_ENCRYPTION_KEY needs no stretching but is not weakened.
_KDF_SALT = b"plum-claims-phi-kdf-v1"
_KDF_ITERATIONS = 600_000  # OWASP 2023 PBKDF2-HMAC-SHA256 floor


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a urlsafe-base64 32-byte Fernet key deterministically from a secret,
    via PBKDF2-HMAC-SHA256 (key stretching). Replaces a single-pass SHA-256 so a
    weak/low-entropy key is hardened; a strong key is unaffected. Fernet needs a
    32-byte urlsafe-base64 key."""
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), _KDF_SALT, _KDF_ITERATIONS)
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


# --- raw bytes / file helpers (source-document encryption at rest) ----------

def encrypt_bytes(b: bytes) -> bytes:
    """Encrypt arbitrary bytes (e.g. a scanned bill/PDF) to a Fernet token (bytes)."""
    return _fernet().encrypt(b)


def decrypt_bytes(b: bytes) -> bytes:
    """Decrypt a Fernet token to the original bytes. TOLERANT: if `b` is not a valid
    token for our key (a legacy/plaintext file, e.g. raw JPEG/PDF bytes), it is
    returned unchanged. This is what lets the encryption flag flip safely and keeps
    dev/test (encryption off → plaintext files) reading through the same path."""
    try:
        return _fernet().decrypt(b)
    except InvalidToken:
        return b


def encrypt_file_in_place(path: str) -> None:
    """Encrypt a file on disk in place (read → encrypt → rewrite). Idempotent-safe:
    a file that is ALREADY our token is left unchanged (decrypt succeeds → re-encrypt
    would double-wrap, so we skip)."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        _fernet().decrypt(raw)  # already encrypted by us → skip
        return
    except InvalidToken:
        pass
    tmp = path + ".enc.tmp"
    with open(tmp, "wb") as f:
        f.write(_fernet().encrypt(raw))
    os.replace(tmp, path)


def read_file_decrypted(path: str) -> bytes:
    """Read a document file, transparently decrypting if it was encrypted at rest.
    Plaintext/legacy files pass through unchanged (see decrypt_bytes tolerance)."""
    with open(path, "rb") as f:
        return decrypt_bytes(f.read())
