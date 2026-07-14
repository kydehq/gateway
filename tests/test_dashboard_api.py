"""
Dashboard HTTP tests — exercise the FastAPI app via TestClient so the real
auth middleware, session cookie, and role gates all run.

Covered:
- bootstrap gate: / → /setup until an admin exists
- /setup creates the first admin
- /login good + bad + lockout
- /api/whoami reflects roles
- /api/users CRUD (admin gated)
- last-admin / self-delete / self-elevation guards
- forced password change flow
- viewer / auditor role gating
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

from kyde import auth, ledger
from kyde._features import HAS_SIGNING

PASSWORD = "CorrectHorse!Battery9"
SECOND_PW = "TrombonePaperclip9!"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_user(
    username: str,
    roles: list[str],
    *,
    password: str = PASSWORD,
    must_change: bool = False,
) -> dict:
    return ledger.create_user(
        username=username,
        email=f"{username}@example.test",
        password_hash=auth.hash_password(password),
        roles=roles,
        must_change_password=must_change,
    )


def _login(client, username: str, password: str = PASSWORD) -> "httpx.Response":
    # follow_redirects=False so we can assert on 303s explicitly.
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def _login_as(client, username: str, password: str = PASSWORD) -> None:
    """Log in and keep the session cookie on the client for subsequent calls."""
    resp = _login(client, username, password)
    assert resp.status_code == 303, resp.text


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def test_root_redirects_to_setup_when_no_admin(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/setup")


def test_setup_creates_first_admin_and_signs_in(client):
    resp = client.post(
        "/setup",
        data={
            "email": "admin@example.test",
            "password": PASSWORD,
            "confirm": PASSWORD,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/")
    assert "session" in resp.cookies

    assert ledger.any_admin_exists() is True
    me = client.get("/api/whoami").json()
    assert me["username"] == "admin"
    assert "admin" in me["roles"]


def test_setup_rejects_weak_password(client):
    resp = client.post(
        "/setup",
        data={"email": "admin@example.test", "password": "short", "confirm": "short"},
        follow_redirects=False,
    )
    # validate_password failure → redirect back to /setup with ?error=
    assert resp.status_code == 303
    assert "/setup" in resp.headers["location"]
    assert ledger.any_admin_exists() is False


def test_setup_mismatched_confirm(client):
    resp = client.post(
        "/setup",
        data={"email": "admin@example.test", "password": PASSWORD, "confirm": "other"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "Passwords+do+not+match" in resp.headers["location"]


def test_setup_refused_once_admin_exists(client):
    _seed_user("admin", ["admin"])
    resp = client.post(
        "/setup",
        data={"email": "x@x", "password": PASSWORD, "confirm": PASSWORD},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Login + session
# ---------------------------------------------------------------------------


def test_login_success_sets_session_cookie(client):
    _seed_user("admin", ["admin"])
    resp = _login(client, "admin")
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/")
    assert "session" in resp.cookies


def test_login_bad_password_no_session(client):
    _seed_user("admin", ["admin"])
    resp = _login(client, "admin", password="wrongpassword!X9")
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert "session" not in resp.cookies


def test_login_locks_after_threshold_failures(client):
    u = _seed_user("alice", ["viewer"])
    for _ in range(ledger.LOCKOUT_THRESHOLD):
        _login(client, "alice", password="wrongpassword!X9")

    fresh = ledger.get_user_by_id(u["id"])
    assert fresh is not None
    assert fresh["locked"] is True

    # Right password now fails too — same generic error so an attacker can't
    # distinguish "bad password" from "account locked".
    resp = _login(client, "alice", password=PASSWORD)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


def test_whoami_requires_session(client):
    assert client.get("/api/whoami").status_code == 401


# ---------------------------------------------------------------------------
# Admin user CRUD (/api/users)
# ---------------------------------------------------------------------------


def test_non_admin_cannot_list_users(client):
    _seed_user("admin", ["admin"])
    _seed_user("bob", ["viewer"])
    _login_as(client, "bob")
    assert client.get("/api/users").status_code == 403


def test_admin_creates_user_with_temp_password(client):
    _seed_user("admin", ["admin"])
    _login_as(client, "admin")

    resp = client.post(
        "/api/users",
        json={
            "username": "carol",
            "email": "carol@example.test",
            "roles": ["viewer"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["username"] == "carol"
    assert body["user"]["roles"] == ["viewer"]
    assert body["user"]["must_change_password"] is True
    assert len(body["temp_password"]) >= 12

    # The temp password satisfies our own policy.
    assert auth.validate_password(body["temp_password"]) == []


def test_admin_create_user_rejects_duplicate_username(client):
    _seed_user("admin", ["admin"])
    _seed_user("dup", ["viewer"])
    _login_as(client, "admin")
    resp = client.post(
        "/api/users",
        json={"username": "dup", "email": "dup@example.test", "roles": ["viewer"]},
    )
    assert resp.status_code == 409


def test_admin_create_user_requires_email_and_role(client):
    _seed_user("admin", ["admin"])
    _login_as(client, "admin")

    r = client.post(
        "/api/users",
        json={"username": "x", "email": "not-an-email", "roles": ["viewer"]},
    )
    assert r.status_code == 400

    r = client.post(
        "/api/users",
        json={"username": "x", "email": "x@example.test", "roles": []},
    )
    assert r.status_code == 400


def test_admin_lists_users_includes_newly_created(client):
    _seed_user("admin", ["admin"])
    _seed_user("bob", ["viewer"])
    _login_as(client, "admin")
    names = {u["username"] for u in client.get("/api/users").json()}
    assert {"admin", "bob"} <= names


def test_admin_patch_user_roles(client):
    _seed_user("admin", ["admin"])
    bob = _seed_user("bob", ["viewer"])
    _login_as(client, "admin")

    r = client.patch(f"/api/users/{bob['id']}", json={"roles": ["auditor"]})
    assert r.status_code == 200
    assert r.json()["roles"] == ["auditor"]


def test_admin_cannot_delete_self(client):
    admin = _seed_user("admin", ["admin"])
    _login_as(client, "admin")
    r = client.delete(f"/api/users/{admin['id']}")
    assert r.status_code == 400
    assert r.json()["error"] == "cannot_delete_self"


def test_cannot_delete_last_admin(client):
    # Two admins exist, but we'll try to delete the non-self one. The
    # self-delete guard above handles the same user; here we set up a second
    # admin, log in as the second, and try to delete the first — that's
    # allowed because another admin remains. Then we try to delete the only
    # remaining admin (via disable) and expect a last-admin rejection.
    a1 = _seed_user("primary", ["admin"])
    a2 = _seed_user("secondary", ["admin"])
    _login_as(client, "secondary")

    # Deleting the other admin is allowed while secondary is still an admin.
    r = client.delete(f"/api/users/{a1['id']}")
    assert r.status_code == 204

    # Now `secondary` is the only admin. Trying to disable them must fail.
    r = client.patch(f"/api/users/{a2['id']}", json={"enabled": False})
    assert r.status_code == 409
    assert r.json()["error"] == "last_admin"


def test_admin_cannot_self_elevate_to_auditor(client):
    """An admin cannot grant themselves the auditor role — a second admin must."""
    admin = _seed_user("admin", ["admin"])
    _login_as(client, "admin")
    r = client.patch(f"/api/users/{admin['id']}", json={"roles": ["admin", "auditor"]})
    assert r.status_code == 403
    assert r.json()["error"] == "self_elevation_forbidden"


def test_admin_reset_password_invalidates_sessions(client):
    _seed_user("admin", ["admin"])
    bob = _seed_user("bob", ["viewer"])
    # Log bob in to mint a session.
    _login_as(client, "bob")
    bob_whoami = client.get("/api/whoami")
    assert bob_whoami.status_code == 200

    # Switch to admin and reset bob's password.
    client.cookies.clear()
    _login_as(client, "admin")
    r = client.post(f"/api/users/{bob['id']}/reset-password")
    assert r.status_code == 200
    assert "temp_password" in r.json()

    # Bob's cached session no longer resolves.
    # (The admin's cookie still works — we only drop sessions for user_id == bob.)
    from kyde import ledger

    assert ledger.list_sessions_for_user(bob["id"]) == []


# ---------------------------------------------------------------------------
# Forced password change flow
# ---------------------------------------------------------------------------


def test_forced_password_change_blocks_other_apis(client):
    # Admin must exist so the bootstrap gate is satisfied — otherwise
    # middleware routes everything to /setup, masking the forced-change gate.
    _seed_user("admin", ["admin"])
    _seed_user("bob", ["viewer"], must_change=True)
    _login_as(client, "bob")

    # /api/whoami is whitelisted on the forced-change path.
    assert client.get("/api/whoami").status_code == 200

    # Everything else is blocked with 409.
    assert client.get("/api/stats").status_code == 409


def test_change_password_lifts_forced_gate(client):
    _seed_user("admin", ["admin"])
    _seed_user("bob", ["viewer"], must_change=True)
    _login_as(client, "bob")
    r = client.post(
        "/api/change-password",
        json={"new_password": SECOND_PW},
    )
    assert r.status_code == 204
    # Gate now lifted — stats becomes reachable.
    assert client.get("/api/stats").status_code == 200


# ---------------------------------------------------------------------------
# Read-path sanity
# ---------------------------------------------------------------------------


def test_stats_and_entries_empty_by_default(client):
    _seed_user("admin", ["admin"])
    _login_as(client, "admin")

    stats = client.get("/api/stats").json()
    assert stats["total"] == 0
    entries = client.get("/api/entries").json()
    assert entries == {
        "items": [],
        "next_cursor": None,
        "has_more": False,
        "total_count": 0,
    }


def test_stats_reflects_written_entries(client):
    _seed_user("admin", ["admin"])
    _login_as(client, "admin")
    from tests.test_ledger import _append_simple

    _append_simple(agent_id="agent:a")
    _append_simple(agent_id="agent:b")
    _append_simple(agent_id="agent:a")

    stats = client.get("/api/stats").json()
    assert stats["total"] == 3
    assert stats["unique_agents"] == 2
    assert stats["agents"]["agent:a"] == 2


def test_entry_detail_redacts_content_for_non_auditors(client):
    _seed_user("admin", ["admin"])
    _login_as(client, "admin")
    from tests.test_ledger import _append_simple

    e = _append_simple()
    detail = client.get(f"/api/entry/{e.entry_id}").json()

    # Admin is NOT an auditor — content must be redacted.
    assert detail["content_redacted"] is True
    assert detail["why_parsed"] == []
    assert detail["full_messages_parsed"] == []
    # Signature verification happens before redaction. In the enterprise edition
    # rows are signed so this stays True; the sandbox build is unsigned, so
    # there is no signature to validate and the field is None.
    assert detail["signature_valid"] is (True if HAS_SIGNING else None)


def test_entry_detail_exposes_content_for_auditors(client):
    _seed_user("admin", ["admin"])  # satisfy bootstrap gate
    _seed_user("auditor", ["viewer", "auditor"])
    _login_as(client, "auditor")
    from tests.test_ledger import _append_simple

    e = _append_simple(
        why_messages=[{"role": "user", "content": "secret prompt"}],
    )
    detail = client.get(f"/api/entry/{e.entry_id}").json()
    assert detail["content_redacted"] is False
    assert detail["why_parsed"][0]["content"] == "secret prompt"
