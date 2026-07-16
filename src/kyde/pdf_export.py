"""
Self-verifying PDF export.

Every exported PDF embeds a final "Cryptographic verification" block that
contains:
  - the SHA-256 hash of the PDF content *as rendered without the block*
  - an Ed25519 (or TPM ECDSA) signature over that hash
  - the public-key fingerprint so the receiver knows which key signed
  - a generation timestamp + ledger version

A small offline verifier (`scripts/verify_pdf.py`, future) can re-hash the
PDF up to the signature line, look up the public key by fingerprint, and
confirm authenticity. Storing the hash + signature on the rendered page —
not in PDF metadata — makes the audit value visible to anyone opening the
file in a viewer.

This module deliberately stays narrow on report shapes:
  - compliance_report  : a one-page Compliance summary
  - audit_log          : multi-page entry table from /api/entries
  - compliance_evidence: a single session / alert / chain snapshot
  - incident_report    : agent-chain export for a single incident

Each shape takes a context dict and returns `bytes`.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from . import _features


@dataclass
class PdfBundle:
    """Result of an export: the PDF bytes + the signature surface attached
    to the last page. Callers can stream `pdf` straight to the client."""

    pdf: bytes
    sha256_hex: str
    signature_b64: str
    public_key_fingerprint: str


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------


def _styles():
    base = getSampleStyleSheet()
    base.add(
        ParagraphStyle(
            name="KydeMono",
            parent=base["Code"],
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#444"),
        )
    )
    base.add(
        ParagraphStyle(
            name="KydeMeta",
            parent=base["BodyText"],
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#666"),
        )
    )
    return base


# ---------------------------------------------------------------------------
# Verification block — added to the last page of every export
# ---------------------------------------------------------------------------


def _render_verification_block(
    story: list, sha256_hex: str, signature_b64: str, fingerprint: str, alg: str
) -> None:
    styles = _styles()
    story.append(Spacer(1, 6 * mm))
    story.append(
        Paragraph(
            "<b>Cryptographic verification</b>",
            styles["BodyText"],
        )
    )
    rows = [
        ["Algorithm", alg],
        ["Public key", fingerprint],
        ["Document SHA-256", _wrap(sha256_hex, 64)],
        ["Signature (base64)", _wrap(signature_b64, 64)],
        ["Generated at (UTC)", datetime.now(timezone.utc).isoformat()],
    ]
    tbl = Table(rows, colWidths=[40 * mm, 130 * mm])
    tbl.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Courier", 8),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#ccc")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#eee")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(tbl)


def _wrap(s: str, width: int) -> str:
    """Insert zero-width-ish breaks so long hash/sig strings wrap in a table cell."""
    return "<br/>".join(s[i : i + width] for i in range(0, len(s), width))


def _sign_pdf(pdf_without_block: bytes) -> tuple[str, str, str, str]:
    """Compute the hash + signature for the pre-block PDF bytes.

    The signature is over `sha256(pdf_without_block)`, packaged as
    `{"v": 1, "sha256": <hex>}` so the future verifier doesn't have to
    re-derive any framing convention.
    """
    sha = hashlib.sha256(pdf_without_block).hexdigest()
    # Starter edition: signing is absent. The report still carries the
    # content hash (tamper-evidence), but no independent signature.
    if not _features.HAS_SIGNING:
        return sha, "", "(signing disabled — starter edition)", "unsigned"
    payload = {"v": 1, "sha256": sha}
    sig_b64 = _features.signing.sign_payload(payload)
    try:
        fp = _features.signing.public_key_fingerprint()
    except FileNotFoundError:
        fp = "(no public key on file)"
    alg = "ECDSA-P256 (TPM)" if _features.signing._TPM_AVAILABLE else "Ed25519"
    return sha, sig_b64, fp, alg


# ---------------------------------------------------------------------------
# Render pipeline
# ---------------------------------------------------------------------------


def _render(title: str, body_builder) -> PdfBundle:
    """Two-pass render: build the PDF without the verification block to get
    the bytes to sign, then re-render with the block appended.

    The signature is over the pre-block bytes (not the final PDF), so the
    verifier can re-render the pre-block content from the embedded data
    and reach the same hash. We keep it simple by re-running the body
    builder twice.
    """
    # Pass 1: render without the verification block to obtain the hash.
    buf1 = io.BytesIO()
    doc1 = SimpleDocTemplate(
        buf1,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=title,
    )
    story1: list[Any] = []
    body_builder(story1)
    doc1.build(story1)
    sha, sig_b64, fp, alg = _sign_pdf(buf1.getvalue())

    # Pass 2: render again with the verification block appended.
    buf2 = io.BytesIO()
    doc2 = SimpleDocTemplate(
        buf2,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=title,
    )
    story2: list[Any] = []
    body_builder(story2)
    _render_verification_block(story2, sha, sig_b64, fp, alg)
    doc2.build(story2)

    return PdfBundle(
        pdf=buf2.getvalue(),
        sha256_hex=sha,
        signature_b64=sig_b64,
        public_key_fingerprint=fp,
    )


# ---------------------------------------------------------------------------
# Public report shapes
# ---------------------------------------------------------------------------


def compliance_report(ctx: dict) -> PdfBundle:
    """One-page compliance overview. ctx fields:
    - status: 'COMPLIANT' / 'NON_COMPLIANT'
    - total_entries, chain_intact (bool), signature_failures
    - public_key_fingerprint, signature_alg
    - regulatory_mappings: [{framework, articles[]}]
    """
    styles = _styles()

    def build(story: list):
        story.append(Paragraph("Compliance report", styles["Title"]))
        story.append(
            Paragraph(
                f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
                styles["KydeMeta"],
            )
        )
        story.append(Spacer(1, 4 * mm))

        # Status
        status = ctx.get("status", "UNKNOWN")
        color = "#0a0" if status == "COMPLIANT" else "#c00"
        story.append(
            Paragraph(
                f"<font color='{color}' size='14'><b>Status: {status}</b></font>",
                styles["BodyText"],
            )
        )
        story.append(Spacer(1, 4 * mm))

        # Chain integrity
        rows = [
            ["Ledger entries", str(ctx.get("total_entries", "—"))],
            ["Chain intact", "yes" if ctx.get("chain_intact") else "no"],
            ["Signature failures", str(ctx.get("signature_failures", 0))],
            ["Signing algorithm", str(ctx.get("signature_alg", "—"))],
            ["Public key", str(ctx.get("public_key_fingerprint", "—"))],
        ]
        tbl = Table(rows, colWidths=[50 * mm, 120 * mm])
        tbl.setStyle(_kv_style())
        story.append(tbl)
        story.append(Spacer(1, 6 * mm))

        story.append(Paragraph("<b>Regulatory mappings</b>", styles["BodyText"]))
        for framework in ctx.get("regulatory_mappings", []):
            story.append(
                Paragraph(
                    f"<b>{framework['framework']}</b>",
                    styles["BodyText"],
                )
            )
            for article in framework.get("articles", []):
                story.append(Paragraph(f"&nbsp;&nbsp;• {article}", styles["BodyText"]))
            story.append(Spacer(1, 2 * mm))

    return _render("Compliance report", build)


def audit_log(ctx: dict) -> PdfBundle:
    """Multi-page table of audit entries. ctx fields:
    - filters: dict (rendered in header)
    - entries: list of dicts with keys
          seq, dt, agent_id, action_type, model, upstream
    - total_count: int (for the header subtitle)
    """
    styles = _styles()
    entries = ctx.get("entries", [])

    def build(story: list):
        story.append(Paragraph("Audit log", styles["Title"]))
        story.append(
            Paragraph(
                f"{len(entries):,} entries (of {ctx.get('total_count', len(entries)):,})",
                styles["KydeMeta"],
            )
        )
        filters = ctx.get("filters") or {}
        if filters:
            story.append(
                Paragraph(
                    "Filters: "
                    + ", ".join(f"{k}={v}" for k, v in filters.items() if v),
                    styles["KydeMeta"],
                )
            )
        story.append(Spacer(1, 4 * mm))

        header = ["Seq", "Time (UTC)", "Agent", "Action", "Model", "Provider"]
        rows = [header]
        for e in entries:
            rows.append(
                [
                    str(e.get("seq", "")),
                    str(e.get("dt", ""))[:19],
                    str(e.get("agent_id", ""))[:30],
                    str(e.get("action_type", "")),
                    str(e.get("model", ""))[:25],
                    str(e.get("upstream", ""))[:20],
                ]
            )
        tbl = Table(
            rows,
            colWidths=[15 * mm, 32 * mm, 40 * mm, 22 * mm, 35 * mm, 26 * mm],
            repeatRows=1,
        )
        tbl.setStyle(_table_style())
        story.append(tbl)

    return _render("Audit log", build)


def compliance_evidence(ctx: dict) -> PdfBundle:
    """A single-session or single-alert evidence snapshot. ctx fields:
    - title: header text
    - subject: short summary line (e.g. session id or alert id)
    - rows: [(label, value)] for the evidence body
    - entries: optional list of detail rows (same shape as audit_log)
    """
    styles = _styles()

    def build(story: list):
        story.append(
            Paragraph(ctx.get("title", "Compliance evidence"), styles["Title"])
        )
        if ctx.get("subject"):
            story.append(Paragraph(ctx["subject"], styles["KydeMeta"]))
        story.append(Spacer(1, 4 * mm))

        rows = [[str(k), str(v)] for k, v in ctx.get("rows", [])]
        if rows:
            tbl = Table(rows, colWidths=[50 * mm, 120 * mm])
            tbl.setStyle(_kv_style())
            story.append(tbl)
            story.append(Spacer(1, 4 * mm))

        entries = ctx.get("entries") or []
        if entries:
            story.append(Paragraph("<b>Entry detail</b>", styles["BodyText"]))
            header = ["Seq", "Time", "Action", "Model"]
            tbl = Table(
                [header]
                + [
                    [
                        str(e.get("seq", "")),
                        str(e.get("dt", ""))[:19],
                        str(e.get("action_type", "")),
                        str(e.get("model", ""))[:30],
                    ]
                    for e in entries
                ],
                colWidths=[15 * mm, 35 * mm, 25 * mm, 95 * mm],
                repeatRows=1,
            )
            tbl.setStyle(_table_style())
            story.append(tbl)

    return _render(ctx.get("title", "Compliance evidence"), build)


def incident_report(ctx: dict) -> PdfBundle:
    """Per-chain incident export. ctx fields:
    - chain_label, status (BLOCKED/PREVENTED/COMPLETED), incident_serial
    - steps: list of {label, status, agent_id, dt}
    - notes: optional free text
    """
    styles = _styles()

    def build(story: list):
        story.append(
            Paragraph(
                f"Incident report — {ctx.get('chain_label', '?')}", styles["Title"]
            )
        )
        story.append(
            Paragraph(
                f"Status: {ctx.get('status', 'UNKNOWN')}  ·  Serial: {ctx.get('incident_serial', '—')}",
                styles["KydeMeta"],
            )
        )
        story.append(Spacer(1, 4 * mm))

        steps = ctx.get("steps", [])
        rows = [["#", "Step", "Status", "Agent", "Time"]]
        for i, s in enumerate(steps, 1):
            rows.append(
                [
                    str(i),
                    str(s.get("label", ""))[:50],
                    str(s.get("status", "")),
                    str(s.get("agent_id", ""))[:30],
                    str(s.get("dt", ""))[:19],
                ]
            )
        tbl = Table(
            rows, colWidths=[8 * mm, 60 * mm, 25 * mm, 45 * mm, 35 * mm], repeatRows=1
        )
        tbl.setStyle(_table_style())
        story.append(tbl)

        if ctx.get("notes"):
            story.append(Spacer(1, 6 * mm))
            story.append(Paragraph("<b>Notes</b>", styles["BodyText"]))
            story.append(Paragraph(str(ctx["notes"]), styles["BodyText"]))

    return _render("Incident report", build)


# ---------------------------------------------------------------------------
# Shared table styles
# ---------------------------------------------------------------------------


def _kv_style() -> TableStyle:
    return TableStyle(
        [
            ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#ccc")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#eee")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
    )


def _table_style() -> TableStyle:
    return TableStyle(
        [
            ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eee")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#ccc")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#eee")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
    )
