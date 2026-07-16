"""Tests for the authentication primitives (kyde.auth).

`hash_password` is exercised incidentally by conftest's user seeding, but
the security-critical *failure* paths weren't covered anywhere:

  * `verify_password` returning False on a wrong password or a malformed
    hash (it must never raise — a bad stored hash is a False, not a 500).
  * the `validate_password` policy matrix (length + four character classes).
  * `generate_temp_password`'s contract: the output always satisfies
    `validate_password`, and respects the minimum-length floor.
  * `needs_rehash` tolerating a garbage hash.

All pure — no DB, no network.
"""

from __future__ import annotations

import pytest

from kyde import auth

# ---------------------------------------------------------------------------
# hash_password / verify_password
# ---------------------------------------------------------------------------


def test_hash_is_not_plaintext_and_is_salted():
    pw = "CorrectHorse!9Battery"
    h1 = auth.hash_password(pw)
    h2 = auth.hash_password(pw)
    assert pw not in h1
    assert h1.startswith("$argon2id$")
    # Per-user salt → the same password hashes to two different strings.
    assert h1 != h2


def test_verify_accepts_correct_password():
    pw = "CorrectHorse!9Battery"
    assert auth.verify_password(pw, auth.hash_password(pw)) is True


def test_verify_rejects_wrong_password():
    h = auth.hash_password("CorrectHorse!9Battery")
    assert auth.verify_password("wrong-password", h) is False


def test_verify_is_case_sensitive():
    h = auth.hash_password("CaseMatters!9aa")
    assert auth.verify_password("casematters!9aa", h) is False


@pytest.mark.parametrize(
    "bad_hash",
    [
        "",
        "not-a-hash",
        "$argon2id$garbage",
        "plaintext-stored-by-mistake",
    ],
)
def test_verify_returns_false_on_malformed_hash(bad_hash):
    # A corrupt / non-Argon stored hash must read as "no match", never raise.
    assert auth.verify_password("anything", bad_hash) is False


def test_verify_empty_password_against_real_hash():
    h = auth.hash_password("CorrectHorse!9Battery")
    assert auth.verify_password("", h) is False


# ---------------------------------------------------------------------------
# needs_rehash
# ---------------------------------------------------------------------------


def test_needs_rehash_false_for_current_params():
    h = auth.hash_password("CorrectHorse!9Battery")
    assert auth.needs_rehash(h) is False


def test_needs_rehash_false_on_garbage():
    # Must not raise on an unparseable hash — defaults to "no rehash".
    assert auth.needs_rehash("not-a-real-hash") is False


# ---------------------------------------------------------------------------
# validate_password — policy matrix
# ---------------------------------------------------------------------------


def test_validate_accepts_compliant_password():
    assert auth.validate_password("Abcdefgh1!xy") == []


def test_validate_too_short_reports_length():
    errs = auth.validate_password("Ab1!xy")  # 6 chars
    assert any("at least 12" in e for e in errs)


@pytest.mark.parametrize(
    "pw,missing",
    [
        ("abcdefgh1!xy", "uppercase"),  # no uppercase
        ("ABCDEFGH1!XY", "lowercase"),  # no lowercase
        ("Abcdefghij!x", "digit"),  # no digit
        ("Abcdefghij1x", "special"),  # no special
    ],
)
def test_validate_flags_each_missing_class(pw, missing):
    errs = auth.validate_password(pw)
    assert any(missing in e for e in errs)


def test_validate_empty_password_fails_every_rule():
    errs = auth.validate_password("")
    # Length + 4 character-class rules → 5 distinct violations.
    assert len(errs) == 5


def test_validate_accumulates_multiple_violations():
    # Short, all-lowercase letters: fails length, uppercase, digit, special.
    errs = auth.validate_password("abcdef")
    assert len(errs) == 4


# ---------------------------------------------------------------------------
# generate_temp_password
# ---------------------------------------------------------------------------


def test_temp_password_always_satisfies_policy():
    # The whole point of the per-class seeding: every generated password
    # must pass validate_password. Run a batch to shake out the RNG.
    for _ in range(200):
        pw = auth.generate_temp_password()
        assert auth.validate_password(pw) == []


def test_temp_password_honours_requested_length():
    pw = auth.generate_temp_password(24)
    assert len(pw) == 24


def test_temp_password_floors_short_length_to_minimum():
    # A caller asking for fewer than _MIN_LEN chars gets bumped up so the
    # result can still satisfy the policy.
    pw = auth.generate_temp_password(4)
    assert len(pw) == auth._MIN_LEN
    assert auth.validate_password(pw) == []


def test_temp_password_omits_ambiguous_characters():
    # Ambiguous glyphs (0 O 1 l I) are excluded so the password survives
    # being read aloud / hand-copied.
    for _ in range(200):
        pw = auth.generate_temp_password()
        assert not (set(pw) & set("0O1lI"))


def test_temp_passwords_are_unique():
    # Cryptographically random → collisions are vanishingly unlikely.
    seen = {auth.generate_temp_password() for _ in range(100)}
    assert len(seen) == 100
