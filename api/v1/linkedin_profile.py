"""
LinkedIn profile-data scanner & downloadable PDF.

FILE: api/v1/linkedin_profile.py

Single POST endpoint that:
  1. Requires the LinkedIn live browser is running (we need the user's
     logged-in session cookies to see full profile data).
  2. Scrapes the profile dict via ``services.linkedin_profile_scraper``.
  3. Renders it as a styled PDF via ``services.profile_pdf``.
  4. Returns the PDF as a ``FileResponse`` for direct download.

Endpoint
--------
POST   /api/v1/linkedin/profile/scan
       body: { "profile_url": "https://www.linkedin.com/in/handle" }
       -> application/pdf  (attachment)
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.dependencies import get_current_user
from core.logging_config import get_logger
from models.user import User
from services.linkedin_live_browser import linkedin_live_browser
from services.linkedin_profile_scraper import scrape_profile
from services.profile_pdf import render_profile_pdf

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/linkedin/profile", tags=["linkedin-profile"])


class ProfileScanRequest(BaseModel):
    profile_url: str = Field(
        ...,
        min_length=8,
        max_length=400,
        description="Full LinkedIn profile URL (e.g. https://www.linkedin.com/in/username).",
    )


@router.post("/scan")
async def scan_profile(
    payload: ProfileScanRequest,
    _user: User = Depends(get_current_user),
) -> Response:
    if linkedin_live_browser.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "LinkedIn live chat must be running to scan profiles (the "
                "scraper uses the live browser's logged-in session). "
                "Call POST /api/v1/linkedin/live/start first."
            ),
        )

    try:
        data = await scrape_profile(payload.profile_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover — Playwright edge case
        logger.exception("Profile scan failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Scan failed: {exc}",
        ) from exc

    try:
        pdf_bytes = render_profile_pdf(data)
    except Exception as exc:  # pragma: no cover — reportlab edge case
        logger.exception("PDF render failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF render failed: {exc}",
        ) from exc

    # Filename derived from profile handle when possible.
    basename = "profile"
    try:
        last = payload.profile_url.rstrip("/").split("/")[-1]
        if last and "?" not in last and len(last) <= 64:
            basename = last
    except Exception:
        pass
    filename = f"{basename}-{int(time.time() * 1000)}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "Content-Length": str(len(pdf_bytes)),
        },
    )
