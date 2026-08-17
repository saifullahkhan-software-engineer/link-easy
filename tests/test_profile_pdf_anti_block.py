"""Smoke tests for the new profile-PDF + 10s anti-block knobs.

These are pure-Python (no Playwright / no live browser) so the suite
stays fast and deterministic.
"""
import io

from reportlab.pdfgen import canvas  # noqa: F401  (pulled in via profile_pdf)

from services.profile_pdf import render_profile_pdf


SAMPLE_PROFILE = {
    "basics": {
        "name": "Ada Lovelace",
        "headline": "Computer pioneer, numerical analysis",
        "location": "London, UK",
        "current_position": "First programmer",
        "profile_url": "https://www.linkedin.com/in/ada-lovelace",
    },
    "about": (
        "Mathematician and writer; worked on analytical engines, "
        "algorithms, and mechanical computation in the 19th century."
    )[:2600],
    "experience": [
        {"title": "Computer Scientist", "company": "Analytical Engine Co.",
         "dates": "1842 – 1851", "location": "London"},
        {"title": "Mathematician", "company": "Self",
         "dates": "1835 – 1842", "location": "Remote"},
    ],
    "education": [
        {"school": "Self-Taught", "degree": "Mathematics", "dates": "1830 – 1835"},
    ],
    "skills": ["Mathematics", "Algorithm Design", "Analytical Engines"],
    "scraped_at": 1717000000.0,
    "source_url": "https://www.linkedin.com/in/ada-lovelace",
}


def test_render_profile_pdf_returns_non_empty_pdf_bytes():
    pdf = render_profile_pdf(SAMPLE_PROFILE)
    assert isinstance(pdf, (bytes, bytearray))
    assert len(pdf) > 1_000, f"PDF unexpectedly small: {len(pdf)} bytes"
    # PDF magic header "%PDF-".
    assert pdf.startswith(b"%PDF-")


def test_render_profile_pdf_keeps_text_intact():
    pdf = render_profile_pdf(SAMPLE_PROFILE)
    # The PDF body is zlib-compressed by default — decode and grep for a
    # known fragment to confirm the renderer wrote the basics out.
    import zlib

    text = bytes(pdf)
    needle = b"Ada Lovelace"
    hit = needle in text
    if not hit:
        # Look for the marker inside the xref stream (compressed body)
        for chunk in text.split(b"\nstream"):
            try:
                inflated = zlib.decompress(chunk)
            except zlib.error:
                continue
            if needle in inflated:
                hit = True
                break
    assert hit, "PDF body did not include the profile name anywhere"


def test_render_handles_missing_sections():
    # All sections empty / missing → still produces a valid PDF.
    minimal = {"basics": {"name": "", "headline": "", "location": "", "profile_url": ""},
               "about": "", "experience": [], "education": [], "skills": []}
    pdf = render_profile_pdf(minimal)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 800


def test_linkedin_live_pacing_constant_default():
    """Mirror of the scanner test — pacing defaults to 10s unless overridden."""
    from services.linkedin_live_browser import LIVE_SEND_DELAY_SECONDS
    # The config knob lives in settings; we just sanity-check that the
    # module-level constant reads a numeric ≥ 1s.
    assert isinstance(LIVE_SEND_DELAY_SECONDS, float)
    assert LIVE_SEND_DELAY_SECONDS >= 1.0


def test_fastapi_smoke_for_new_routes():
    """Routes are registered in app and respond (RFC-friendly) to a\n    no-auth probe. Identical contract to the WhatsApp live smoke."""
    from starlette.testclient import TestClient

    from main import app

    client = TestClient(app, raise_server_exceptions=False)
    expected_401 = {
        "GET  /api/v1/linkedin/live/status":                401,
        "POST /api/v1/linkedin/live/start":                401,
        "POST /api/v1/linkedin/live/stop":                 401,
        "GET  /api/v1/linkedin/live/chats":                401,
        "POST /api/v1/linkedin/live/chats/open":           401,
        "POST /api/v1/linkedin/live/chats/close":          401,
        "GET  /api/v1/linkedin/live/messages":             401,
        "POST /api/v1/linkedin/live/messages/send":        401,
        "POST /api/v1/linkedin/profile/scan":              401,
    }
    # POST routes take JSON; supply empty payload so validators don't
    # intercept before auth.
    for spec, expected in expected_401.items():
        m, p = spec.split()
        url = p
        if m == "POST":
            r = client.post(url, json={"chat_id": "x"})
        else:
            r = client.get(url)
        assert r.status_code == expected, f"{spec} → {r.status_code} (want {expected})"
