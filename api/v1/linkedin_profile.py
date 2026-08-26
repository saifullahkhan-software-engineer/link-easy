"""LinkedIn profile scan endpoint with a preview-first JSON contract."""
from __future__ import annotations

import base64
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, HttpUrl

from api.dependencies import get_current_user
from api.rate_limit_deps import rate_limit
from api.v1.linkedin import require_linkedin_enabled
from core.logging_config import get_logger
from models.user import User
from services.linkedin_live_browser import linkedin_live_browser
from services.linkedin_profile_scraper import scrape_profile
from services.profile_pdf import render_profile_pdf

logger = get_logger(__name__)
# Profile scanning opens a LinkedIn browser session, so it is gated by the
# same availability flag as account connect. Returns 503 while disabled.
router = APIRouter(
    prefix="/api/v1/linkedin/profile",
    tags=["linkedin-profile"],
    dependencies=[Depends(require_linkedin_enabled)],
)


class ProfileScanRequest(BaseModel):
    profile_url: HttpUrl


class ProfileScanResponse(BaseModel):
    """Structured preview plus the exact PDF generated from that preview."""

    report: dict[str, Any]
    filename: str
    pdf_base64: str


def _pdf_filename(report: dict[str, Any]) -> str:
    name = str((report.get("basics") or {}).get("name") or "linkedin-profile")
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-.").lower()
    return f"{slug or 'linkedin-profile'}-scan.pdf"


@router.post(
    "/scan",
    response_model=ProfileScanResponse,
    dependencies=[Depends(rate_limit("profile:scan"))],
)
async def scan_profile_pdf(
    payload: ProfileScanRequest,
    current_user: User = Depends(get_current_user),
) -> ProfileScanResponse:
    """Scrape once, return a visible report and its downloadable PDF bytes.

    The endpoint starts a temporary LinkedIn browser when live chat is not
    already running. If live chat is active, the manager's operation lock
    pauses polling while the profile is visited and restores the exact thread
    URL afterward.
    """
    started_here = False
    if linkedin_live_browser.status != "running":
        start_result = await linkedin_live_browser.start(current_user.email)
        if start_result.get("status") != "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=start_result.get("message")
                or "Could not start the connected LinkedIn account.",
            )
        started_here = True
    elif not linkedin_live_browser.is_owned_by(current_user.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A different account is currently using the LinkedIn browser.",
        )

    try:
        report = await scrape_profile(str(payload.profile_url))
        pdf_bytes = render_profile_pdf(report)
        if not pdf_bytes.startswith(b"%PDF-"):
            raise RuntimeError("PDF generation returned an invalid document")
        return ProfileScanResponse(
            report=report,
            filename=_pdf_filename(report),
            pdf_base64=base64.b64encode(pdf_bytes).decode("ascii"),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("LinkedIn profile scan failed")
        detail = str(exc).strip() or exc.__class__.__name__
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Profile scan failed: {detail}",
        ) from exc
    finally:
        if started_here:
            await linkedin_live_browser.stop()
