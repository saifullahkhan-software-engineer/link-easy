"""
LinkedIn-profile → PDF renderer.

FILE: services/profile_pdf.py

Takes the dict from ``services.linkedin_profile_scraper.scrape_profile``
and produces a styled PDF using the ``reportlab`` Platypus engine. The
layout is intentionally clean and section-based:

  ── Basics ──
  ── About ──
  ── Experience  (table)  ──
  ── Education  (table)  ──
  ── Skills ──

The renderer returns bytes ready to hand back as a FastAPI
``FileResponse`` (``application/pdf``).
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)


# Pixel-ish look — give the PDF enough whitespace to scan-print.
BRAND = colors.HexColor("#0a66c2")          # LinkedIn blue
INK   = colors.HexColor("#1d2226")
MUTED = colors.HexColor("#666666")
RULE  = colors.HexColor("#e6e6e6")

MARGIN_MM = 14


def render_profile_pdf(data: dict[str, Any]) -> bytes:
    """Render the scraper's profile dict to PDF bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN_MM * mm,
        rightMargin=MARGIN_MM * mm,
        topMargin=MARGIN_MM * mm,
        bottomMargin=MARGIN_MM * mm,
        title=_xml(data.get("basics", {}).get("name") or "LinkedIn Profile"),
        author="LinkEasy",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle(
        "h1", parent=styles["Heading1"],
        fontSize=12, leading=14,
        textColor=BRAND, spaceBefore=0, spaceAfter=2,
        fontName="Helvetica-Bold",
    )
    h2 = ParagraphStyle(
        "h2", parent=styles["Heading2"],
        fontSize=10, leading=12,
        textColor=INK, spaceBefore=10, spaceAfter=4,
        fontName="Helvetica-Bold",
    )
    body = ParagraphStyle(
        "body", parent=styles["BodyText"],
        fontSize=9.5, leading=13, textColor=INK, spaceAfter=4,
    )
    muted = ParagraphStyle(
        "muted", parent=body, textColor=MUTED, fontSize=8.5, leading=11,
    )
    cell = ParagraphStyle(
        "cell", parent=body, fontSize=9, leading=12,
    )
    cell_muted = ParagraphStyle(
        "cell_muted", parent=muted, fontSize=8.5,
    )

    story: list[Any] = []

    # ── Header (basics) ──
    basics = data.get("basics", {}) or {}
    story.append(Paragraph(_xml(basics.get("headline") or "LinkedIn profile"), muted))
    story.append(Paragraph(_xml(basics.get("name").strip() if basics.get("name") else "Unknown name"), h1))
    story.append(Paragraph(_xml(basics.get("location") or "Location unavailable"), muted))
    story.append(Paragraph("Source: " + _xml(basics.get("profile_url") or ""), muted))
    story.append(Spacer(1, 4 * mm))
    story.append(_rule())

    # ── About ──
    story.append(Paragraph("About", h2))
    about = data.get("about") or "Unavailable."
    story.append(Paragraph(_xml(about) if about else "Unavailable.", body))
    story.append(Spacer(1, 2 * mm))

    # ── Experience ──
    story.append(Paragraph("Experience", h2))
    experience = data.get("experience") or []
    if experience:
        rows = [
            [
                Paragraph("<b>Title</b>", body),
                Paragraph("<b>Company</b>", body),
                Paragraph("<b>Dates</b>", body),
                Paragraph("<b>Location</b>", body),
            ]
        ]
        for x in experience:
            rows.append(
                [
                    Paragraph(_xml(x.get("title") or "—"), cell),
                    Paragraph(_xml(x.get("company") or "—"), cell),
                    Paragraph(_xml(x.get("dates") or "—"), cell_muted),
                    Paragraph(_xml(x.get("location") or "—"), cell_muted),
                ]
            )
        story.append(_grid(rows, col_widths=[56 * mm, 56 * mm, 36 * mm, 34 * mm]))
    else:
        story.append(Paragraph("Unavailable.", body))
    story.append(Spacer(1, 2 * mm))

    # ── Education ──
    story.append(Paragraph("Education", h2))
    education = data.get("education") or []
    if education:
        rows = [
            [
                Paragraph("<b>School</b>", body),
                Paragraph("<b>Degree</b>", body),
                Paragraph("<b>Dates</b>", body),
            ]
        ]
        for x in education:
            rows.append(
                [
                    Paragraph(_xml(x.get("school") or "—"), cell),
                    Paragraph(_xml(x.get("degree") or "—"), cell),
                    Paragraph(_xml(x.get("dates") or "—"), cell_muted),
                ]
            )
        story.append(_grid(rows, col_widths=[72 * mm, 76 * mm, 34 * mm]))
    else:
        story.append(Paragraph("Unavailable.", body))
    story.append(Spacer(1, 2 * mm))

    # ── Skills ──
    story.append(Paragraph("Skills", h2))
    skills = data.get("skills") or []
    if skills:
        story.append(Paragraph(_xml(", ".join(skills)), body))
    else:
        story.append(Paragraph("Unavailable.", body))

    story.append(Spacer(1, 6 * mm))
    story.append(_rule())
    scraped_at = data.get("scraped_at")
    stamp = (
        datetime.fromtimestamp(scraped_at).strftime("%Y-%m-%d %H:%M:%S UTC")
        if isinstance(scraped_at, (int, float))
        else "unknown"
    )
    story.append(
        Paragraph(
            f"Generated by LinkEasy on {stamp}. Public LinkedIn data only; "
            "respect each profile's privacy and LinkedIn's terms of service.",
            muted,
        )
    )

    doc.build(story)
    return buf.getvalue()


# ── Tiny rendering helpers ───────────────────────────────────────────────

def _xml(s: str) -> str:
    """Escape for reportlab Paragraph (preserves \n)."""
    if not s:
        return "—"
    out = (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return out.replace("\n", "<br/>")


def _rule() -> Any:
    """A single-row table that renders as a thin separator."""
    t = Table([[""]], colWidths=[0])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE)]))
    return t


def _grid(rows: list[list[Any]], col_widths: list[float]) -> Table:
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9.5),
                ("TEXTCOLOR", (0, 0), (-1, 0), INK),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, BRAND),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TEXTCOLOR", (0, 1), (-1, -1), INK),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfc")]),
            ]
        )
    )
    return t
