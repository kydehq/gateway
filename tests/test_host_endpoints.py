"""
Tests for the host-resolution HTTP surface (Phase 2 step 3):
/api/hosts/resolve, /api/host-labels GET/PUT/DELETE, /refresh.

DNS is mocked at the host_resolver seam.
"""

from unittest.mock import patch


from kyde import auth, host_resolver, ledger

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


def _seed_viewer(client):
    ledger.create_user(
        username="admin",
        email="admin@example.test",
        password_hash=auth.hash_password(_PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )
    ledger.create_user(
        username="viewer",
        email="viewer@example.test",
        password_hash=auth.hash_password(_PASSWORD),
        roles=["viewer"],
        must_change_password=False,
    )
    r = client.post(
        "/login",
        data={"username": "viewer", "password": _PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303


# ---------------------------------------------------------------------------
# /api/hosts/resolve
# ---------------------------------------------------------------------------


def test_resolve_requires_auth(client):
    r = client.get("/api/hosts/resolve?identifier=10.4.0.1")
    assert r.status_code == 401


def test_resolve_ip_returns_hostname(client):
    _seed_admin(client)
    with patch.object(host_resolver, "_reverse_dns_sync", return_value="api.internal"):
        body = client.get("/api/hosts/resolve?identifier=10.4.0.1").json()
    assert body["kind"] == "ip"
    assert body["ip"] == "10.4.0.1"
    assert body["hostname"] == "api.internal"


def test_resolve_hostname_returns_matching_ips(client):
    _seed_admin(client)
    ledger.upsert_host_label(ip="10.4.0.10", hostname="lb.internal")
    ledger.upsert_host_label(ip="10.4.0.11", hostname="lb.internal")

    body = client.get("/api/hosts/resolve?identifier=lb.internal").json()
    assert body["kind"] == "hostname"
    assert body["hostname"] == "lb.internal"
    assert {x["ip"] for x in body["ips"]} == {"10.4.0.10", "10.4.0.11"}


def test_resolve_unknown_hostname_returns_empty_ips(client):
    _seed_admin(client)
    body = client.get("/api/hosts/resolve?identifier=nope.example.com").json()
    assert body["kind"] == "hostname"
    assert body["ips"] == []


# ---------------------------------------------------------------------------
# /api/host-labels (list)
# ---------------------------------------------------------------------------


def _seed_observed_ip(seq: int, timestamp: float, ip: str):
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ledger (seq, entry_id, timestamp, agent_id,"
                " action_type, model, input_hash, output_hash, prev_hash,"
                " entry_hash, signature)"
                " VALUES (%s, %s, %s, 'x', 'chat', 'm', '0','0','0','0','s')",
                (seq, f"e-{seq}", timestamp),
            )
            cur.execute(
                "INSERT INTO request_network (seq, timestamp, remote_addr) "
                "VALUES (%s, %s, %s::inet)",
                (seq, timestamp, ip),
            )
        conn.commit()


def test_host_labels_list_status_labeled(client):
    _seed_admin(client)
    _seed_observed_ip(1, 1700000000.0, "10.4.0.20")
    ledger.upsert_host_label(ip="10.4.0.20", hostname="api.internal")

    body = client.get("/api/host-labels?status=labeled").json()
    assert len(body) == 1
    assert body[0]["ip"] == "10.4.0.20"
    assert body[0]["hostname"] == "api.internal"
    assert body[0]["source"] == "admin"


def test_host_labels_list_search_matches_ip(client):
    _seed_admin(client)
    _seed_observed_ip(2, 1700000000.0, "10.4.0.21")
    body = client.get("/api/host-labels?status=all&q=10.4.0.21").json()
    assert any(r["ip"] == "10.4.0.21" for r in body)


def test_host_labels_list_dns_miss_shown_distinctly(client):
    _seed_admin(client)
    _seed_observed_ip(3, 1700000000.0, "10.4.0.22")
    # Simulate a cached dns miss.
    ledger.upsert_host_dns(ip="10.4.0.22", hostname=None, ttl_seconds=3600)

    body = client.get("/api/host-labels?status=unlabeled").json()
    target = next(r for r in body if r["ip"] == "10.4.0.22")
    # Source string is "dns miss" so the UI can render it as a distinct
    # state instead of a generic "unlabeled".
    assert target["source"] == "dns miss"


def test_host_labels_list_rejects_unsupported_status(client):
    _seed_admin(client)
    r = client.get("/api/host-labels?status=mystery")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# /api/host-labels (mutations)
# ---------------------------------------------------------------------------


def test_host_label_put_requires_admin(client):
    _seed_viewer(client)
    r = client.put("/api/host-labels/10.4.0.30", json={"hostname": "x.local"})
    assert r.status_code == 403


def test_host_label_put_creates_admin_row(client):
    _seed_admin(client)
    r = client.put("/api/host-labels/10.4.0.31", json={"hostname": "crm.internal"})
    assert r.status_code == 200
    body = r.json()
    assert body["ip"] == "10.4.0.31"
    assert body["hostname"] == "crm.internal"
    assert body["source"] == "admin"


def test_host_label_put_rejects_empty_hostname(client):
    _seed_admin(client)
    r = client.put("/api/host-labels/10.4.0.32", json={"hostname": "   "})
    assert r.status_code == 400


def test_host_label_delete_removes_admin_row(client):
    _seed_admin(client)
    client.put("/api/host-labels/10.4.0.33", json={"hostname": "tmp.local"})
    r = client.delete("/api/host-labels/10.4.0.33")
    assert r.status_code == 200
    # Idempotent-failure: deleting an unlabeled IP gives 404.
    r2 = client.delete("/api/host-labels/10.4.0.33")
    assert r2.status_code == 404


def test_host_label_refresh_forces_dns_lookup(client):
    _seed_admin(client)
    # Seed a stale dns row to verify refresh replaces it.
    ledger.upsert_host_dns(ip="10.4.0.34", hostname="old.local", ttl_seconds=3600)
    with patch.object(host_resolver, "_reverse_dns_sync", return_value="new.local"):
        r = client.post("/api/host-labels/10.4.0.34/refresh")
    assert r.status_code == 200
    assert r.json()["hostname"] == "new.local"


def test_host_label_refresh_preserves_admin_label(client):
    _seed_admin(client)
    ledger.upsert_host_label(ip="10.4.0.35", hostname="admin.corp")
    with patch.object(host_resolver, "_reverse_dns_sync", return_value="dns.local"):
        r = client.post("/api/host-labels/10.4.0.35/refresh")
    assert r.json()["hostname"] == "admin.corp"
    assert r.json()["source"] == "admin"
