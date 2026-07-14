"""
Tests for the `window` query param on /api/stats and /api/token-analysis
(Agent Activity time filter).

`get_stats_rows` and `get_token_analysis_rows` accept an optional `since`
floor. The dashboard endpoints map `window` (1h/24h/7d/30d/90d/all) to
that floor — default 24h, "all" means no floor.
"""

import time


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


def _insert_at(timestamp: float, agent_id: str = "agent:win"):
    """Insert a ledger row at an arbitrary timestamp via direct SQL — the
    public append() path uses time.time() and we need rows in the past for
    window-filtering assertions."""
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ledger ("
                "  entry_id, timestamp, agent_id, action_type, model,"
                "  input_hash, output_hash, prev_hash, entry_hash, signature,"
                "  session_id, prompt_tokens, completion_tokens, upstream"
                ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    f"e-{timestamp}-{agent_id}",
                    timestamp,
                    agent_id,
                    "chat",
                    "gpt-4o",
                    "0" * 64,
                    "0" * 64,
                    "0" * 64,
                    "0" * 64,
                    "stub",
                    f"s-{timestamp}",
                    100,
                    50,
                    "openai",
                ),
            )
        conn.commit()


def test_stats_default_window_is_24h(client):
    _seed_admin(client)
    now = time.time()
    _insert_at(now - 60, agent_id="agent:recent")
    _insert_at(now - 3 * 86400, agent_id="agent:old")  # 3 days back

    body = client.get("/api/stats").json()
    # Default is 24h, so the 3-days-back row should be excluded.
    assert "agent:recent" in body["agents"]
    assert "agent:old" not in body["agents"]


def test_stats_window_7d_includes_older_rows(client):
    _seed_admin(client)
    now = time.time()
    _insert_at(now - 60, agent_id="agent:recent")
    _insert_at(now - 3 * 86400, agent_id="agent:old")
    _insert_at(now - 10 * 86400, agent_id="agent:ancient")

    body = client.get("/api/stats?window=7d").json()
    assert "agent:recent" in body["agents"]
    assert "agent:old" in body["agents"]
    assert "agent:ancient" not in body["agents"]


def test_stats_window_all_returns_everything(client):
    _seed_admin(client)
    now = time.time()
    _insert_at(now - 60, agent_id="agent:recent")
    _insert_at(now - 100 * 86400, agent_id="agent:way-back")

    body = client.get("/api/stats?window=all").json()
    assert "agent:recent" in body["agents"]
    assert "agent:way-back" in body["agents"]


def test_stats_rejects_unsupported_window(client):
    _seed_admin(client)
    r = client.get("/api/stats?window=42years")
    assert r.status_code == 400


def test_token_analysis_default_window_is_24h(client):
    _seed_admin(client)
    now = time.time()
    _insert_at(now - 60, agent_id="agent:t-recent")
    _insert_at(now - 3 * 86400, agent_id="agent:t-old")

    body = client.get("/api/token-analysis").json()
    assert "agent:t-recent" in body["by_agent"]
    assert "agent:t-old" not in body["by_agent"]


def test_token_analysis_is_tokens_only(client):
    """Cost reporting was retired: the response carries token aggregates and
    no USD/EUR/FX fields, top-level or per-bucket."""
    _seed_admin(client)
    now = time.time()
    _insert_at(now - 60, agent_id="agent:tok")

    body = client.get("/api/token-analysis").json()

    # Token totals present.
    assert body["total_tokens"] == 150
    assert body["total_prompt_tokens"] == 100
    assert body["total_completion_tokens"] == 50

    # No cost surface anywhere.
    cost_keys = {
        "total_usd",
        "total_eur",
        "total_prompt_usd",
        "total_completion_usd",
        "total_prompt_eur",
        "total_completion_eur",
        "fx_usd_eur",
    }
    assert cost_keys.isdisjoint(body.keys())
    bucket = body["by_agent"]["agent:tok"]
    assert {"prompt_usd", "completion_usd", "total_usd", "total_eur"}.isdisjoint(
        bucket.keys()
    )
    assert bucket["total_tokens"] == 150


def test_token_analysis_window_all(client):
    _seed_admin(client)
    now = time.time()
    _insert_at(now - 60, agent_id="agent:tt-recent")
    _insert_at(now - 100 * 86400, agent_id="agent:tt-way-back")

    body = client.get("/api/token-analysis?window=all").json()
    assert "agent:tt-recent" in body["by_agent"]
    assert "agent:tt-way-back" in body["by_agent"]


def test_entries_default_window_is_24h(client):
    _seed_admin(client)
    now = time.time()
    _insert_at(now - 60, agent_id="agent:e-recent")
    _insert_at(now - 3 * 86400, agent_id="agent:e-old")

    body = client.get("/api/entries").json()
    agent_ids = {it["agent_id"] for it in body["items"]}
    assert "agent:e-recent" in agent_ids
    assert "agent:e-old" not in agent_ids
    # total_count reflects the windowed set, not the full ledger.
    assert body["total_count"] == 1


def test_entries_session_id_filter(client):
    """/api/entries supports session_id= to narrow to a single session.
    Powers the "Full audit trail →" link on Sessions / Agent Chains."""
    _seed_admin(client)
    now = time.time()
    sid = "session-xyz"
    _insert_at(now - 60, agent_id="agent:a")
    # Insert one row tagged with sid via direct SQL since _insert_at uses
    # its own session_id pattern.
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ledger ("
                " entry_id, timestamp, agent_id, action_type, model,"
                " input_hash, output_hash, prev_hash, entry_hash, signature,"
                " session_id, upstream"
                ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    f"e-{now}-sid",
                    now - 30,
                    "agent:b",
                    "chat",
                    "gpt-4o",
                    "0" * 64,
                    "0" * 64,
                    "0" * 64,
                    "0" * 64,
                    "stub",
                    sid,
                    "openai",
                ),
            )
        conn.commit()

    body = client.get(f"/api/entries?session_id={sid}").json()
    assert body["total_count"] == 1
    assert all(it["session_id"] == sid for it in body["items"])


def test_entries_window_all_returns_everything(client):
    _seed_admin(client)
    now = time.time()
    _insert_at(now - 60, agent_id="agent:e-recent")
    _insert_at(now - 100 * 86400, agent_id="agent:e-ancient")

    body = client.get("/api/entries?window=all").json()
    agent_ids = {it["agent_id"] for it in body["items"]}
    assert {"agent:e-recent", "agent:e-ancient"}.issubset(agent_ids)


def test_token_analysis_window_90d(client):
    _seed_admin(client)
    now = time.time()
    _insert_at(now - 60, agent_id="agent:n-recent")
    _insert_at(now - 60 * 86400, agent_id="agent:n-mid")
    _insert_at(now - 100 * 86400, agent_id="agent:n-old")

    body = client.get("/api/token-analysis?window=90d").json()
    assert "agent:n-recent" in body["by_agent"]
    assert "agent:n-mid" in body["by_agent"]
    assert "agent:n-old" not in body["by_agent"]
