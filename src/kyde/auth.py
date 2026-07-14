"""
Password hashing, policy validation, and temporary-password generation.

Argon2id is used server-side with per-user salts. Plaintext passwords cross
the wire over TLS — there is deliberately no client-side hashing (that would
make the stored hash itself the credential). See
docs/sparkling-wibbling-axolotl.md for the rationale.
"""

import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Tuned for an admin dashboard: defaults (64 MiB memory, 3 iterations,
# parallelism 4) give ~200-500 ms on a modern server — slow enough to
# punish brute force, fast enough not to matter for interactive login.
_HASHER = PasswordHasher()

# Rule set per the approved plan. NIST SP 800-63B recommends length-only
# policies; the explicit character-class rules are retained here because the
# user asked for them. Swap out `validate_password` if you want to go
# length-only later.
_MIN_LEN = 12
_SPECIALS = set("!@#$%^&*()-_=+[]{};:,.<>?/\\|`~'\"")

# Temp passwords use a human-friendly alphabet — ambiguous characters
# (0/O, 1/l/I) are omitted so they survive being read aloud or copied by
# hand.
_TEMP_ALPHABET = (
    "ABCDEFGHJKLMNPQRSTUVWXYZ" "abcdefghijkmnpqrstuvwxyz" "23456789" "!@#$%^&*-_+?"
)


def hash_password(plain: str) -> str:
    """Hash a plaintext password with Argon2id (salt + params embedded)."""
    return _HASHER.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verify. Returns False on mismatch or malformed hash."""
    try:
        return _HASHER.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def needs_rehash(hashed: str) -> bool:
    """True if the stored hash uses older Argon2 parameters than _HASHER."""
    try:
        return _HASHER.check_needs_rehash(hashed)
    except Exception:
        return False


def generate_temp_password(length: int = 16) -> str:
    """Return a cryptographically-random temp password.

    Guarantees at least one char from each required class so the generated
    password always satisfies `validate_password`.
    """
    if length < _MIN_LEN:
        length = _MIN_LEN
    picks = [
        secrets.choice(string.ascii_uppercase.replace("O", "").replace("I", "")),
        secrets.choice(string.ascii_lowercase.replace("l", "")),
        secrets.choice("23456789"),
        secrets.choice("!@#$%^&*-_+?"),
    ]
    remaining = length - len(picks)
    picks.extend(secrets.choice(_TEMP_ALPHABET) for _ in range(remaining))
    # Fisher-Yates-ish shuffle using secrets (random.shuffle is not crypto-safe)
    for i in range(len(picks) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        picks[i], picks[j] = picks[j], picks[i]
    return "".join(picks)


def validate_password(plain: str) -> list[str]:
    """Return a list of rule-violation strings. Empty list means the password is acceptable."""
    errors: list[str] = []
    if len(plain) < _MIN_LEN:
        errors.append(f"must be at least {_MIN_LEN} characters")
    if not any(c.isupper() for c in plain):
        errors.append("must contain at least one uppercase letter")
    if not any(c.islower() for c in plain):
        errors.append("must contain at least one lowercase letter")
    if not any(c.isdigit() for c in plain):
        errors.append("must contain at least one digit")
    if not any(c in _SPECIALS for c in plain):
        errors.append("must contain at least one special character")
    return errors
