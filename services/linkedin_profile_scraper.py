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

# LinkedIn is currently rolling out its SDUI profile page. That page has no h1
# and all visual classes are generated hashes; its stable card id ends in
# ``Topcard`` and the member name is an h2. Keep the classic selectors as well
# because both page variants are active at the same time.
PROFILE_NAME_SELECTOR = (
    "main h1.text-heading-xlarge, "
    "main h1[class*='top-card'], "
    "main h1, "
    "main [id$='Topcard'] h2"
)
PROFILE_READY_SELECTOR = PROFILE_NAME_SELECTOR


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


async def _safe_texts(root, selector: str) -> list[str]:
    """Return de-duplicated descendant texts without exposing DOM failures."""
    try:
        elements = await root.query_selector_all(selector)
    except Exception:
        return []

    texts: list[str] = []
    seen: set[str] = set()
    for element in elements:
        text = _collapse_whitespace(await _safe_text(element))
        if text and text not in seen:
            seen.add(text)
            texts.append(text)
    return texts


async def _safe_page_title(page) -> str:
    try:
        return _collapse_whitespace(await page.title())
    except Exception:
        return ""


def _name_from_title(title: str) -> str:
    """Extract a profile name from ``Ada Lovelace | LinkedIn``."""
    match = re.match(r"^(.+?)\s*(?:\||[-–—])\s*LinkedIn\s*$", title or "", re.I)
    if not match:
        return ""
    candidate = _collapse_whitespace(match.group(1))
    if candidate.casefold() in {
        "linkedin",
        "sign in",
        "log in",
        "join linkedin",
        "security verification",
    }:
        return ""
    return candidate[:200]


async def _lazy_render_profile(page) -> None:
    """Walk the page so old and virtualized SDUI section cards can render."""
    # Fixed positions are intentional: they work in both Playwright and the
    # lightweight page fakes used by the regression suite. The old three-stop
    # walk ended around Activity on the SDUI page, before Experience/Education.
    for y in (700, 1500, 2500, 3700, 5100, 6800, 8600, 10600):
        try:
            await page.evaluate(
                "y => window.scrollTo({top: y, behavior: 'instant'})", y
            )
        except Exception:
            break
        await asyncio.sleep(0.35)
    try:
        await page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
    except Exception:
        pass
    await asyncio.sleep(0.35)


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
            if any(
                marker in current_url
                for marker in ("/login", "/checkpoint", "/authwall", "/uas/login")
            ):
                raise RuntimeError(
                    "LinkedIn session expired or requires verification. Reconnect the account and try again."
                )

            try:
                await page.wait_for_selector(PROFILE_READY_SELECTOR, timeout=15000)
            except Exception as exc:
                # Some LinkedIn experiments paint the document title before
                # attaching the SDUI card. A specific ``Name | LinkedIn`` title
                # is enough to continue with a useful basic report instead of
                # returning a false 500 merely because the page has no h1.
                if not _name_from_title(await _safe_page_title(page)):
                    raise RuntimeError(
                        "LinkedIn did not render the profile. Check that the URL is accessible to the connected account."
                    ) from exc

            await _lazy_render_profile(page)

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
    basics: dict[str, Any] = {
        "name": "",
        "headline": "",
        "location": "",
        "current_position": "",
    }
    name_el = await page.query_selector(PROFILE_NAME_SELECTOR)
    if name_el is not None:
        basics["name"] = _collapse_whitespace(await _safe_text(name_el))[:200]
    if not basics["name"]:
        basics["name"] = _name_from_title(await _safe_page_title(page))

    # Classic profile page headline/location.
    headline_el = await page.query_selector(
        "main .text-body-medium.break-words, "
        "main [data-generated-suggestion-target='headline']"
    )
    if headline_el is not None:
        basics["headline"] = _collapse_whitespace(await _safe_text(headline_el))

    loc_el = await page.query_selector(
        "main .pv-top-card .text-body-small.inline.t-black--light.break-words, "
        "main span.text-body-small.inline.t-black--light.break-words"
    )
    if loc_el is not None:
        basics["location"] = _collapse_whitespace(await _safe_text(loc_el))

    # New SDUI page: visual classes are hashes, but the Topcard id and semantic
    # p/h2 tags are stable. In this layout the paragraphs are ordered as
    # relationship, headline, company/school, location, contact info.
    topcard_lines = await _safe_texts(page, "main [id$='Topcard'] p")
    if topcard_lines:
        location = _sdui_location(topcard_lines)
        if not basics["location"]:
            basics["location"] = location
        if not basics["headline"]:
            basics["headline"] = _sdui_headline(
                topcard_lines, basics["name"], location
            )

    basics["headline"] = basics["headline"][:500]
    basics["location"] = basics["location"][:200]
    # Current position — best signal is the first experience row's title.
    basics["profile_url"] = page.url or requested_url
    return basics


def _is_topcard_noise(text: str, name: str = "") -> bool:
    clean = _collapse_whitespace(text)
    folded = clean.casefold().strip(" ·•")
    if not clean or (name and folded == name.casefold()):
        return True
    if folded in {"1st", "2nd", "3rd", "contact info", "connections", "connection"}:
        return True
    if re.fullmatch(r"\d[\d,]*", folded):
        return True
    if "mutual connection" in folded or folded.endswith(" connections"):
        return True
    if re.fullmatch(r"\(?\w+\s*/\s*\w+\)?", clean):  # pronouns
        return True
    return False


def _sdui_location(lines: list[str]) -> str:
    contact_index = next(
        (i for i, line in enumerate(lines) if line.casefold() == "contact info"),
        len(lines),
    )
    # The location immediately precedes Contact info (sometimes with a separate
    # middle-dot paragraph between them). Prefer a comma-bearing place string.
    before_contact = [
        line for line in lines[:contact_index] if not _is_topcard_noise(line)
    ]
    for line in reversed(before_contact):
        if "," in line and len(line) <= 200:
            return line
    return ""


def _sdui_headline(lines: list[str], name: str, location: str) -> str:
    for line in lines:
        if _is_topcard_noise(line, name) or line == location:
            continue
        # Contact/company links appear after the headline. The first remaining
        # semantic paragraph is therefore the most reliable signal.
        return line
    return ""


async def _scrape_about(page) -> str:
    about_el = await page.query_selector(
        "main section:has(#about) .inline-show-more-text span[aria-hidden='true'], "
        "main section:has(#about) .inline-show-more-text, "
        "main section.summary .display-flex, "
        "main section[data-section='summary'] "
        ".pv-shared-text-with-see-more span[aria-hidden='true'], "
        "main [id$='About'] p"
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


async def _row_lines(row) -> list[str]:
    """Semantic SDUI fallback when LinkedIn replaces all legacy classes."""
    return await _safe_texts(row, "h3, p")


def _line_matching(lines: list[str], pattern: str) -> str:
    regex = re.compile(pattern, re.I)
    return next((line for line in lines if regex.search(line)), "")


async def _scrape_experience(page) -> list[dict]:
    """Read classic profile entities and the classless SDUI Experience card."""
    rows = await page.query_selector_all(
        "main section:has(#experience) "
        "div[data-view-name='profile-component-entity'], "
        "main section:has(#experience) ul > li, "
        "main section.experience-section ul > li, "
        "main section[data-section='experience'] ul > li, "
        "main [id$='Experience'] li, "
        "main [id$='Experience'] [role='listitem']"
    )
    out: list[dict] = []
    for row in rows:
        try:
            title_el = await row.query_selector("h3, .t-bold span, .t-bold")
            company_el = await row.query_selector(
                "p.t-14.t-normal:not(.t-black--light) span[aria-hidden='true'], "
                ".pv-entity__secondary-title"
            )
            dates_el = await row.query_selector(
                "h4 span[aria-hidden='true'], .pv-entity__date-range span, "
                "[class*='date-range'], "
                ".t-14.t-normal.t-black--light span[aria-hidden='true']"
            )
            location_el = await row.query_selector(
                ".pv-entity__location span, .t-12.t-black--light.t-normal"
            )
            item = {
                "title": (await _safe_text(title_el))[:160].strip(),
                "company": (await _safe_text(company_el))[:160].strip(),
                "dates": (await _safe_text(dates_el))[:120].strip(),
                "location": (await _safe_text(location_el))[:120].strip(),
            }
            if not any(item.values()):
                lines = await _row_lines(row)
                if lines:
                    item["title"] = lines[0][:160]
                    item["company"] = (lines[1] if len(lines) > 1 else "")[:160]
                    item["dates"] = _line_matching(
                        lines[1:], r"\b(?:19|20)\d{2}\b|\bpresent\b"
                    )[:120]
                    item["location"] = next(
                        (
                            line[:120]
                            for line in lines[2:]
                            if "," in line and line != item["dates"]
                        ),
                        "",
                    )
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
        "main section[data-section='education'] ul > li, "
        "main [id$='Education'] li, "
        "main [id$='Education'] [role='listitem']"
    )
    out: list[dict] = []
    for row in rows:
        try:
            school_el = await row.query_selector("h3, .pv-entity__school-name, .t-bold")
            degree_el = await row.query_selector(
                ".pv-entity__degree-name span, .pv-entity__secondary-title span, "
                "p.t-14.t-black span[aria-hidden='true']"
            )
            dates_el = await row.query_selector(
                ".pv-entity__dates span[aria-hidden='true'], h4 span[aria-hidden='true'], [class*='date-range']"
            )
            item = {
                "school": (await _safe_text(school_el))[:140].strip(),
                "degree": (await _safe_text(degree_el))[:200].strip(),
                "dates": (await _safe_text(dates_el))[:120].strip(),
            }
            if not any(item.values()):
                lines = await _row_lines(row)
                if lines:
                    item["school"] = lines[0][:140]
                    item["degree"] = (lines[1] if len(lines) > 1 else "")[:200]
                    item["dates"] = _line_matching(
                        lines[1:], r"\b(?:19|20)\d{2}\b|\bpresent\b"
                    )[:120]
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
        "main .skills-section-list span.t-bold, "
        "main [id$='Skills'] li h3, "
        "main [id$='Skills'] [role='listitem'] h3"
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
        key = (d.get("title") or "") + "/" + (
            d.get("company") or d.get("school") or ""
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out
