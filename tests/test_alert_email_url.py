"""Pin the alert-email deep-link URL shape.

`_dashboard_url()` used to splice PUBLIC_PORT in, but that setting
holds the *gateway* port (4000 default) — what agents POST /v1/* at —
not the UI's port. Auditors clicking through the email would land on
:4000 and get a JSON 404 instead of the UI. Fix: never include the
port; rely on PUBLIC_PROTOCOL + PUBLIC_HOSTNAME and the standard 80/443
the reverse proxy serves the UI on.

These tests lock the shape so a future PUBLIC_PORT consumer doesn't
quietly creep back in here.
"""

from __future__ import annotations

import pytest

from kyde import smtp_sender


@pytest.fixture
def fake_settings(monkeypatch):
    """Override smtp_sender's settings lookup with a plain dict. Avoids
    a DB roundtrip for what's a pure URL-building test."""
    store: dict = {}

    def fake_get(key: str):
        return store.get(key)

    monkeypatch.setattr(smtp_sender.settings, "get", fake_get)
    return store


def test_dashboard_url_omits_port_even_when_public_port_set(fake_settings):
    fake_settings["PUBLIC_PROTOCOL"] = "https"
    fake_settings["PUBLIC_HOSTNAME"] = "kyde.example.com"
    fake_settings["PUBLIC_PORT"] = "4000"  # the gateway port, not the UI
    assert smtp_sender._dashboard_url() == "https://kyde.example.com"


def test_dashboard_url_omits_port_for_http_too(fake_settings):
    fake_settings["PUBLIC_PROTOCOL"] = "http"
    fake_settings["PUBLIC_HOSTNAME"] = "internal.kyde.dev"
    fake_settings["PUBLIC_PORT"] = "8080"
    assert smtp_sender._dashboard_url() == "http://internal.kyde.dev"


def test_dashboard_url_default_when_settings_empty(fake_settings):
    assert smtp_sender._dashboard_url() == "http://localhost"


def test_alert_email_link_uses_portless_dashboard_url(fake_settings):
    fake_settings["PUBLIC_PROTOCOL"] = "https"
    fake_settings["PUBLIC_HOSTNAME"] = "kyde.example.com"
    fake_settings["PUBLIC_PORT"] = "4000"

    text, html = smtp_sender.build_alert_bodies(
        {
            "alert_id": "alert-abc",
            "entry_id": "entry-xyz",
            "session_id": "s-1",
            "scanner": "regex",
            "score": 0.9,
            "seen_count": 1,
            "last_seen_entry_id": "entry-xyz",
            "created_at": 0.0,
            "findings": [{"entity_type": "email"}],
        }
    )

    expected_link = "https://kyde.example.com/alerts/alert-abc"
    assert expected_link in text
    assert expected_link in html
    # And the gateway port must not leak into the link.
    assert ":4000" not in text
    assert ":4000" not in html
