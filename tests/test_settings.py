"""Tests for the runtime-tunable settings layer (kyde.settings).

Covers the three things every other module trusts this layer to get right:

  * resolution precedence — DB row → env var → hard-coded default — and the
    fail-safe fall-through when a DB/env value is corrupt (a bad override
    must never take the proxy down).
  * the typed `_decode` + the validators (`_in_range`, `_one_of`,
    `_smtp_port_range`, `_cidr_list`) that gate `set_value`.
  * the in-process cache: hits, per-key + whole invalidation, TTL expiry,
    and `get_with_source` bypassing it.

NOTE: conftest's `clean_db` deliberately does NOT truncate the `settings`
table (it doubles as deployment config). So this module isolates itself —
the autouse fixture deletes the keys it touches before and after each test
and clears the cache.
"""

from __future__ import annotations

import pytest

from kyde import ledger, settings

# Keys this module writes to — wiped before/after every test so rows never
# leak across tests (settings table survives clean_db).
_TOUCHED = [
    "DLP_BERT_THRESHOLD",
    "SMTP_PORT",
    "SMTP_ENCRYPTION",
    "SMTP_ENABLED",
    "PUBLIC_PROTOCOL",
    "TRUSTED_PROXY_CIDRS",
    "SMTP_MIN_SCORE",
]


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch):
    for k in _TOUCHED:
        ledger.delete_setting(k)
    settings.invalidate_cache()
    # Make sure no ambient env override bleeds in from the shell.
    for k in _TOUCHED:
        monkeypatch.delenv(k, raising=False)
    yield
    for k in _TOUCHED:
        ledger.delete_setting(k)
    settings.invalidate_cache()


# ---------------------------------------------------------------------------
# _decode — typed coercion
# ---------------------------------------------------------------------------


def test_decode_float():
    spec = settings.SPECS["DLP_BERT_THRESHOLD"]
    assert settings._decode(spec, "0.25") == 0.25


def test_decode_int():
    spec = settings.SPECS["SMTP_PORT"]
    assert settings._decode(spec, "465") == 465


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("On", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("nope", False),
    ],
)
def test_decode_bool(raw, expected):
    spec = settings.SPECS["SMTP_ENABLED"]
    assert settings._decode(spec, raw) is expected


def test_decode_string_passthrough():
    spec = settings.SPECS["PUBLIC_PROTOCOL"]
    assert settings._decode(spec, "https") == "https"


# ---------------------------------------------------------------------------
# Resolution precedence: DB → env → default
# ---------------------------------------------------------------------------


def test_get_returns_default_when_unset():
    assert settings.get("DLP_BERT_THRESHOLD") == 0.5  # spec default


def test_env_overrides_default(monkeypatch):
    monkeypatch.setenv("DLP_BERT_THRESHOLD", "0.33")
    settings.invalidate_cache()
    value, source = settings.get_with_source("DLP_BERT_THRESHOLD")
    assert value == 0.33
    assert source == "env"


def test_db_overrides_env(monkeypatch):
    monkeypatch.setenv("DLP_BERT_THRESHOLD", "0.33")
    ledger.upsert_setting("DLP_BERT_THRESHOLD", "0.91", None)
    settings.invalidate_cache()
    value, source = settings.get_with_source("DLP_BERT_THRESHOLD")
    assert value == 0.91
    assert source == "db"


def test_corrupt_db_value_falls_through_to_env(monkeypatch):
    # A non-float DB row must not crash get() — it falls through to env.
    monkeypatch.setenv("DLP_BERT_THRESHOLD", "0.4")
    ledger.upsert_setting("DLP_BERT_THRESHOLD", "not-a-number", None)
    settings.invalidate_cache()
    value, source = settings.get_with_source("DLP_BERT_THRESHOLD")
    assert value == 0.4
    assert source == "env"


def test_corrupt_db_and_env_falls_through_to_default(monkeypatch):
    monkeypatch.setenv("DLP_BERT_THRESHOLD", "also-bad")
    ledger.upsert_setting("DLP_BERT_THRESHOLD", "still-bad", None)
    settings.invalidate_cache()
    value, source = settings.get_with_source("DLP_BERT_THRESHOLD")
    assert value == 0.5  # default
    assert source == "default"


def test_get_unknown_key_raises():
    with pytest.raises(KeyError, match="unknown setting"):
        settings.get("NOT_A_REAL_SETTING")


def test_get_with_source_unknown_key_raises():
    with pytest.raises(KeyError):
        settings.get_with_source("NOT_A_REAL_SETTING")


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_get_caches_until_invalidated():
    # Prime the cache with the default, then write a DB override. Without
    # invalidation get() must still serve the cached default.
    assert settings.get("DLP_BERT_THRESHOLD") == 0.5
    ledger.upsert_setting("DLP_BERT_THRESHOLD", "0.99", None)
    assert settings.get("DLP_BERT_THRESHOLD") == 0.5  # stale cache hit
    settings.invalidate_cache("DLP_BERT_THRESHOLD")
    assert settings.get("DLP_BERT_THRESHOLD") == 0.99  # fresh read


def test_invalidate_whole_cache():
    assert settings.get("DLP_BERT_THRESHOLD") == 0.5
    ledger.upsert_setting("DLP_BERT_THRESHOLD", "0.77", None)
    settings.invalidate_cache()  # no key → clear everything
    assert settings.get("DLP_BERT_THRESHOLD") == 0.77


def test_cache_ttl_expiry(monkeypatch):
    # Drive time.monotonic forward past the TTL and confirm a re-read.
    clock = {"t": 1000.0}
    monkeypatch.setattr(settings.time, "monotonic", lambda: clock["t"])
    assert settings.get("DLP_BERT_THRESHOLD") == 0.5
    ledger.upsert_setting("DLP_BERT_THRESHOLD", "0.88", None)
    # Still within TTL → cached default.
    clock["t"] = 1000.0 + settings._CACHE_TTL - 0.1
    assert settings.get("DLP_BERT_THRESHOLD") == 0.5
    # Past TTL → fresh read.
    clock["t"] = 1000.0 + settings._CACHE_TTL + 0.1
    assert settings.get("DLP_BERT_THRESHOLD") == 0.88


def test_get_with_source_bypasses_cache():
    # Prime cache with default, write DB override; get_with_source must
    # report the live DB value even though get() would serve the cache.
    settings.get("DLP_BERT_THRESHOLD")  # prime
    ledger.upsert_setting("DLP_BERT_THRESHOLD", "0.66", None)
    value, source = settings.get_with_source("DLP_BERT_THRESHOLD")
    assert value == 0.66 and source == "db"


# ---------------------------------------------------------------------------
# set_value — validate + persist + invalidate
# ---------------------------------------------------------------------------


def test_set_value_persists_and_invalidates():
    settings.get("DLP_BERT_THRESHOLD")  # prime cache with default
    settings.set_value("DLP_BERT_THRESHOLD", "0.42", user_id=None)
    # set_value invalidates, so the next get() reflects the new value.
    assert settings.get("DLP_BERT_THRESHOLD") == 0.42


def test_set_value_rejects_out_of_range():
    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        settings.set_value("DLP_BERT_THRESHOLD", "1.5", user_id=None)
    # Nothing persisted.
    assert ledger.get_setting("DLP_BERT_THRESHOLD") is None


def test_set_value_rejects_bad_enum():
    with pytest.raises(ValueError, match="must be one of"):
        settings.set_value("SMTP_ENCRYPTION", "rot13", user_id=None)


def test_set_value_rejects_bad_port():
    with pytest.raises(ValueError, match="between 1 and 65535"):
        settings.set_value("SMTP_PORT", "70000", user_id=None)


def test_set_value_unknown_key_raises():
    with pytest.raises(KeyError):
        settings.set_value("NOPE", "x", user_id=None)


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


def test_reset_clears_db_override():
    settings.set_value("DLP_BERT_THRESHOLD", "0.42", user_id=None)
    assert settings.get_with_source("DLP_BERT_THRESHOLD")[1] == "db"
    deleted = settings.reset("DLP_BERT_THRESHOLD")
    assert deleted is True
    # Back to default after reset.
    assert settings.get_with_source("DLP_BERT_THRESHOLD") == (0.5, "default")


def test_reset_unknown_key_raises():
    with pytest.raises(KeyError):
        settings.reset("NOPE")


# ---------------------------------------------------------------------------
# Validators (direct)
# ---------------------------------------------------------------------------


def test_in_range_bounds():
    check = settings._in_range(0.0, 1.0)
    check(0.0)
    check(1.0)
    check(0.5)
    with pytest.raises(ValueError):
        check(-0.1)
    with pytest.raises(ValueError):
        check(1.1)


def test_one_of():
    check = settings._one_of("a", "b")
    check("a")
    with pytest.raises(ValueError):
        check("c")


def test_smtp_port_range():
    settings._smtp_port_range(587)
    with pytest.raises(ValueError):
        settings._smtp_port_range(0)
    with pytest.raises(ValueError):
        settings._smtp_port_range(99999)


def test_cidr_list_accepts_valid_and_blank():
    settings._cidr_list("10.0.0.0/8, 192.168.0.0/16")
    settings._cidr_list("")  # blank is allowed
    settings._cidr_list("  ,  ")  # empty tokens skipped


def test_cidr_list_rejects_bad_cidr():
    with pytest.raises(ValueError, match="invalid CIDR"):
        settings._cidr_list("10.0.0.0/8, not-a-cidr")


def test_set_value_validates_cidr_list():
    with pytest.raises(ValueError, match="invalid CIDR"):
        settings.set_value("TRUSTED_PROXY_CIDRS", "999.999.0.0/8", user_id=None)
    # A valid list persists.
    settings.set_value("TRUSTED_PROXY_CIDRS", "172.16.0.0/12", user_id=None)
    assert settings.get_with_source("TRUSTED_PROXY_CIDRS")[1] == "db"


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


def test_list_all_covers_every_spec():
    rows = settings.list_all()
    assert {r["key"] for r in rows} == set(settings.SPECS.keys())
    # Each row carries the effective value + source.
    for r in rows:
        assert "value" in r and r["source"] in ("db", "env", "default")


def test_list_all_reflects_db_override():
    settings.set_value("DLP_BERT_THRESHOLD", "0.42", user_id=None)
    rows = {r["key"]: r for r in settings.list_all()}
    row = rows["DLP_BERT_THRESHOLD"]
    assert row["value"] == 0.42
    assert row["source"] == "db"
    assert row["updated_at"] is not None
