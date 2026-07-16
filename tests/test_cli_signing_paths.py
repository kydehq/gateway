"""Tests for the enterprise signing bodies of `kyde keygen` / `kyde key`.

The starter build ships without `kyde.signing`, so tests/test_cli_commands.py
can only pin the early-return messages. Here we install a fake signing module
into sys.modules and flip the `_features.HAS_SIGNING` seam so the real CLI
logic — flag parsing, --force handling, TPM probing, audit logging — runs
against a stub backend. The contract under test is commands.py's control
flow, not the cryptography (that lives in the enterprise repo).

Also covers the `kyde.proxy` __main__ shim and the `_as_list` JSON helper.
"""

from __future__ import annotations

import runpy
import sys
import types
from pathlib import Path

import pytest

from kyde import _features, commands, ledger

# ---------------------------------------------------------------------------
# Fake kyde.signing
# ---------------------------------------------------------------------------


def _make_fake_signing(tmp_path: Path) -> types.ModuleType:
    mod = types.ModuleType("kyde.signing")
    mod.TPM_KEY_PATH = tmp_path / "tpm.key"
    mod.PRIVATE_KEY_PATH = tmp_path / "signing.key"
    mod.PUBLIC_KEY_PATH = tmp_path / "signing.pub"
    mod._probe_tpm = lambda: False
    mod.public_key_fingerprint = lambda: "SHA256:fake-fingerprint"

    def generate_keypair():
        mod.PRIVATE_KEY_PATH.write_text("fake-private")
        mod.PUBLIC_KEY_PATH.write_text(
            "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----\n"
        )
        return mod.PRIVATE_KEY_PATH, mod.PUBLIC_KEY_PATH

    def generate_tpm_keypair():
        mod.TPM_KEY_PATH.write_bytes(b"fake-tpm-blob")
        mod.PUBLIC_KEY_PATH.write_text(
            "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----\n"
        )

    mod.generate_keypair = generate_keypair
    mod.generate_tpm_keypair = generate_tpm_keypair
    return mod


@pytest.fixture
def fake_signing(monkeypatch, tmp_path):
    mod = _make_fake_signing(tmp_path)
    monkeypatch.setitem(sys.modules, "kyde.signing", mod)
    monkeypatch.setattr(_features, "HAS_SIGNING", True)
    return mod


# ---------------------------------------------------------------------------
# keygen — flag parsing
# ---------------------------------------------------------------------------


def test_keygen_rejects_unknown_key_type(fake_signing, capsys):
    commands.run_cli(["keygen", "--type", "hsm9000"])
    out = capsys.readouterr().out
    assert "Unknown key type: hsm9000" in out
    assert "Usage: kyde keygen" in out


def test_keygen_type_flag_without_value_defaults_to_local(fake_signing, capsys):
    commands.run_cli(["keygen", "--type"])
    out = capsys.readouterr().out
    assert "Ed25519 keypair generated" in out


# ---------------------------------------------------------------------------
# keygen — local software key
# ---------------------------------------------------------------------------


def test_keygen_local_generates_and_audits(fake_signing, capsys):
    commands.run_cli(["keygen"])
    out = capsys.readouterr().out
    assert "Ed25519 keypair generated" in out
    assert "SHA256:fake-fingerprint" in out
    assert "Audit entry logged to ledger." in out
    assert fake_signing.PRIVATE_KEY_PATH.exists()

    rows = ledger.list_entries()
    keygen_rows = [r for r in rows if r["agent_id"] == "admin:cli"]
    assert keygen_rows, "keygen should append an audit row"


def test_keygen_local_refuses_overwrite_without_force(fake_signing, capsys):
    fake_signing.PRIVATE_KEY_PATH.write_text("existing")
    commands.run_cli(["keygen"])
    out = capsys.readouterr().out
    assert "Local key already exists" in out
    assert "Use --force to overwrite" in out
    assert fake_signing.PRIVATE_KEY_PATH.read_text() == "existing"


def test_keygen_local_force_overwrites(fake_signing, capsys):
    fake_signing.PRIVATE_KEY_PATH.write_text("existing")
    commands.run_cli(["keygen", "--force"])
    out = capsys.readouterr().out
    assert "Ed25519 keypair generated" in out
    assert fake_signing.PRIVATE_KEY_PATH.read_text() == "fake-private"


def test_keygen_local_audit_failure_is_nonfatal(fake_signing, capsys, monkeypatch):
    def _boom(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(ledger, "append", _boom)
    commands.run_cli(["keygen"])
    out = capsys.readouterr().out
    assert "Ed25519 keypair generated" in out
    assert "Warning: failed to log audit entry" in out


# ---------------------------------------------------------------------------
# keygen — TPM key
# ---------------------------------------------------------------------------


def test_keygen_tpm_unavailable(fake_signing, capsys):
    commands.run_cli(["keygen", "--type", "tpm"])
    out = capsys.readouterr().out
    assert "TPM not available" in out
    assert not fake_signing.TPM_KEY_PATH.exists()


def test_keygen_tpm_generates_and_audits(fake_signing, capsys):
    fake_signing._probe_tpm = lambda: True
    commands.run_cli(["keygen", "--type", "tpm"])
    out = capsys.readouterr().out
    assert "TPM ECDSA P-256 keypair generated" in out
    assert "private key never leaves the device" in out
    assert "Audit entry logged to ledger." in out
    assert fake_signing.TPM_KEY_PATH.exists()


def test_keygen_tpm_refuses_overwrite_without_force(fake_signing, capsys):
    fake_signing.TPM_KEY_PATH.write_bytes(b"existing")
    commands.run_cli(["keygen", "--type", "tpm"])
    out = capsys.readouterr().out
    assert "TPM key already exists" in out
    assert "Use --force to overwrite" in out


def test_keygen_tpm_generation_failure(fake_signing, capsys):
    fake_signing._probe_tpm = lambda: True

    def _boom():
        raise RuntimeError("tpm exploded")

    fake_signing.generate_tpm_keypair = _boom
    commands.run_cli(["keygen", "--type", "tpm"])
    out = capsys.readouterr().out
    assert "Failed to generate TPM key: tpm exploded" in out


def test_keygen_tpm_audit_failure_is_nonfatal(fake_signing, capsys, monkeypatch):
    fake_signing._probe_tpm = lambda: True

    def _boom(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(ledger, "append", _boom)
    commands.run_cli(["keygen", "--type", "tpm"])
    out = capsys.readouterr().out
    assert "TPM ECDSA P-256 keypair generated" in out
    assert "Warning: failed to log audit entry" in out


# ---------------------------------------------------------------------------
# key — status report
# ---------------------------------------------------------------------------


def test_key_info_nothing_provisioned(fake_signing, capsys):
    commands.run_cli(["key"])
    out = capsys.readouterr().out
    assert "TPM available      : ✗ No" in out
    assert out.count("Status              : ✗ Not found") == 2
    assert "No public key found. Run: kyde keygen" in out


def test_key_info_local_key_active(fake_signing, capsys):
    fake_signing.generate_keypair()
    commands.run_cli(["key"])
    out = capsys.readouterr().out
    assert "LOCAL SOFTWARE KEY:" in out
    assert "Active              : ✓ Yes" in out
    assert "SHA256:fake-fingerprint" in out
    assert "BEGIN PUBLIC KEY" in out


def test_key_info_tpm_takes_precedence_over_local(fake_signing, capsys):
    fake_signing.generate_keypair()
    fake_signing.generate_tpm_keypair()
    fake_signing._probe_tpm = lambda: True
    commands.run_cli(["key"])
    out = capsys.readouterr().out
    assert "TPM available      : ✓ Yes" in out
    assert "Active              : ✗ No (TPM key takes precedence)" in out
    assert "Active              : ✓ Yes (TPM is accessible)" in out


def test_key_info_tpm_key_present_but_inaccessible(fake_signing, capsys):
    fake_signing.generate_tpm_keypair()
    commands.run_cli(["key"])
    out = capsys.readouterr().out
    assert "Active              : ✗ No (TPM not accessible)" in out


def test_key_info_fingerprint_unavailable(fake_signing, capsys):
    fake_signing.generate_keypair()

    def _missing():
        raise FileNotFoundError

    fake_signing.public_key_fingerprint = _missing
    commands.run_cli(["key"])
    out = capsys.readouterr().out
    assert "Fingerprint         : (not available)" in out


# ---------------------------------------------------------------------------
# kyde.proxy __main__ shim / _as_list helper
# ---------------------------------------------------------------------------


def test_proxy_module_runs_cli_as_main(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["kyde", "help"])
    runpy.run_module("kyde.proxy", run_name="__main__")
    assert "Commands:" in capsys.readouterr().out


def test_as_list_decodes_all_ledger_column_shapes():
    assert commands._as_list(None) == []
    assert commands._as_list("") == []
    assert commands._as_list('[{"a": 1}]') == [{"a": 1}]
    assert commands._as_list(b'["x"]') == ["x"]
    assert commands._as_list([1, 2]) == [1, 2]
