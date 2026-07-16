"""Tests for the `kyde` CLI (kyde.commands).

Everything runs against the test Postgres from kyde.testing — the CLI talks
to the same ledger module as the servers. Output is asserted via capsys.

The signing-specific bodies of `keygen`/`key` are enterprise-only; in the
sandbox build (`kyde.signing` absent) both commands take the early-return
path, which is what these tests pin down.
"""

from __future__ import annotations

import pytest

from kyde import commands, ledger
from kyde._features import HAS_SIGNING
from kyde.testing import append_simple, seed_user

sandbox_only = pytest.mark.skipif(
    HAS_SIGNING, reason="sandbox-edition CLI output only"
)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_no_args_prints_help(capsys):
    commands.run_cli([])
    out = capsys.readouterr().out
    assert "Commands:" in out
    assert "ledger verify" in out


def test_help_command_prints_help(capsys):
    commands.run_cli(["help"])
    assert "Commands:" in capsys.readouterr().out


def test_unknown_command_prints_help(capsys):
    commands.run_cli(["frobnicate"])
    out = capsys.readouterr().out
    assert "Unknown command: frobnicate" in out
    assert "Commands:" in out


def test_unknown_ledger_subcommand(capsys):
    commands.run_cli(["ledger", "nope"])
    out = capsys.readouterr().out
    assert "Unknown ledger subcommand: nope" in out


def test_ledger_show_without_id_prints_usage(capsys):
    commands.run_cli(["ledger", "show"])
    assert "Usage: kyde ledger" in capsys.readouterr().out


def test_unknown_admin_subcommand(capsys):
    commands.run_cli(["admin", "nope"])
    assert "Unknown admin subcommand: nope" in capsys.readouterr().out


def test_unknown_backfill_subcommand(capsys):
    commands.run_cli(["backfill", "nope"])
    assert "Unknown backfill subcommand: nope" in capsys.readouterr().out


def test_unknown_dlp_subcommand(capsys):
    commands.run_cli(["dlp", "nope"])
    assert "Unknown dlp subcommand: nope" in capsys.readouterr().out


def test_run_cli_entry_uses_argv(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["kyde", "help"])
    commands.run_cli_entry()
    assert "Commands:" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# keygen / key — sandbox edition paths
# ---------------------------------------------------------------------------


@sandbox_only
def test_keygen_sandbox_message(capsys):
    commands.run_cli(["keygen"])
    out = capsys.readouterr().out
    assert "enterprise feature" in out
    assert "No keys to generate" in out


@sandbox_only
def test_key_info_sandbox_message(capsys):
    commands.run_cli(["key"])
    out = capsys.readouterr().out
    assert "not included in this edition" in out
    assert "hash-chained" in out


# ---------------------------------------------------------------------------
# serve / dashboard — argument parsing (uvicorn stubbed)
# ---------------------------------------------------------------------------


def test_serve_parses_host_and_port(capsys, monkeypatch):
    calls: list[dict] = []
    import uvicorn

    monkeypatch.setattr(
        uvicorn, "run", lambda app, **kw: calls.append(kw)
    )
    commands.run_cli(["serve", "--port", "9123", "--host", "127.0.0.9"])
    assert calls == [{"host": "127.0.0.9", "port": 9123, "log_level": "warning"}]
    assert "9123" in capsys.readouterr().out


def test_dashboard_parses_host_and_port(capsys, monkeypatch):
    calls: list[dict] = []
    import uvicorn

    monkeypatch.setattr(
        uvicorn, "run", lambda app, **kw: calls.append(kw)
    )
    commands.run_cli(["dashboard", "--port", "9345", "--host", "0.0.0.0"])
    assert calls == [{"host": "0.0.0.0", "port": 9345, "log_level": "info"}]
    assert "Audit Dashboard" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# ledger list / verify / show
# ---------------------------------------------------------------------------


def test_ledger_list_empty(capsys):
    commands.run_cli(["ledger", "list"])
    assert "Ledger is empty." in capsys.readouterr().out


def test_ledger_list_shows_entries(capsys):
    append_simple("agent:cli-test")
    append_simple(
        "agent:cli-tools",
        tool_calls=[{"function": "bash", "args": {"cmd": "ls"}}],
    )
    commands.run_cli(["ledger"])  # bare `ledger` defaults to list
    out = capsys.readouterr().out
    assert "agent:cli-test" in out
    assert "bash" in out
    assert "Showing 2 entries" in out


def test_ledger_verify_ok(capsys):
    append_simple()
    commands.run_cli(["ledger", "verify"])
    out = capsys.readouterr().out
    assert "Entries to verify: 1" in out
    assert "Ledger is intact" in out


def test_ledger_verify_broken_chain_exits_nonzero(capsys):
    append_simple()
    append_simple()
    # Corrupt the chain directly — flip the second entry's prev_hash.
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ledger SET prev_hash = repeat('0', 64) WHERE seq = 2"
            )
        conn.commit()
    ledger._reset_verify_cache()
    with pytest.raises(SystemExit) as exc:
        commands.run_cli(["ledger", "verify"])
    assert exc.value.code == 1
    assert "integrity FAILED" in capsys.readouterr().out


def _entry_detail_asserts(out: str, entry) -> None:
    assert "LEDGER ENTRY DETAIL" in out
    assert entry.entry_id in out
    assert "WHY (reasoning context):" in out
    assert "INTEGRITY:" in out


def test_ledger_show_by_seq(capsys):
    entry = append_simple("agent:show-seq")
    commands.run_cli(["ledger", "show", str(entry.seq)])
    out = capsys.readouterr().out
    _entry_detail_asserts(out, entry)
    assert "agent:show-seq" in out
    assert "TOOL CALLS: none" in out


def test_ledger_show_by_uuid_and_prefix(capsys):
    entry = append_simple(
        "agent:show-uuid",
        tool_calls=[{"function": "search", "args": {"q": "x"}}],
    )
    commands.run_cli(["ledger", "show", entry.entry_id])
    out = capsys.readouterr().out
    _entry_detail_asserts(out, entry)
    assert "Function : search" in out

    # Prefix lookup takes the fallback branch.
    commands.run_cli(["ledger", "show", entry.entry_id[:8]])
    _entry_detail_asserts(capsys.readouterr().out, entry)


def test_ledger_show_not_found(capsys):
    commands.run_cli(["ledger", "show", "definitely-not-there"])
    assert "Entry not found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# admin create-admin
# ---------------------------------------------------------------------------


def test_admin_create_requires_both_flags(capsys):
    commands.run_cli(["admin", "create-admin", "--username", "solo"])
    assert "Both --username and --email are required." in capsys.readouterr().out


def test_admin_create_rejects_unknown_flag(capsys):
    commands.run_cli(["admin", "create-admin", "--nope", "x"])
    assert "Unknown arg: --nope" in capsys.readouterr().out


def test_admin_create_creates_user_with_temp_password(capsys):
    commands.run_cli(
        [
            "admin",
            "create-admin",
            "--username",
            "recovery",
            "--email",
            "rec@example.test",
        ]
    )
    out = capsys.readouterr().out
    assert "Admin user created." in out
    assert "Temp password" in out

    user = ledger.get_user_by_username("recovery")
    assert user is not None
    assert "admin" in user["roles"]
    assert user["must_change_password"] is True


def test_admin_create_refuses_existing_username(capsys):
    seed_user("taken", ["viewer"])
    commands.run_cli(
        ["admin", "create-admin", "--username", "taken", "--email", "t@example.test"]
    )
    assert "already exists" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# backfill ua-fields
# ---------------------------------------------------------------------------


def test_backfill_ua_fields_updates_unparsed_rows(capsys):
    entry = append_simple("agent:ua", user_agent="claude-code/1.2.3")
    # request_network rows are written by the server, not ledger.append —
    # seed a historical row with unparsed UA columns directly.
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO request_network (seq, timestamp) VALUES (%s, %s)",
                (entry.seq, entry.timestamp),
            )
        conn.commit()

    commands.run_cli(["backfill", "ua-fields"])
    out = capsys.readouterr().out
    assert "UA backfill complete" in out
    assert "updated 1" in out

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ua_tool FROM request_network")
            rows = cur.fetchall()
    assert rows and rows[0]["ua_tool"] != ""


def test_backfill_ua_fields_noop_when_clean(capsys):
    commands.run_cli(["backfill", "ua-fields"])
    out = capsys.readouterr().out
    assert "scanned 0, updated 0" in out


# ---------------------------------------------------------------------------
# dlp dedupe-alerts
# ---------------------------------------------------------------------------


def _alert(
    session_id: str, findings: list[dict], entry_id: str, scanner: str = "regex"
) -> dict:
    row, _ = ledger.upsert_dlp_alert(
        entry_id=entry_id,
        session_id=session_id,
        scanner=scanner,
        score=0.9,
        findings=findings,
    )
    return row


def _seed_duplicate_session() -> tuple[dict, dict, dict]:
    """One session: original alert, subsumed duplicate, alert with new signal.

    upsert_dlp_alert dedupes identical (scanner, findings) into one open row,
    so the duplicate comes from a DIFFERENT scanner re-detecting the same
    finding — a separate alert row whose signatures are fully subsumed.
    """
    e1 = append_simple("agent:dlp", session_id="sess-dup")
    e2 = append_simple("agent:dlp", session_id="sess-dup")
    e3 = append_simple("agent:dlp", session_id="sess-dup")
    email = {"entity_type": "EMAIL_ADDRESS", "text": "a@x.test"}
    iban = {"entity_type": "IBAN", "text": "DE00 1234"}
    original = _alert("sess-dup", [email], e1.entry_id)
    duplicate = _alert(
        "sess-dup",
        [{"label": "EMAIL_ADDRESS", "text": "a@x.test"}],
        e2.entry_id,
        scanner="bert",
    )
    fresh = _alert("sess-dup", [email, iban], e3.entry_id)
    return original, duplicate, fresh


def _alert_status(alert_id: str) -> tuple[str, str | None]:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, disposition FROM dlp_alerts WHERE alert_id = %s",
                (alert_id,),
            )
            row = cur.fetchone()
    return row["status"], row["disposition"]


def test_dlp_dedupe_dry_run_previews_without_writing(capsys):
    original, duplicate, fresh = _seed_duplicate_session()
    commands.run_cli(["dlp", "dedupe-alerts", "--dry-run"])
    out = capsys.readouterr().out
    assert "would close 1 duplicate alert" in out
    assert "(dry-run; no rows modified)" in out
    assert _alert_status(duplicate["alert_id"])[0] != "closed"


def test_dlp_dedupe_closes_subsumed_alerts(capsys):
    original, duplicate, fresh = _seed_duplicate_session()
    commands.run_cli(["dlp", "dedupe-alerts"])
    out = capsys.readouterr().out
    assert "Closed 1 duplicate alert" in out

    status, disposition = _alert_status(duplicate["alert_id"])
    assert status == "closed"
    assert disposition == "duplicate"
    # The anchor and the alert with new signal stay open.
    assert _alert_status(original["alert_id"])[0] != "closed"
    assert _alert_status(fresh["alert_id"])[0] != "closed"


def test_dlp_dedupe_nothing_to_close(capsys):
    e1 = append_simple("agent:dlp", session_id="sess-solo")
    _alert("sess-solo", [{"entity_type": "PII", "text": "x"}], e1.entry_id)
    commands.run_cli(["dlp", "dedupe-alerts"])
    assert "Nothing to close." in capsys.readouterr().out
