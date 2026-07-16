"""
CLI commands: keygen, key, serve, ledger list/verify/show
"""

import json
import sys
from datetime import datetime


def run_cli_entry():
    """Console-script entry point for `kyde`."""
    run_cli(sys.argv[1:])


def _as_list(value) -> list:
    """Decode a ledger JSON column that may arrive either way.

    jsonb columns come back from psycopg already decoded (list/dict);
    legacy TEXT rows and defaults arrive as JSON strings.
    """
    if value is None:
        return []
    if isinstance(value, (bytes, str)):
        return json.loads(value or "[]")
    return value


def run_cli(args: list[str]):
    if not args or args[0] == "help":
        _print_help()
        return

    cmd = args[0]

    if cmd == "keygen":
        _cmd_keygen(args[1:])

    elif cmd == "key":
        _cmd_key_info()

    elif cmd == "serve":
        _cmd_serve(args[1:])

    elif cmd == "dashboard":
        _cmd_dashboard(args[1:])

    elif cmd == "ledger":
        sub = args[1] if len(args) > 1 else "list"
        if sub == "list":
            _cmd_ledger_list()
        elif sub == "verify":
            _cmd_ledger_verify()
        elif sub == "show" and len(args) > 2:
            _cmd_ledger_show(args[2])
        else:
            print(f"Unknown ledger subcommand: {sub}")
            print("Usage: kyde ledger [list|verify|show <id>]")

    elif cmd == "admin":
        sub = args[1] if len(args) > 1 else ""
        if sub == "create-admin":
            _cmd_admin_create(args[2:])
        else:
            print(f"Unknown admin subcommand: {sub}")
            print("Usage: kyde admin create-admin --username X --email Y")

    elif cmd == "backfill":
        sub = args[1] if len(args) > 1 else ""
        if sub == "ua-fields":
            _cmd_backfill_ua_fields()
        else:
            print(f"Unknown backfill subcommand: {sub}")
            print("Usage: kyde backfill ua-fields")

    elif cmd == "dlp":
        sub = args[1] if len(args) > 1 else ""
        if sub == "dedupe-alerts":
            _cmd_dlp_dedupe(args[2:])
        else:
            print(f"Unknown dlp subcommand: {sub}")
            print("Usage: kyde dlp dedupe-alerts [--dry-run]")

    else:
        print(f"Unknown command: {cmd}")
        _print_help()


# ---------------------------------------------------------------------------


def _cmd_keygen(args: list[str] = None):
    from . import _features

    if not _features.HAS_SIGNING:
        print(
            "Audit signing is an enterprise feature and is not included in this edition.\n"
            "The ledger runs unsigned (hash-chained, tamper-evident). No keys to generate."
        )
        return
    from .signing import (
        generate_keypair,
        generate_tpm_keypair,
        public_key_fingerprint,
        TPM_KEY_PATH,
        PRIVATE_KEY_PATH,
        _probe_tpm,
    )

    if args is None:
        args = []

    # Parse --type flag
    key_type = "local"  # default
    if "--type" in args:
        idx = args.index("--type")
        if idx + 1 < len(args):
            key_type = args[idx + 1].lower()

    # Parse --force flag
    force = "--force" in args

    if key_type not in ("local", "tpm"):
        print(f"✗ Unknown key type: {key_type}")
        print("Usage: kyde keygen [--type local|tpm] [--force]")
        return

    # Check for existing keys and warn if not forced
    if key_type == "tpm":
        if TPM_KEY_PATH.exists() and not force:
            print(f"✗ TPM key already exists at {TPM_KEY_PATH}")
            print("   Use --force to overwrite")
            return
    else:  # local
        if PRIVATE_KEY_PATH.exists() and not force:
            print(f"✗ Local key already exists at {PRIVATE_KEY_PATH}")
            print("   Use --force to overwrite")
            return

    # Generate TPM key
    if key_type == "tpm":
        if not _probe_tpm():
            print(
                "✗ TPM not available. Install tpm2-pytss and ensure TPM device is accessible."
            )
            print("   pip install kyde-gateway[tpm]")
            return

        try:
            generate_tpm_keypair()
            fingerprint = public_key_fingerprint()
            print("✓ TPM ECDSA P-256 keypair generated")
            print(f"  TPM key blob : {str(TPM_KEY_PATH)}")
            print("  Public key   : ~/.agent-ledger/signing.pub")
            print(f"  Fingerprint  : {fingerprint}")
            print("  Algorithm    : ECDSA P-256 / SHA-256")
            print()
            print("Key is stored in TPM — private key never leaves the device.")
            # ITIL Phase 1: audit log the keygen operation
            try:
                from . import ledger as _ledger

                _ledger.append(
                    agent_id="admin:cli",
                    action_type="admin",
                    model="N/A",
                    request_body={
                        "operation": "keygen",
                        "key_type": key_type,
                        "force": force,
                    },
                    response_body={"fingerprint": fingerprint, "status": "success"},
                    why_messages=[],
                    tool_calls=[],
                    client_ip="cli",
                    session_id="admin",
                    upstream="",
                )
                print("  Audit entry logged to ledger.")
            except Exception as e:
                print(f"  Warning: failed to log audit entry: {e}")
        except Exception as e:
            print(f"✗ Failed to generate TPM key: {e}")
            return

    # Generate software Ed25519 key
    else:
        priv_path, pub_path = generate_keypair()
        fingerprint = public_key_fingerprint()
        print("✓ Ed25519 keypair generated")
        print(f"  Private key : {priv_path}")
        print(f"  Public key  : {pub_path}")
        print(f"  Fingerprint : {fingerprint}")
        print("  Algorithm   : Ed25519")
        print()
        print("Keep the private key safe — it is the root of trust for your ledger.")
        # ITIL Phase 1: audit log the keygen operation
        try:
            from . import ledger as _ledger

            _ledger.append(
                agent_id="admin:cli",
                action_type="admin",
                model="N/A",
                request_body={
                    "operation": "keygen",
                    "key_type": key_type,
                    "force": force,
                },
                response_body={"fingerprint": fingerprint, "status": "success"},
                why_messages=[],
                tool_calls=[],
                client_ip="cli",
                session_id="admin",
                upstream="",
            )
            print("  Audit entry logged to ledger.")
        except Exception as e:
            print(f"  Warning: failed to log audit entry: {e}")


def _cmd_key_info():
    from . import _features

    if not _features.HAS_SIGNING:
        print("Audit signing is not included in this edition (sandbox).")
        print("The ledger is hash-chained and tamper-evident, but unsigned.")
        return
    from .signing import (
        PUBLIC_KEY_PATH,
        TPM_KEY_PATH,
        PRIVATE_KEY_PATH,
        public_key_fingerprint,
        _probe_tpm,
    )

    print("=" * 60)
    print("KEY CONFIGURATION")
    print("=" * 60)
    print()

    # TPM status
    print("TPM STATUS:")
    tpm_accessible = _probe_tpm()
    if tpm_accessible:
        print("  TPM available      : ✓ Yes (accessible)")
    else:
        print("  TPM available      : ✗ No (not accessible or not installed)")
    print()

    # Show local software key
    print("LOCAL SOFTWARE KEY:")
    if PRIVATE_KEY_PATH.exists():
        print("  Status              : ✓ Present")
        print(f"  Private key file    : {str(PRIVATE_KEY_PATH)}")
        print("  Algorithm           : Ed25519")
        if TPM_KEY_PATH.exists():
            print("  Active              : ✗ No (TPM key takes precedence)")
        else:
            print("  Active              : ✓ Yes")
    else:
        print("  Status              : ✗ Not found")
    print()

    # Show TPM key
    print("TPM KEY:")
    if TPM_KEY_PATH.exists():
        print("  Status              : ✓ Present")
        print(f"  TPM key blob        : {str(TPM_KEY_PATH)}")
        print("  Algorithm           : ECDSA P-256 / SHA-256")
        if tpm_accessible:
            print("  Active              : ✓ Yes (TPM is accessible)")
        else:
            print("  Active              : ✗ No (TPM not accessible)")
    else:
        print("  Status              : ✗ Not found")
    print()

    # Show active public key
    if PUBLIC_KEY_PATH.exists():
        print("ACTIVE PUBLIC KEY:")
        try:
            fingerprint = public_key_fingerprint()
            print(f"  Fingerprint         : {fingerprint}")
        except FileNotFoundError:
            print("  Fingerprint         : (not available)")

        try:
            pub_pem = PUBLIC_KEY_PATH.read_text()
            print()
            print("PUBLIC KEY (PEM):")
            print(pub_pem)
        except FileNotFoundError:
            print("✗ Public key file not found")
    else:
        print("✗ No public key found. Run: kyde keygen")


def _cmd_dashboard(args: list[str]):
    import uvicorn
    from .dashboard import app as dashboard_app

    host = "0.0.0.0"
    port = 8501

    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        if a == "--host" and i + 1 < len(args):
            host = args[i + 1]

    print(f"Starting Audit Dashboard on http://localhost:{port}")
    uvicorn.run(dashboard_app, host=host, port=port, log_level="info")


def _cmd_serve(args: list[str]):
    import uvicorn
    from .server import app

    host = "0.0.0.0"
    port = 8000

    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        if a == "--host" and i + 1 < len(args):
            host = args[i + 1]

    print(f"Starting Agent Behavioral Ledger Proxy on {host}:{port}")
    print(f"Point your agent at: OPENAI_BASE_URL=http://localhost:{port}/v1")
    print()
    uvicorn.run(app, host=host, port=port, log_level="warning")


def _cmd_ledger_list():
    from .ledger import list_entries

    entries = list_entries(limit=50)
    if not entries:
        print("Ledger is empty.")
        return

    print(
        f"{'SEQ':<5} {'TIME':<20} {'AGENT':<20} {'ACTION':<12} {'UPSTREAM':<10} {'IP':<16} {'MODEL':<18} {'TOOLS'}"
    )
    print("-" * 140)
    for e in reversed(entries):
        ts = datetime.fromtimestamp(e["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        seq = e["seq"]
        agent = e["agent_id"][:18]
        action = e["action_type"]
        upstream = (e.get("upstream") or "-")[:10]
        ip = (e.get("client_ip") or "-")[:15]
        model = e["model"][:16]
        tools = _as_list(e["tool_calls"])
        tool_names = ", ".join(tc.get("function", "?") for tc in tools) or "-"
        print(
            f"{seq:<5} {ts:<20} {agent:<20} {action:<12} {upstream:<10} {ip:<16} {model:<18} {tool_names}"
        )

    print()
    print(
        f"Showing {len(entries)} entries. Run 'ledger show <entry_id|seq>' for detail."
    )


def _cmd_ledger_verify():
    from .ledger import verify_chain, list_entries

    print("Verifying ledger chain integrity...")
    entries = list_entries(limit=1000000)
    print(f"  Entries to verify: {len(entries)}")

    valid, errors = verify_chain()

    if valid:
        print("✓ Ledger is intact — all signatures valid, chain unbroken.")
    else:
        print(f"✗ Ledger integrity FAILED — {len(errors)} error(s):")
        for err in errors:
            print(f"  • {err}")
        sys.exit(1)


def _cmd_ledger_show(entry_ref: str):
    from .ledger import get_entry
    from .ledger import list_entries
    from . import _features

    entry = None

    # Accept numeric sequence numbers from `ledger list`.
    if entry_ref.isdigit():
        wanted_seq = int(entry_ref)
        all_entries = list_entries(1000000)
        matches = [e for e in all_entries if e.get("seq") == wanted_seq]
        if matches:
            entry = matches[0]

    # Try direct UUID lookup.
    if entry is None:
        entry = get_entry(entry_ref)

    if not entry:
        # Try prefix match
        all_entries = list_entries(1000000)
        matches = [e for e in all_entries if e["entry_id"].startswith(entry_ref)]
        if not matches:
            print(f"Entry not found: {entry_ref}")
            return
        entry = matches[0]

    print("=" * 60)
    print("LEDGER ENTRY DETAIL")
    print("=" * 60)
    print(f"  ID         : {entry['entry_id']}")
    print(f"  Seq        : {entry['seq']}")
    print(f"  Time       : {datetime.fromtimestamp(entry['timestamp'])}")
    print(f"  Agent      : {entry['agent_id']}")
    print(f"  Session    : {entry.get('session_id', '')}")
    print(f"  Upstream   : {entry.get('upstream', '')}")
    print(f"  Client IP  : {entry.get('client_ip', '')}")
    print(f"  User-Agent : {(entry.get('user_agent', '') or '')[:120]}")
    print(f"  Action     : {entry['action_type']}")
    print(f"  Model      : {entry['model']}")
    print()

    why = _as_list(entry["why"])
    print("WHY (reasoning context):")
    for msg in why:
        role = msg["role"].upper()
        content = msg["content"][:200]
        print(f"  [{role}] {content}")
    print()

    tool_calls = _as_list(entry["tool_calls"])
    if tool_calls:
        print("TOOL CALLS:")
        for tc in tool_calls:
            print(f"  Function : {tc.get('function')}")
            print(f"  Args     : {json.dumps(tc.get('args', {}), indent=10)}")
    else:
        print("TOOL CALLS: none")
    print()

    full_messages = _as_list(entry.get("full_messages"))
    print(f"SESSION CONTEXT: {len(full_messages)} message(s) captured")
    for msg in full_messages:
        role = msg.get("role", "?").upper()
        content = str(msg.get("content", ""))[:200]
        print(f"  [{role}] {content}")
    print()

    print("INTEGRITY:")
    print(f"  Input hash  : {entry['input_hash']}")
    print(f"  Output hash : {entry['output_hash']}")
    print(f"  Prev hash   : {entry['prev_hash'][:32]}…")
    print(f"  Entry hash  : {entry['entry_hash'][:32]}…")

    # Verify signature
    signable = {
        "entry_id": entry["entry_id"],
        "timestamp": entry["timestamp"],
        "agent_id": entry["agent_id"],
        "action_type": entry["action_type"],
        "model": entry["model"],
        "why": _as_list(entry["why"]),
        "input_hash": entry["input_hash"],
        "output_hash": entry["output_hash"],
        "tool_calls": _as_list(entry["tool_calls"]),
        "prev_hash": entry["prev_hash"],
    }
    if _features.HAS_SIGNING and entry["signature"]:
        sig_valid = _features.signing.verify_payload(signable, entry["signature"])
        status = "✓ VALID" if sig_valid else "✗ INVALID"
    else:
        status = "— unsigned (sandbox edition)"
    print(f"  Signature   : {status}")
    print()


def _cmd_admin_create(args: list[str]):
    """Emergency CLI admin creation — works even if other admins exist.

    Use when every admin account is locked out or the bootstrap DB has been
    lost. Prints a generated temp password that the user must change on first
    login. If the username already exists, the command refuses rather than
    overwriting (avoid silent credential reset via filesystem access).
    """
    username: str = ""
    email: str = ""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--username" and i + 1 < len(args):
            username = args[i + 1]
            i += 2
        elif a == "--email" and i + 1 < len(args):
            email = args[i + 1]
            i += 2
        else:
            print(f"Unknown arg: {a}")
            print("Usage: kyde admin create-admin --username X --email Y")
            return

    if not username or not email:
        print("Both --username and --email are required.")
        print("Usage: kyde admin create-admin --username X --email Y")
        return

    from . import ledger as _ledger
    from . import auth as _auth

    if _ledger.get_user_by_username(username, include_deleted=True):
        print(
            f"✗ Username '{username}' already exists (including soft-deleted). Aborting."
        )
        return

    temp_pw = _auth.generate_temp_password()
    user = _ledger.create_user(
        username=username,
        email=email,
        password_hash=_auth.hash_password(temp_pw),
        roles=["admin"],
        must_change_password=True,
    )
    print("✓ Admin user created.")
    print(f"  Username          : {user['username']}")
    print(f"  Email             : {user['email']}")
    print(f"  Roles             : {user['roles']}")
    print(f"  Temp password     : {temp_pw}")
    print()
    print("  Share this password with the user and have them sign in.")
    print("  They will be forced to pick a new password on first login.")


def _cmd_backfill_ua_fields() -> None:
    """Populate request_network.ua_* for rows whose UA hasn't been parsed yet.

    Historical rows (backfilled from ledger.client_ip at schema init time)
    start with empty UA fields because UA parsing is Python-side. This
    command walks those rows in batches, re-parses the stored user_agent
    on the joined ledger row, and updates the side-table columns in place.
    """
    from . import ledger, network_origin

    batch_size = 500
    updated_total = 0
    scanned_total = 0

    print("Backfilling request_network UA fields…")
    while True:
        with ledger._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rn.seq, l.user_agent
                      FROM request_network rn
                      JOIN ledger          l USING (seq)
                     WHERE rn.ua_tool = ''
                       AND l.user_agent <> ''
                     ORDER BY rn.seq
                     LIMIT %s
                    """,
                    (batch_size,),
                )
                rows = list(cur.fetchall())

        if not rows:
            break

        scanned_total += len(rows)
        updates: list[tuple[str, str, str, int]] = []
        for r in rows:
            tool, ver, os_str = network_origin._parse_ua(r["user_agent"])
            # A row matches if EITHER the ua_tool or ua_version became
            # populated — don't skip rows that parse to "unknown" since
            # at minimum ua_os may have been extracted.
            if not (tool or ver or os_str):
                continue
            updates.append((tool, ver[:100], os_str[:100], int(r["seq"])))

        if not updates:
            # Every row in this batch parses to empty — bail out to avoid
            # an infinite loop on rows that can never match ua_tool <> ''.
            break

        with ledger._conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    UPDATE request_network
                       SET ua_tool = %s, ua_version = %s, ua_os = %s
                     WHERE seq = %s
                       AND ua_tool = ''
                    """,
                    updates,
                )
            conn.commit()

        updated_total += len(updates)
        print(f"  scanned {scanned_total}, updated {updated_total}…")

        if len(rows) < batch_size:
            break

    print(f"✓ UA backfill complete — scanned {scanned_total}, updated {updated_total}.")


def _cmd_dlp_dedupe(args: list[str]) -> None:
    """Close legacy duplicate DLP alerts caused by full-context re-scanning.

    Before the delta-scan fix, every entry's `full_messages` got scanned
    end-to-end, so one user-side leak in turn 1 produced one alert per
    subsequent turn. This walks each session's open alerts oldest-first
    and closes any whose findings are entirely subsumed by an earlier
    alert in the same session (disposition='duplicate').

    Alerts that introduce at least one NEW finding (different pattern
    or different matched value) are kept open — they still carry signal.

    Pass --dry-run to preview without writing.
    """
    from . import ledger

    dry_run = "--dry-run" in args

    def _finding_signatures(findings: list[dict]) -> set[tuple[str, str]]:
        """Stable per-finding key: (entity_type / pattern_name, normalized text)."""
        out: set[tuple[str, str]] = set()
        for m in findings or []:
            if not isinstance(m, dict):
                continue
            etype = str(
                m.get("entity_type") or m.get("pattern_name") or m.get("label") or ""
            )
            text = (
                str(m.get("text") or m.get("value") or m.get("matched_value") or "")
                .strip()
                .lower()
            )
            if etype or text:
                out.add((etype, text))
        return out

    print("Scanning open DLP alerts for in-session duplicates…")
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, alert_id, session_id, scanner, findings, created_at
                  FROM dlp_alerts
                 WHERE status <> 'closed'
                   AND session_id <> ''
                 ORDER BY session_id, id ASC
                """
            )
            rows = list(cur.fetchall())

    by_session: dict[str, list[dict]] = {}
    for r in rows:
        by_session.setdefault(r["session_id"], []).append(r)

    scanned_sessions = 0
    closed_count = 0
    closures: list[tuple[str, str]] = []  # (alert_id, parent_alert_id)

    for session_id, alerts in by_session.items():
        scanned_sessions += 1
        seen: set[tuple[str, str]] = set()
        canonical_alert_id: str | None = None
        for a in alerts:
            sigs = _finding_signatures(a.get("findings") or [])
            if not sigs:
                # No usable findings — leave alone, don't risk closing an
                # alert whose payload we can't reason about.
                continue
            if canonical_alert_id is None:
                # First alert in this session anchors the "seen" set.
                seen |= sigs
                canonical_alert_id = a["alert_id"]
                continue
            if sigs.issubset(seen):
                # Every finding on this alert was already on an earlier
                # open alert in the same session — pure replay noise.
                closures.append((a["alert_id"], canonical_alert_id))
            else:
                # New signal — keep it open and grow the seen set so later
                # alerts can dedupe against it too.
                seen |= sigs

    closed_count = len(closures)
    print(
        f"  · scanned {scanned_sessions} session{'s' if scanned_sessions != 1 else ''} "
        f"with {len(rows)} open alert{'s' if len(rows) != 1 else ''}"
    )
    print(
        f"  · {'would close' if dry_run else 'closing'} "
        f"{closed_count} duplicate alert{'s' if closed_count != 1 else ''}"
    )

    if dry_run:
        for alert_id, parent in closures[:20]:
            print(f"     - {alert_id[:8]} → duplicate of {parent[:8]}")
        if len(closures) > 20:
            print(f"     … and {len(closures) - 20} more")
        print("(dry-run; no rows modified)")
        return

    if closed_count == 0:
        print("Nothing to close.")
        return

    # Route through the state machine so each close appends a
    # dlp_alert_events row with actor_kind='system'. Per-row instead of
    # one bulk UPDATE because we want one audit event per alert.
    from . import dlp_triage

    failed: list[tuple[str, str]] = []
    for alert_id, parent in closures:
        try:
            dlp_triage.transition(
                alert_id=alert_id,
                to_status="closed",
                actor_kind="system",
                disposition="duplicate",
                note=f"auto-closed as duplicate of {parent}",
            )
        except dlp_triage.TransitionError as exc:
            # Most likely the alert is already closed by another path;
            # skip and report at the end so a single bad row doesn't
            # abort the whole batch.
            failed.append((alert_id, str(exc)))

    succeeded = closed_count - len(failed)
    print(f"✓ Closed {succeeded} duplicate alert{'s' if succeeded != 1 else ''}.")
    if failed:
        print(f"  Skipped {len(failed)} (already closed or invalid transition):")
        for alert_id, msg in failed[:5]:
            print(f"     - {alert_id[:8]}: {msg}")
        if len(failed) > 5:
            print(f"     ... and {len(failed) - 5} more.")


def _print_help():
    print(__doc__ if __doc__ else "")
    print(
        """
Commands:
  keygen [--type local|tpm] [--force]  Generate signing keypair (default: local Ed25519)
  key                                  Show public key and key source (TPM or local)
  serve [--port N]                     Start the proxy server (default port 8000)
  dashboard [--port N]                 Launch the Streamlit audit dashboard (default port 8501)
  ledger list                          List recent ledger entries
  ledger verify                        Verify full chain integrity
  ledger show <id|seq>                 Show detailed entry by UUID/prefix or sequence number
  admin create-admin --username X --email Y
                                       Create a new admin account (recovery path
                                       when all admins are locked out). Prints
                                       a one-time temp password.
  backfill ua-fields                   Parse stored user_agent on historical
                                       request_network rows and populate the
                                       ua_tool / ua_version / ua_os columns.
                                       Safe to re-run; only touches rows still
                                       at their defaults.
  dlp dedupe-alerts [--dry-run]        One-shot cleanup of duplicate DLP alerts
                                       left over from full-context re-scanning.
                                       Walks each session oldest-first and
                                       closes alerts whose findings are entirely
                                       subsumed by an earlier alert in the same
                                       session (disposition='duplicate').
                                       --dry-run previews without writing.
"""
    )
