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
    """Open ``profile_url`` exclusively, scrape it, and restore the prior URL."""
    parsed = urlparse(profile_url)
    host = (parsed.hostname or "").lower()
    if host not in {"linkedin.com", "www.linkedin.com"}:
        raise ValueError("profile_url must be a linkedin.com profile URL")
    if not parsed.path.startswith("/in/"):
        raise ValueError("profile_url must point to a LinkedIn /in/ profile")

    # Live chat polling and profile navigation use the same Playwright page.
    # Holding this manager lock prevents a list/message poll from navigating or
    # reading the DOM halfway through the profile scan.
    async with linkedin_live_browser.profile_page() as page:
        original_url = page.url
        try:
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
            current_url = (page.url or "").lower()
            if "/login" in current_url or "/checkpoint" in current_url:
                raise RuntimeError(
                    "LinkedIn session expired or requires verification. Reconnect the account and try again."
                )
            try:
                await page.wait_for_selector(
                    "main h1.text-heading-xlarge, main h1[class*='top-card'], main h1",
                    timeout=15000,
                )
            except Exception as exc:
                raise RuntimeError(
                    "LinkedIn did not render the profile. Check that the URL is accessible to the connected account."
                ) from exc

            # Experience and education are lazy-rendered below the fold.
            for y in (700, 1500, 2400):
                try:
                    await page.evaluate("y => window.scrollTo({top: y, behavior: 'instant'})", y)
                except Exception:
                    break
                await asyncio.sleep(0.5)
            try:
                await page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
            except Exception:
                pass
            await asyncio.sleep(0.5)

            basics = await _scrape_basics(page, profile_url)
            if not basics.get("name"):
                raise RuntimeError(
                    "The profile loaded but LinkedIn did not expose its name. The session may need verification."
                )
            experience = await _scrape_experience(page)
            if experience:
                first_role = experience[0]
                basics["current_position"] = " at ".join(
                    part
                    for part in (first_role.get("title"), first_role.get("company"))
                    if part
                )
            data: dict[str, Any] = {
                "basics": basics,
                "about": await _scrape_about(page),
                "experience": experience,
                "education": await _scrape_education(page),
                "skills": await _scrape_skills(page),
                "scraped_at": time.time(),
                "source_url": profile_url,
            }
        finally:
            # Restore the exact thread URL, not merely /messaging/. This keeps
            # a selected live conversation open after a profile preview.
            restore_url = original_url or "https://www.linkedin.com/messaging/"
            try:
                await page.goto(
                    restore_url, wait_until="domcontentloaded", timeout=30000
                )
            except Exception as exc:
                logger.warning("Could not restore LinkedIn page after profile scan: %s", exc)

        return data


# ── Section scrapers (best-effort) ─────────────────────────────────────────

async def _scrape_basics(page, requested_url: str) -> dict[str, Any]:
    basics: dict[str, Any] = {"name": "", "headline": "", "location": "", "current_position": ""}
    # Name — top-of-card h1.
    name_el = await page.query_selector(
        "main h1.text-heading-xlarge, main h1[class*='top-card'], main h1"
    )
    if name_el is not None:
        basics["name"] = (await _safe_text(name_el)).strip()
    # Headline.
    headline_el = await page.query_selector(
        "main .text-body-medium.break-words, "
        "main [data-generated-suggestion-target='headline']"
    )
    if headline_el is not None:
        basics["headline"] = (await _safe_text(headline_el)).strip()
    # Location.
    loc_el = await page.query_selector(
        "main .pv-top-card .text-body-small.inline.t-black--light.break-words, "
        "main span.text-body-small.inline.t-black--light.break-words"
    )
    if loc_el is not None:
        basics["location"] = (await _safe_text(loc_el)).strip()
    # Current position — best signal is the first experience row's title.
    basics["profile_url"] = page.url or requested_url
    return basics


async def _scrape_about(page) -> str:
    about_el = await page.query_selector(
        "main section:has(#about) .inline-show-more-text span[aria-hidden='true'], "
        "main section:has(#about) .inline-show-more-text, "
        "main section.summary .display-flex, "
        "main section[data-section='summary'] "
        ".pv-shared-text-with-see-more span[aria-hidden='true']"
    )
    if about_el is None:
        # Fallback: the about summary section's first paragraph.
        about_el = await page.query_selector(
            "main section:has(#about) p, "
            "main section.summary p, "
            "main section[data-section='summary'] p, "
            "main .pv-about__summary-text"
        )
    if about_el is None:
        return ""
    txt = (await _safe_text(about_el)).strip()
    return _collapse_whitespace(txt)[:2_600]


async def _scrape_experience(page) -> list[dict]:
    """Each row is ``[data-section='experience'] li`` or ``section.experience-section li``."""
    rows = await page.query_selector_all(
        "main section:has(#experience) "
        "div[data-view-name='profile-component-entity'], "
        "main section:has(#experience) ul > li, "
        "main section.experience-section ul > li, "
        "main section[data-section='experience'] ul > li"
    )
    out: list[dict] = []
    for row in rows:
        try:
            title_el    = await row.query_selector("h3, .t-bold span, .t-bold")
            company_el  = await row.query_selector(
                "p.t-14.t-normal:not(.t-black--light) span[aria-hidden='true'], "
                ".pv-entity__secondary-title"
            )
            dates_el    = await row.query_selector(
                "h4 span[aria-hidden='true'], .pv-entity__date-range span, "
                "[class*='date-range'], "
                ".t-14.t-normal.t-black--light span[aria-hidden='true']"
            )
            location_el = await row.query_selector(
                ".pv-entity__location span, .t-12.t-black--light.t-normal"
            )
            item = {
                "title":    (await _safe_text(title_el))[:160].strip(),
                "company":  (await _safe_text(company_el))[:160].strip(),
                "dates":    (await _safe_text(dates_el))[:120].strip(),
                "location": (await _safe_text(location_el))[:120].strip(),
            }
            if not any(item.values()):
                continue
            out.append(item)
            if len(out) >= 6:
                break
        except Exception:
            continue
    return _dedupe_dict_list(out)


async def _scrape_education(page) -> list[dict]:
    rows = await page.query_selector_all(
        "main section:has(#education) "
        "div[data-view-name='profile-component-entity'], "
        "main section:has(#education) ul > li, "
        "main section.education-section ul > li, "
        "main section[data-section='education'] ul > li"
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
            item = {
                "school": (await _safe_text(school_el))[:140].strip(),
                "degree": (await _safe_text(degree_el))[:200].strip(),
                "dates": (await _safe_text(dates_el))[:120].strip(),
            }
            if not any(item.values()):
                continue
            out.append(item)
            if len(out) >= 6:
                break
        except Exception:
            continue
    return _dedupe_dict_list(out)


async def _scrape_skills(page, max_items: int = 8) -> list[str]:
    skills: list[str] = []
    els = await page.query_selector_all(
        "main section:has(#skills) "
        "div[data-view-name='profile-component-entity'] .t-bold span[aria-hidden='true'], "
        "main section:has(#skills) li .t-bold span[aria-hidden='true'], "
        "main section.skills-section "
        ".pv-skill-category-entity__name span[aria-hidden='true'], "
        "main section[data-section='skills'] li span[class*='skill-name'], "
        "main .skills-section-list span.t-bold"
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
