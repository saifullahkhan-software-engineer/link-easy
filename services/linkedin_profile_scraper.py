"""
LinkedIn profile data scraper.

FILE: services/linkedin_profile_scraper.py

Reuses the running :class:`LinkedInLiveBrowser` page so the user's
already-logged-in session is honoured (no fresh login flow needed).
Navigates to the supplied profile URL, scrapes the labelled sections,
and returns a structured dict. The caller (``profile_pdf.py``) is
responsible for rendering that dict into a PDF.

Sections are stable, public surface:
  - basics:    name, headline, location, current position, profile URL
  - about:     LinkedIn "About" snippet, 2.6 kB max
  - experience: title, company, dates, location, duration per row
  - education:  school, degree, dates
  - skills:     top skill endorsements

The scraper never throws on missing fields; the public
``scrape_profile`` always yields a dict with the section keys present
(empty list / empty string means the field was unavailable).
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import urlparse

from core.logging_config import get_logger

from services.linkedin_live_browser import linkedin_live_browser

logger = get_logger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────

async def _safe_text(locator) -> str:
    """Read ``innerText`` from one locator handle, never raises."""
    if locator is None:
        return ""
    try:
        return (await locator.inner_text()) or ""
    except Exception:
        return ""


async def _safe_attr(locator, name: str) -> str:
    if locator is None:
        return ""
    try:
        return (await locator.get_attribute(name)) or ""
    except Exception:
        return ""


# ── Public entry point ───────────────────────────────────────────────────

async def scrape_profile(profile_url: str) -> dict[str, Any]:
    """Open ``profile_url`` in the live browser, scrape, return a dict.

    The live browser must be running (the user has to be logged in); we
    reuse its Chromium context so there's no fresh login flow. The page is
    parked back at the messaging thread list when finished.
    """
    page = await linkedin_live_browser._require_page()
    original_url = page.url

    parsed = urlparse(profile_url)
    if "linkedin.com" not in (parsed.netloc or ""):
        raise ValueError(f"profile_url is not a linkedin.com URL: {profile_url}")

    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        # LinkedIn renders the profile progressively; the headline name
        # and "About" appear quickly but right-rail widgets take longer.
        await asyncio.sleep(2.0)
    except Exception as exc:
        raise RuntimeError(f"Could not navigate to profile URL: {exc}") from exc

    try:
        data: dict[str, Any] = {
            "basics":    await _scrape_basics(page, profile_url),
            "about":     await _scrape_about(page),
            "experience": await _scrape_experience(page),
            "education": await _scrape_education(page),
            "skills":    await _scrape_skills(page),
            "scraped_at": time.time(),
            "source_url": profile_url,
        }
    finally:
        # Park the page back on the messaging thread list so the live
        # chat picks up where the user left it.
        try:
            await page.goto("https://www.linkedin.com/messaging/", wait_until="domcontentloaded", timeout=15000)
        except Exception:
            try:
                await page.goto(original_url or "https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            except Exception:
                pass

    return data


# ── Section scrapers (best-effort) ─────────────────────────────────────────

async def _scrape_basics(page, requested_url: str) -> dict[str, Any]:
    basics: dict[str, Any] = {"name": "", "headline": "", "location": "", "current_position": ""}
    # Name — top-of-card h1.
    name_el = await page.query_selector("h1.text-heading-xlarge, h1[class*='top-card']")
    if name_el is not None:
        basics["name"] = (await _safe_text(name_el)).strip()
    # Headline.
    headline_el = await page.query_selector(".text-body-medium.break-words, [data-generated-suggestion-target='headline']")
    if headline_el is not None:
        basics["headline"] = (await _safe_text(headline_el)).strip()
    # Location.
    loc_el = await page.query_selector(
        ".pv-top-card .text-body-small.inline.t-black--light.break-words, "
        "span.text-body-small.inline.t-black--light.break-words"
    )
    if loc_el is not None:
        basics["location"] = (await _safe_text(loc_el)).strip()
    # Current position — best signal is the first experience row's title.
    basics["profile_url"] = page.url or requested_url
    return basics


async def _scrape_about(page) -> str:
    about_el = await page.query_selector(
        "section.summary .display-flex, "
        "section[data-section='summary'] .pv-shared-text-with-see-more span[aria-hidden='true'], "
        "div.inline-show-more-text span.visually-hidden"
    )
    if about_el is None:
        # Fallback: the about summary section's first paragraph.
        about_el = await page.query_selector(
            "section.summary p, "
            "section[data-section='summary'] p, "
            ".pv-about__summary-text"
        )
    if about_el is None:
        return ""
    txt = (await _safe_text(about_el)).strip()
    return _collapse_whitespace(txt)[:2_600]


async def _scrape_experience(page) -> list[dict]:
    """Each row is ``[data-section='experience'] li`` or ``section.experience-section li``."""
    rows = await page.query_selector_all(
        "section.experience-section ul > li, "
        "section[data-section='experience'] ul > li, "
        "div[data-view-name='profile-component-entity']"
    )
    out: list[dict] = []
    for row in rows:
        try:
            title_el    = await row.query_selector("h3, .t-bold span, .t-bold")
            company_el  = await row.query_selector(
                "p.t-14.t-normal span[aria-hidden='true'], .pv-entity__secondary-title, .t-14.t-normal.t-black--light"
            )
            dates_el    = await row.query_selector("h4 span[aria-hidden='true'], .pv-entity__date-range span, [class*='date-range']")
            location_el = await row.query_selector(".pv-entity__location span, .t-12.t-black--light.t-normal")
            out.append(
                {
                    "title":    (await _safe_text(title_el))[:160].strip(),
                    "company":  (await _safe_text(company_el))[:160].strip(),
                    "dates":    (await _safe_text(dates_el))[:120].strip(),
                    "location": (await _safe_text(location_el))[:120].strip(),
                }
            )
            if len(out) >= 6:
                break
        except Exception:
            continue
    return _dedupe_dict_list(out)


async def _scrape_education(page) -> list[dict]:
    rows = await page.query_selector_all(
        "section.education-section ul > li, "
        "section[data-section='education'] ul > li"
    )
    out: list[dict] = []
    for row in rows:
        try:
            school_el  = await row.query_selector("h3, .pv-entity__school-name, .t-bold")
            degree_el  = await row.query_selector(
                ".pv-entity__degree-name span, .pv-entity__secondary-title span, "
                "p.t-14.t-black span[aria-hidden='true']"
            )
            dates_el  = await row.query_selector(
                ".pv-entity__dates span[aria-hidden='true'], h4 span[aria-hidden='true'], [class*='date-range']"
            )
            out.append(
                {
                    "school": (await _safe_text(school_el))[:140].strip(),
                    "degree": (await _safe_text(degree_el))[:200].strip(),
                    "dates": (await _safe_text(dates_el))[:120].strip(),
                }
            )
            if len(out) >= 6:
                break
        except Exception:
            continue
    return _dedupe_dict_list(out)


async def _scrape_skills(page, max_items: int = 8) -> list[str]:
    skills: list[str] = []
    els = await page.query_selector_all(
        "section.skills-section .pv-skill-category-entity__name span[aria-hidden='true'], "
        "section[data-section='skills'] li span[class*='skill-name'], "
        ".skills-section-list span.t-bold"
    )
    for el in els:
        try:
            txt = (await _safe_text(el)).strip()
            if txt and txt not in skills:
                skills.append(txt)
                if len(skills) >= max_items:
                    break
        except Exception:
            continue
    return skills


# ── Tiny utilities ──────────────────────────────────────────────────────

def _collapse_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _dedupe_dict_list(lst: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for d in lst:
        # Title+company or school is the join key.
        key = (d.get("title") or "") + "/" + (d.get("company") or d.get("school") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out
