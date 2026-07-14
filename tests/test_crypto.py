"""
Unit tests for src/kyde/crypto.py — AES-GCM-256 helpers.

Each test redirects KEY_DIR/AES_KEY_PATH to a fresh tmpdir so we never
write to the user's real ~/.agent-ledger/ and tests are fully isolated
from one another.
"""

from __future__ import annotations

import base64

import pytest

from kyde import crypto


@pytest.fixture
def isolated_key(tmp_path, monkeypatch):
    """Point crypto at a tmpdir and reset its internal cache."""
    monkeypatch.setattr(crypto, "KEY_DIR", tmp_path)
    monkeypatch.setattr(crypto, "AES_KEY_PATH", tmp_path / "smtp_aes.key")
    monkeypatch.setattr(crypto, "_key_cache", None)
    return tmp_path


def test_generate_if_missing(isolated_key):
    assert not (isolated_key / "smtp_aes.key").exists()
    key = crypto.ensure_aes_key()
    assert len(key) == 32
    assert (isolated_key / "smtp_aes.key").exists()
    # Subsequent call returns the same material.
    assert crypto.ensure_aes_key() == key


def test_permissions_tightened(isolated_key):
    crypto.ensure_aes_key()
    mode = (isolated_key / "smtp_aes.key").stat().st_mode & 0o777
    assert mode == 0o600


def test_refuses_to_overwrite_existing(isolated_key):
    path = isolated_key / "smtp_aes.key"
    # Existing file that's the wrong length: must raise, not silently replace.
    path.write_bytes(b"short")
    crypto._key_cache = None  # simulate fresh process
    with pytest.raises(ValueError):
        crypto.ensure_aes_key()


def test_roundtrip(isolated_key):
    ct = crypto.encrypt("hello world")
    assert ct != "hello world"
    # Base64 and long enough for nonce(12) + at least tag(16).
    assert len(base64.b64decode(ct)) >= 28
    assert crypto.decrypt(ct) == "hello world"


def test_roundtrip_unicode(isolated_key):
    plaintext = "pässwörd — ✓ — 漢字"
    ct = crypto.encrypt(plaintext)
    assert crypto.decrypt(ct) == plaintext


def test_roundtrip_empty(isolated_key):
    ct = crypto.encrypt("")
    assert crypto.decrypt(ct) == ""


def test_nonce_is_random(isolated_key):
    """Two encryptions of the same plaintext must produce different ciphertexts."""
    a = crypto.encrypt("same-input")
    b = crypto.encrypt("same-input")
    assert a != b
    assert crypto.decrypt(a) == crypto.decrypt(b) == "same-input"


def test_corrupted_ciphertext_raises(isolated_key):
    ct = crypto.encrypt("secret")
    raw = bytearray(base64.b64decode(ct))
    # Flip a byte inside the authenticated payload.
    raw[-1] ^= 0xFF
    tampered = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(Exception):
        crypto.decrypt(tampered)


def test_short_blob_raises(isolated_key):
    with pytest.raises(ValueError):
        crypto.decrypt(base64.b64encode(b"too short").decode())


def test_wrong_key_rejects(isolated_key, monkeypatch):
    """Encrypting with one key then rotating the key file should fail to decrypt."""
    ct = crypto.encrypt("secret")
    # Rotate: force a fresh key by clearing the file and cache.
    (isolated_key / "smtp_aes.key").unlink()
    monkeypatch.setattr(crypto, "_key_cache", None)
    crypto.ensure_aes_key()
    with pytest.raises(Exception):
        crypto.decrypt(ct)
