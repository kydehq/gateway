"""
Tests for the /api/sessions filter bar (Item 3 of the deferred polish list):
window, has_alert, agent, sort.
"""

import time
import uuid


from kyde import auth, ledger

_PASSWORD = "CorrectHorse!Battery9"


def _seed_admin(client):
    ledger.create_user(
        username="admin",
        email="admin@example.test",
        password_hash=auth.hash_password(_PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )
    r = client.post(
        "/login",
        data={"username": "admin", "password": _PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303


def _insert_session_row(timestamp: float, session_id: str, agent_id: str):
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ledger ("
                "  entry_id, timestamp, agent_id, action_type, model,"
                "  input_hash, output_hash, prev_hash, entry_hash, signature,"
                "  session_id, upstream"
                ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    f"e-{uuid.uuid4().hex[:8]}",
                    timestamp,
                    agent_id,
                    "chat",
                    "gpt-4o",
                    "0" * 64,
                    "0" * 64,
                    "0" * 64,
                    "0" * 64,
                    "stub",
                    session_id,
                    "openai",
                ),
            )
        conn.commit()


def _insert_alert(session_id: str, entry_id: str = "stub", status: str = "new"):
    """Insert a synthetic alert. The schema's disposition_ck constraint
    requires `disposition` IS NOT NULL when status='closed' — we set a
    benign 'false_positive' to satisfy that."""
    aid = f"alert-{uuid.uuid4().hex[:12]}"
    now = time.time()
    disposition = "false_positive" if status == "closed" else None
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dlp_alerts ("
                "  alert_id, entry_id, session_id, scanner, score, findings,"
                "  status, disposition, created_at, updated_at, dedup_hash"
                ") VALUES (%s,%s,%s,%s,%s,'[]'::jsonb,%s,%s,%s,%s,%s)",
                (
                    aid,
                    entry_id,
                    session_id,
                    "regex",
                    0.5,
                    status,
                    disposition,
                    now,
                    now,
                    aid,
                ),
            )
        conn.commit()


def test_window_filter_excludes_old_sessions(client):
    _seed_admin(client)
    now = time.time()
    _insert_session_row(now - 60, "s-recent", "agent:a")
    _insert_session_row(now - 5 * 86400, "s-old", "agent:b")

    body = client.get("/api/sessions?window=24h").json()
    ids = {s["session_id"] for s in body["items"]}
    assert "s-recent" in ids
    assert "s-old" not in ids


def test_window_all_includes_old_sessions(client):
    _seed_admin(client)
    now = time.time()
    _insert_session_row(now - 100 * 86400, "s-ancient", "agent:c")

    body = client.get("/api/sessions?window=all").json()
    ids = {s["session_id"] for s in body["items"]}
    assert "s-ancient" in ids


def test_has_alert_yes_keeps_only_alerted_sessions(client):
    _seed_admin(client)
    now = time.time()
    _insert_session_row(now - 60, "s-with-alert", "agent:a")
    _insert_session_row(now - 60, "s-without", "agent:b")
    _insert_alert("s-with-alert")

    body = client.get("/api/sessions?window=24h&has_alert=yes").json()
    ids = {s["session_id"] for s in body["items"]}
    assert "s-with-alert" in ids
    assert "s-without" not in ids


def test_has_alert_no_excludes_alerted_sessions(client):
    _seed_admin(client)
    now = time.time()
    _insert_session_row(now - 60, "s-alerted", "agent:a")
    _insert_session_row(now - 60, "s-clean", "agent:b")
    _insert_alert("s-alerted")

    body = client.get("/api/sessions?window=24h&has_alert=no").json()
    ids = {s["session_id"] for s in body["items"]}
    assert "s-alerted" not in ids
    assert "s-clean" in ids


def test_has_alert_only_counts_open_alerts(client):
    _seed_admin(client)
    now = time.time()
    _insert_session_row(now - 60, "s-closed-alert", "agent:a")
    _insert_alert("s-closed-alert", status="closed")

    body = client.get("/api/sessions?window=24h&has_alert=yes").json()
    ids = {s["session_id"] for s in body["items"]}
    # Closed alerts don't satisfy the has_alert=yes filter.
    assert "s-closed-alert" not in ids


def test_agent_filter_keeps_only_matching_sessions(client):
    _seed_admin(client)
    now = time.time()
    _insert_session_row(now - 60, "s-A", "agent:alpha")
    _insert_session_row(now - 60, "s-B", "agent:beta")

    body = client.get("/api/sessions?window=24h&agent=agent:alpha").json()
    ids = {s["session_id"] for s in body["items"]}
    assert "s-A" in ids
    assert "s-B" not in ids


def test_agent_filter_supports_multi(client):
    _seed_admin(client)
    now = time.time()
    _insert_session_row(now - 60, "s-A", "agent:alpha")
    _insert_session_row(now - 60, "s-B", "agent:beta")
    _insert_session_row(now - 60, "s-G", "agent:gamma")

    body = client.get(
        "/api/sessions?window=24h&agent=agent:alpha&agent=agent:beta"
    ).json()
    ids = {s["session_id"] for s in body["items"]}
    assert {"s-A", "s-B"}.issubset(ids)
    assert "s-G" not in ids


def test_sort_oldest_orders_first_time_ascending(client):
    _seed_admin(client)
    now = time.time()
    _insert_session_row(now - 60, "s-2", "agent:a")
    _insert_session_row(now - 3600, "s-1", "agent:a")
    _insert_session_row(now - 10, "s-3", "agent:a")

    body = client.get("/api/sessions?window=24h&sort=oldest").json()
    order = [s["session_id"] for s in body["items"]]
    assert order[:3] == ["s-1", "s-2", "s-3"]


def test_sort_entries_orders_by_entry_count(client):
    _seed_admin(client)
    now = time.time()
    for _ in range(5):
        _insert_session_row(now - 60, "s-busy", "agent:a")
    _insert_session_row(now - 60, "s-quiet", "agent:b")

    body = client.get("/api/sessions?window=24h&sort=entries").json()
    order = [s["session_id"] for s in body["items"][:2]]
    assert order[0] == "s-busy"


def test_invalid_sort_rejected(client):
    _seed_admin(client)
    r = client.get("/api/sessions?window=24h&sort=zzz")
    assert r.status_code == 422  # Pydantic rejects pattern mismatch


def test_invalid_has_alert_rejected(client):
    _seed_admin(client)
    r = client.get("/api/sessions?window=24h&has_alert=maybe")
    assert r.status_code == 422


def test_session_status_blocked_observed_allowed(client):
    """Each session decorates with status in {blocked, observed, allowed}.
    blocked > observed in precedence — a session with both a policy_block
    entry AND a DLP alert is still 'blocked'."""
    _seed_admin(client)
    now = time.time()
    # ALLOWED: clean session
    _insert_session_row(now - 60, "s-clean", "agent:a")

    # OBSERVED: open alert, no block
    _insert_session_row(now - 60, "s-obs", "agent:b")
    _insert_alert("s-obs")

    # BLOCKED: policy_block entry
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ledger ("
                "  entry_id, timestamp, agent_id, action_type, model,"
                "  input_hash, output_hash, prev_hash, entry_hash, signature,"
                "  session_id, upstream"
                ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    f"e-block-{uuid.uuid4().hex[:8]}",
                    now - 60,
                    "agent:c",
                    "policy_block",
                    "gpt-4o",
                    "0" * 64,
                    "0" * 64,
                    "0" * 64,
                    "0" * 64,
                    "stub",
                    "s-blk",
                    "openai",
                ),
            )
        conn.commit()

    body = client.get("/api/sessions?window=24h").json()
    by_id = {s["session_id"]: s for s in body["items"]}
    assert by_id["s-clean"]["status"] == "allowed"
    assert by_id["s-obs"]["status"] == "observed"
    assert by_id["s-blk"]["status"] == "blocked"


def test_status_filter_keeps_only_blocked(client):
    _seed_admin(client)
    now = time.time()
    _insert_session_row(now - 60, "s-allow", "agent:a")
    _insert_session_row(now - 60, "s-block", "agent:b")
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ledger ("
                "  entry_id, timestamp, agent_id, action_type, model,"
                "  input_hash, output_hash, prev_hash, entry_hash, signature,"
                "  session_id, upstream"
                ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    f"e-blk-{uuid.uuid4().hex[:8]}",
                    now - 60,
                    "agent:b",
                    "policy_block",
                    "gpt-4o",
                    "0" * 64,
                    "0" * 64,
                    "0" * 64,
                    "0" * 64,
                    "stub",
                    "s-block",
                    "openai",
                ),
            )
        conn.commit()

    body = client.get("/api/sessions?window=24h&status=blocked").json()
    ids = {s["session_id"] for s in body["items"]}
    assert "s-block" in ids
    assert "s-allow" not in ids


def test_status_filter_combines_multiple(client):
    _seed_admin(client)
    now = time.time()
    _insert_session_row(now - 60, "s-allow", "agent:a")
    _insert_session_row(now - 60, "s-obs", "agent:b")
    _insert_alert("s-obs")

    # blocked+observed should keep s-obs and exclude s-allow.
    body = client.get("/api/sessions?window=24h&status=blocked&status=observed").json()
    ids = {s["session_id"] for s in body["items"]}
    assert "s-obs" in ids
    assert "s-allow" not in ids


def test_status_filter_rejects_invalid_value(client):
    _seed_admin(client)
    # Pydantic doesn't validate the strings here (List[str]); the ledger
    # layer raises ValueError → 400 via the endpoint.
    r = client.get("/api/sessions?window=24h&status=mystery")
    assert r.status_code == 400
