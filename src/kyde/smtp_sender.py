"""
SMTP send path for DLP alert notifications.

Called from `notifications._poll_once()` for live alerts and from the
`POST /api/settings/smtp/test` handler for test-send clicks. No database
access happens here — callers pass already-loaded rows. Keeps this module
pure and easy to unit-test.

Three encryption modes (matching the SMTP_ENCRYPTION setting):

  - `tls`      — implicit TLS from the first byte (SMTPS, port 465).
  - `starttls` — plaintext connect, then STARTTLS upgrade (submission, 587).
  - `none`     — plaintext (port 25; test/dev only).
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Optional

import aiosmtplib

from . import crypto, settings


@dataclass(frozen=True)
class SmtpConfig:
    """Plaintext SMTP config snapshot. Build once per send; never log it."""

    host: str
    port: int
    encryption: str  # "none" | "starttls" | "tls"
    username: str
    password: str
    from_address: str
    from_name: str
    reply_to: str
    tls_verify: bool
    timeout_seconds: int


def load_smtp_config() -> SmtpConfig:
    """Read every SMTP_* setting and decrypt the password. Raises a clear
    ValueError if required fields are missing or the password fails to
    decrypt (typically: the AES key was rotated or deleted)."""
    host = (settings.get("SMTP_HOST") or "").strip()
    from_address = (settings.get("SMTP_FROM_ADDRESS") or "").strip()
    if not host:
        raise ValueError("SMTP_HOST is not configured")
    if not from_address:
        raise ValueError("SMTP_FROM_ADDRESS is not configured")

    enc_blob = settings.get("SMTP_PASSWORD_ENC") or ""
    password = ""
    if enc_blob:
        try:
            password = crypto.decrypt(enc_blob)
        except Exception as e:
            raise ValueError(
                f"SMTP password could not be decrypted — AES key may have "
                f"changed. Re-enter the password in settings. ({e})"
            ) from e

    return SmtpConfig(
        host=host,
        port=int(settings.get("SMTP_PORT")),
        encryption=str(settings.get("SMTP_ENCRYPTION")),
        username=(settings.get("SMTP_USERNAME") or "").strip(),
        password=password,
        from_address=from_address,
        from_name=(settings.get("SMTP_FROM_NAME") or "").strip()
        or "Kyde Gateway Alerts",
        reply_to=(settings.get("SMTP_REPLY_TO") or "").strip(),
        tls_verify=bool(settings.get("SMTP_TLS_VERIFY")),
        timeout_seconds=int(settings.get("SMTP_TIMEOUT_SECONDS")),
    )


def _tls_context(verify: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def send_email(
    cfg: SmtpConfig,
    recipients: list[str],
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> None:
    """Send a single message to all recipients. Raises on any failure."""
    if not recipients:
        raise ValueError("no recipients")

    msg = EmailMessage()
    msg["From"] = formataddr((cfg.from_name, cfg.from_address))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    msg["Message-ID"] = make_msgid(domain=cfg.from_address.split("@", 1)[-1] or "kyde")
    if cfg.reply_to:
        msg["Reply-To"] = cfg.reply_to
    for k, v in (extra_headers or {}).items():
        msg[k] = v

    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    tls_ctx = _tls_context(cfg.tls_verify)
    use_tls = cfg.encryption == "tls"
    start_tls = cfg.encryption == "starttls"

    await aiosmtplib.send(
        msg,
        hostname=cfg.host,
        port=cfg.port,
        username=cfg.username or None,
        password=cfg.password or None,
        use_tls=use_tls,
        start_tls=start_tls,
        tls_context=tls_ctx if (use_tls or start_tls) else None,
        timeout=cfg.timeout_seconds,
    )


# ---------------------------------------------------------------------------
# Alert email template
# ---------------------------------------------------------------------------


def _dashboard_url() -> str:
    """Build the base dashboard URL from PUBLIC_PROTOCOL + PUBLIC_HOSTNAME.

    Deliberately does NOT append PUBLIC_PORT: that setting holds the
    *gateway* port (4000 by default — where agents POST /v1/* requests),
    not the admin UI's port. Production deployments terminate TLS at a
    reverse proxy on 443; auditors clicking through an alert email
    expect to land on the UI's standard host, not the proxy's internal
    port. No trailing slash.
    """
    proto = (settings.get("PUBLIC_PROTOCOL") or "http").strip() or "http"
    host = (settings.get("PUBLIC_HOSTNAME") or "localhost").strip() or "localhost"
    return f"{proto}://{host}"


def _findings_summary(scanner: str, findings: list[dict]) -> str:
    """One-line human-readable summary of what the scanner matched."""
    if scanner == "regex":
        bits = []
        for m in findings or []:
            etype = m.get("entity_type") or m.get("pattern_name") or "match"
            bits.append(str(etype))
        if not bits:
            return "regex match"
        return ", ".join(sorted(set(bits)))
    if scanner == "bert":
        label = (findings[0].get("label") if findings else "") or "classifier hit"
        return str(label)
    return scanner


def build_alert_subject(alert: dict) -> str:
    scanner = alert.get("scanner", "?")
    score = float(alert.get("score", 0.0) or 0.0)
    summary = _findings_summary(scanner, alert.get("findings") or [])
    return f"[Kyde Alert] {scanner} — {summary} (score {score:.2f})"


def build_alert_bodies(alert: dict) -> tuple[str, str]:
    """Return (text_body, html_body) for an alert row."""
    alert_id = alert.get("alert_id", "")
    entry_id = alert.get("entry_id", "")
    session_id = alert.get("session_id", "")
    scanner = alert.get("scanner", "?")
    score = float(alert.get("score", 0.0) or 0.0)
    seen_count = int(alert.get("seen_count", 1) or 1)
    last_seen = alert.get("last_seen_entry_id") or entry_id
    created_at = alert.get("created_at") or 0.0
    when = datetime.fromtimestamp(float(created_at), tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    summary = _findings_summary(scanner, alert.get("findings") or [])
    link = f"{_dashboard_url()}/alerts/{alert_id}"

    text = (
        "A DLP alert was raised on the Kyde Gateway.\n\n"
        f"  Scanner       : {scanner}\n"
        f"  Finding       : {summary}\n"
        f"  Score         : {score:.3f}\n"
        f"  First seen    : {when}\n"
        f"  Seen count    : {seen_count}\n"
        f"  First entry   : {entry_id}\n"
        f"  Last entry    : {last_seen}\n"
        f"  Session       : {session_id or '(none)'}\n"
        f"  Alert ID      : {alert_id}\n\n"
        f"Open this alert in the dashboard to review and classify it:\n"
        f"  {link}\n\n"
        "You are receiving this because your account has the 'auditor' "
        "role on this gateway.\n"
    )

    # Minimal HTML — no CSS frameworks, just inline styles that render
    # acceptably in any mail client.
    html = f"""\
<html><body style="font-family: -apple-system, system-ui, sans-serif;
                   color:#111; line-height:1.5;">
  <h2 style="margin:0 0 12px 0;">Kyde Gateway — DLP alert</h2>
  <p style="margin:0 0 16px 0; color:#444;">
    {summary} detected by <b>{scanner}</b> at score <b>{score:.3f}</b>.
  </p>
  <table cellpadding="4" cellspacing="0" border="0"
         style="border-collapse:collapse; font-size:14px;">
    <tr><td style="color:#666;">First seen</td><td>{when}</td></tr>
    <tr><td style="color:#666;">Seen count</td><td>{seen_count}</td></tr>
    <tr><td style="color:#666;">First entry</td><td><code>{entry_id}</code></td></tr>
    <tr><td style="color:#666;">Last entry</td><td><code>{last_seen}</code></td></tr>
    <tr><td style="color:#666;">Session</td><td><code>{session_id or "(none)"}</code></td></tr>
    <tr><td style="color:#666;">Alert ID</td><td><code>{alert_id}</code></td></tr>
  </table>
  <p style="margin:18px 0;">
    <a href="{link}" style="background:#111; color:#fff;
            padding:8px 14px; border-radius:4px; text-decoration:none;">
      Review in dashboard
    </a>
  </p>
  <p style="font-size:12px; color:#888;">
    You are receiving this because your account has the 'auditor' role on this gateway.
  </p>
</body></html>
"""
    return text, html


async def send_alert_email(cfg: SmtpConfig, recipients: list[str], alert: dict) -> None:
    """Render and send one alert email. Raises on any failure."""
    subject = build_alert_subject(alert)
    text_body, html_body = build_alert_bodies(alert)
    # Stable threading anchor so mail clients group dedup repeats
    # together once `every_scan` policy is ever used.
    thread_anchor = f"<alert-{alert.get('alert_id', '')}@kyde>"
    await send_email(
        cfg,
        recipients=recipients,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        extra_headers={"References": thread_anchor, "In-Reply-To": thread_anchor},
    )


async def send_test_email(cfg: SmtpConfig, recipients: list[str]) -> None:
    """Canned diagnostic email used by the 'Send test email' admin button."""
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    text = (
        "This is a Kyde Gateway SMTP test message.\n\n"
        f"  Time         : {when}\n"
        f"  From         : {cfg.from_address}\n"
        f"  Host / Port  : {cfg.host}:{cfg.port}\n"
        f"  Encryption   : {cfg.encryption}\n"
        f"  TLS verify   : {cfg.tls_verify}\n"
        f"  Recipients   : {len(recipients)}\n\n"
        "If you received this, SMTP delivery to auditors is working.\n"
    )
    html = f"""\
<html><body style="font-family:-apple-system,system-ui,sans-serif;">
  <h3 style="margin:0 0 10px 0;">Kyde Gateway — SMTP test</h3>
  <p>Delivery to <b>{len(recipients)}</b> auditor recipient(s) succeeded.</p>
  <p style="color:#666; font-size:13px;">Sent {when} via {cfg.host}:{cfg.port} ({cfg.encryption}).</p>
</body></html>
"""
    await send_email(
        cfg,
        recipients=recipients,
        subject="[Kyde] SMTP test",
        text_body=text,
        html_body=html,
    )
