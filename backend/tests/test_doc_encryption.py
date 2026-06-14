"""Source-document at-rest encryption (gated by phi_encryption_enabled). The helpers
are tolerant of plaintext so dev/test/eval (encryption off) read through unchanged."""
import tests.conftest  # noqa: F401 — inserts backend/ on sys.path
from app.services import crypto


def test_bytes_roundtrip():
    raw = b"\x89PNG\r\n fake scan bytes \x00\x01\x02\xff"
    tok = crypto.encrypt_bytes(raw)
    assert tok != raw
    assert crypto.decrypt_bytes(tok) == raw


def test_decrypt_is_tolerant_of_plaintext():
    raw = b"\xff\xd8\xff raw jpeg, not a fernet token"
    assert crypto.decrypt_bytes(raw) == raw  # passthrough, never raises


def test_encrypt_file_in_place_then_read(tmp_path):
    p = tmp_path / "doc.png"
    raw = b"scanned bill bytes " * 200
    p.write_bytes(raw)
    crypto.encrypt_file_in_place(str(p))
    assert p.read_bytes() != raw                       # on disk: ciphertext
    assert crypto.read_file_decrypted(str(p)) == raw   # read back: original
    # Idempotent — encrypting an already-encrypted file is a no-op (no double-wrap).
    crypto.encrypt_file_in_place(str(p))
    assert crypto.read_file_decrypted(str(p)) == raw


def test_read_plaintext_file_passthrough(tmp_path):
    # A legacy/plaintext file (encryption was off when written) reads unchanged.
    p = tmp_path / "plain.png"
    raw = b"\x89PNG plaintext document"
    p.write_bytes(raw)
    assert crypto.read_file_decrypted(str(p)) == raw
