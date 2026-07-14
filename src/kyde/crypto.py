"""
Symmetric encryption helpers for settings that must not be stored in
plaintext (currently: SMTP_PASSWORD_ENC).

The key is a 32-byte AES-GCM-256 key persisted next to the Ed25519 signing
material in ~/.agent-ledger/ (mounted from the `kyde-store` volume, same
place `signing.py` writes). It is auto-created on first use and NEVER
overwritten — losing the key does not corrupt the ledger, but every
previously-encrypted secret becomes unrecoverable and must be re-entered
by an admin through the UI.

Why AES-GCM-256 and not (e.g.) Fernet: GCM gives us authenticated
encryption with a standard nonce/tag layout and is what the
`cryptography.hazmat.primitives.ciphers.aead.AESGCM` helper already
exposes — no extra deps, no KDF ceremony, no IV reuse traps as long as we
generate a fresh random nonce per encryption (which we do).

Wire format (base64 of a single byte string):

    nonce (12 bytes) || ciphertext || gcm_tag (16 bytes)

AESGCM.encrypt() appends the 16-byte authentication tag internally; the
layout above matches what AESGCM.decrypt() expects when you split at the
nonce boundary.
"""

from __future__ import annotations

import base64
import os
import threading
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Mirror signing.py: the shared volume is mounted at ~/.agent-ledger.
KEY_DIR = Path.home() / ".agent-ledger"
AES_KEY_PATH = KEY_DIR / "smtp_aes.key"

_KEY_LEN = 32  # AES-256
_NONCE_LEN = 12  # GCM standard

_key_cache: bytes | None = None
_key_lock = threading.Lock()


def ensure_aes_key() -> bytes:
    """Return the 32-byte AES-GCM key, generating it once if absent.

    Thread-safe and idempotent. Safe to call from startup hooks on every
    container boot. The first call on a fresh install creates the file;
    subsequent calls just read it.

    Raises ValueError if the file exists but is not exactly 32 bytes —
    we REFUSE to overwrite, since silently generating a fresh key would
    make every previously-encrypted secret unrecoverable.
    """
    global _key_cache
    if _key_cache is not None:
        return _key_cache
    with _key_lock:
        if _key_cache is not None:
            return _key_cache
        if AES_KEY_PATH.exists():
            data = AES_KEY_PATH.read_bytes()
            if len(data) != _KEY_LEN:
                raise ValueError(
                    f"AES key at {AES_KEY_PATH} is {len(data)} bytes, expected {_KEY_LEN}"
                )
            _key_cache = data
            return data
        # First-run: generate. Atomic write via tmp + rename so a crash
        # mid-write can't leave a short/garbled file behind.
        KEY_DIR.mkdir(parents=True, exist_ok=True)
        try:
            KEY_DIR.chmod(0o700)
        except PermissionError:
            pass
        fresh = AESGCM.generate_key(bit_length=256)
        tmp = AES_KEY_PATH.with_suffix(".tmp")
        tmp.write_bytes(fresh)
        tmp.chmod(0o600)
        tmp.rename(AES_KEY_PATH)
        _key_cache = fresh
        return fresh


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 string, return base64 of nonce||ct||tag."""
    aes = AESGCM(ensure_aes_key())
    nonce = os.urandom(_NONCE_LEN)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt(blob_b64: str) -> str:
    """Reverse of encrypt(). Raises on tampering or wrong key."""
    aes = AESGCM(ensure_aes_key())
    blob = base64.b64decode(blob_b64)
    if len(blob) < _NONCE_LEN + 16:
        raise ValueError("ciphertext too short")
    nonce, ct = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    return aes.decrypt(nonce, ct, associated_data=None).decode("utf-8")
