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

.. important::
   Every JavaScript snippet passed to ``page.evaluate`` must be a **raw**
   string (``r\"\"\"...\"\"\"``) whenever it contains a backslash. In a normal
   Python string ``\\b`` becomes a backspace (0x08), ``\\f`` a form feed, and
   so on, so the browser receives corrupted source. That silently broke the
   "Show all"/"See more" expander regex and made scans return only the
   basics and About sections. ``tests/test_live_chat_and_profile_scan_fixes.py``
   asserts no injected snippet ships a control character.
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


async def _expand_profile_sections(page) -> None:
    """Expand profile cards before scraping their virtualized contents.

    LinkedIn frequently renders only the About card and the first visual
    portion of Experience/Skills until a ``Show all`` or ``See more`` control
    is clicked.  The control copy and CSS classes change, but the visible
    button/link text remains a useful semantic hook.  This is best-effort: a
    missing ``evaluate`` implementation in a test double or an older page
    must not make an otherwise valid profile fail.
    """
    try:
        await page.evaluate(
            # NOTE: raw string. Without the ``r`` prefix Python turns the
            # ``\b`` word-boundary into a literal backspace (0x08) character,
            # so the regex below became /^(show all|see more)<BS>/i and never
            # matched a real control. Every "Show all"/"See more" expander was
            # then skipped, which is why scans returned only About/basics.
            r"""() => {
                const wanted = /^(show all|see more)\b/i;
                let clicked = 0;
                const controls = Array.from(document.querySelectorAll(
                    'main button, main a, main [role="button"]'
                ));
                for (const control of controls) {
                    const text = (control.innerText || control.getAttribute('aria-label') || '')
                        .replace(/\s+/g, ' ').trim();
                    if (!wanted.test(text) || control.dataset.linkeasyExpanded === '1') continue;
                    // "Show all experiences/skills" is often a navigation
                    // link to /details/... rather than an in-place expander.
                    // Clicking it here used to leave the profile page before
                    // the other cards were scraped. Keep real buttons/role
                    // controls (About/inline expanders) in-place and leave
                    // section-level semantic fallbacks below.
                    if (control.tagName === 'A' && control.getAttribute('href')) continue;
                    control.dataset.linkeasyExpanded = '1';
                    control.click();
                    clicked += 1;
                }
                return clicked;
            }"""
        )
    except Exception:
        return
    await asyncio.sleep(0.45)


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
        # Experience, Education, and Skills often mount their own "Show all"
        # control only after the card enters the viewport. Expanding once at
        # the top therefore scraped About but left every lower section empty.
        await _expand_profile_sections(page)
        await asyncio.sleep(0.55)
    # SDUI can append more rows after the fixed positions above.  One bottom
    # pass catches those rows without an unbounded scroll loop.
    try:
        await page.evaluate(
            "window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'instant'})"
        )
    except Exception:
        pass
    await asyncio.sleep(0.35)
    try:
        await page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
    except Exception:
        pass
    await asyncio.sleep(0.35)


async def _profile_detail_urls(page) -> dict[str, str]:
    """Find LinkedIn's optional full-section links without clicking them."""
    try:
        links = await page.evaluate(
            """() => {
                const found = {};
                for (const anchor of document.querySelectorAll('main a[href]')) {
                    const href = anchor.href || '';
                    const path = href.toLowerCase();
                    for (const section of ['experience', 'education', 'skills']) {
                        if (!found[section] && path.includes(`/details/${section}`)) {
                            found[section] = href;
                        }
                    }
                }
                return found;
            }"""
        )
    except Exception:
        return {}
    return links if isinstance(links, dict) else {}


async def _scrape_profile_detail_sections(
    page,
    profile_url: str,
    detail_urls: dict[str, str],
    experience: list[dict],
    education: list[dict],
    skills: list[str],
) -> tuple[list[dict], list[dict], list[str]]:
    """Use LinkedIn's full-section pages when profile cards expose them.

    Profile cards are frequently virtualized and show only a preview. The
    ``/details/experience`` and similar links contain the complete list, but
    clicking them would navigate away before the other cards are read. Visit
    each link deliberately, scrape it, and restore the profile between visits.
    """
    for section in ("experience", "education", "skills"):
        detail_url = detail_urls.get(section)
        if not detail_url:
            continue
        try:
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_selector("main", timeout=15000)
            except Exception:
                pass
            rows = await _evaluate_sdui_rows(page, section)
            if section == "experience":
                experience = _dedupe_dict_list(
                    experience + _parse_sdui_experience_rows(rows)
                )[:20]
            elif section == "education":
                education = _dedupe_dict_list(
                    education + _parse_sdui_education_rows(rows)
                )[:20]
            else:
                skills = list(dict.fromkeys(skills + _parse_sdui_skills(rows)))[:20]
        except Exception as exc:
            logger.debug("Could not scrape LinkedIn %s detail page: %s", section, exc)
        finally:
            try:
                await page.goto(
                    profile_url, wait_until="domcontentloaded", timeout=30000
                )
            except Exception as exc:
                logger.debug("Could not restore profile after %s details: %s", section, exc)
    return experience, education, skills


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

            await _expand_profile_sections(page)
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
            about = await _scrape_about(page)
            education = await _scrape_education(page)
            skills = await _scrape_skills(page)
            detail_urls = await _profile_detail_urls(page)
            if detail_urls:
                experience, education, skills = await _scrape_profile_detail_sections(
                    page,
                    profile_url,
                    detail_urls,
                    experience,
                    education,
                    skills,
                )
            data: dict[str, Any] = {
                "basics": basics,
                "about": about,
                "experience": experience,
                "education": education,
                "skills": skills,
                "scraped_at": time.time(),
                "source_url": profile_url,
            }
            logger.info(
                "LinkedIn profile scan sections: name=%s about=%s experience=%d education=%d skills=%d",
                bool(basics.get("name")),
                bool(about),
                len(experience),
                len(education),
                len(skills),
            )
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


async def _evaluate_sdui_rows(page, section: str) -> list[list[str]]:
    """Extract visible row text from LinkedIn's classless SDUI cards.

    The newer profile surface gives cards generated IDs and removes the old
    ``pv-*`` classes.  This fallback deliberately reads each row's
    ``innerText`` (rather than every nested span separately): nested spans
    repeat the same company/date text and the old implementation accidentally
    used one global ``seen`` set, causing all rows after the first one to lose
    their fields.
    """
    try:
        rows = await page.evaluate(
            """(sectionName) => {
                const key = String(sectionName || '').toLowerCase();
                const aliases = {
                    experience: ['experience', 'work experience'],
                    education: ['education'],
                    skills: ['skills', 'top skills'],
                }[key] || [key];
                const clean = (value) => String(value || '')
                    .replace(/\\u00a0/g, ' ')
                    .split(/\\n+/)
                    .map((line) => line.replace(/\\s+/g, ' ').trim())
                    .filter(Boolean);
                const marker = (el) => [
                    el.id,
                    el.getAttribute('data-section'),
                    el.getAttribute('data-view-name'),
                    el.getAttribute('aria-label'),
                ].filter(Boolean).join(' ').toLowerCase();
                const headingMatch = (el) => aliases.includes(
                    clean(el.innerText).join(' ').toLowerCase()
                );
                const roots = Array.from(document.querySelectorAll(
                    'main section, main [role="region"], main [data-section], main [id]'
                ));
                let root = roots.find((el) => aliases.some((alias) => marker(el).includes(alias)));
                if (!root) {
                    const heading = Array.from(document.querySelectorAll(
                        'main h1, main h2, main h3, main h4'
                    )).find(headingMatch);
                    if (heading) root = heading.closest('section, [role="region"], [data-view-name]') || heading.parentElement;
                }
                if (!root) return [];

                const selector = [
                    'li',
                    '[role="listitem"]',
                    '[data-view-name*="entity" i]',
                    '[data-view-name*="profile-component-entity" i]',
                    'div.pvs-list__item--line-separated',
                ].join(',');
                let candidates = Array.from(root.querySelectorAll(selector));
                // Prefer the deepest logical rows; an outer li and its entity
                // child frequently both match the selector.
                const leaves = candidates.filter((candidate) => !candidates.some(
                    (other) => other !== candidate && candidate.contains(other)
                ));
                candidates = leaves.length ? leaves : candidates;
                if (!candidates.length) candidates = [root];

                const output = [];
                const seenRows = new Set();
                for (const candidate of candidates) {
                    const lines = clean(candidate.innerText);
                    const deduped = [];
                    for (const line of lines) {
                        if (!deduped.includes(line)) deduped.push(line);
                    }
                    const row = deduped.slice(0, 16);
                    const identity = row.join('\\u001f');
                    if (row.length && !seenRows.has(identity)) {
                        seenRows.add(identity);
                        output.push(row);
                    }
                }
                return output.slice(0, 20);
            }""",
            section,
        )
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    return [
        [_collapse_whitespace(str(value)) for value in row if _collapse_whitespace(str(value))]
        for row in rows
        if isinstance(row, list)
    ]


_SDUI_CONTROL_RE = re.compile(
    r"^(?:show all|see all|see more|show less)\b"
    r"|^(?:contact info|skills|experience|education)(?:\s+\d+)?$",
    re.I,
)
_DATE_RE = re.compile(r"\b(?:19|20)\d{2}\b|\bpresent\b", re.I)
_EMPLOYMENT_TYPE_RE = re.compile(
    r"^(?:full[- ]time|part[- ]time|contract|freelance|internship|self[- ]employed|temporary)$",
    re.I,
)


def _clean_sdui_lines(lines: list[str], section: str) -> list[str]:
    """Remove headings, controls, and repeated accessibility text."""
    cleaned: list[str] = []
    for value in lines:
        line = _collapse_whitespace(value)
        if not line or _SDUI_CONTROL_RE.match(line):
            continue
        if section == "skills" and re.fullmatch(
            r"[\d,]+(?:\s+endorsements?)?", line, re.I
        ):
            continue
        if line not in cleaned:
            cleaned.append(line)
    return cleaned


def _parse_sdui_experience_rows(rows: list[list[str]]) -> list[dict]:
    out = []
    for raw_lines in rows:
        lines = _clean_sdui_lines(raw_lines, "experience")
        if not lines:
            continue
        dates = _line_matching(lines[1:], _DATE_RE.pattern)
        dates_index = lines.index(dates) if dates in lines else None
        company_candidates = [
            line for index, line in enumerate(lines[1:], start=1)
            if index != dates_index and not _EMPLOYMENT_TYPE_RE.fullmatch(line)
        ]
        location = next(
            (
                line[:120]
                for index, line in enumerate(lines[1:], start=1)
                if index != dates_index
                and ("," in line or re.search(r"\b(remote|hybrid|on[- ]site)\b", line, re.I))
                and line not in company_candidates[:1]
            ),
            "",
        )
        out.append({
            "title": lines[0][:160],
            "company": (company_candidates[0] if company_candidates else "")[:160],
            "dates": dates[:120],
            "location": location,
        })
    return _dedupe_dict_list(out)


def _parse_sdui_education_rows(rows: list[list[str]]) -> list[dict]:
    out = []
    for raw_lines in rows:
        lines = _clean_sdui_lines(raw_lines, "education")
        if not lines:
            continue
        dates = _line_matching(lines[1:], _DATE_RE.pattern)
        dates_index = lines.index(dates) if dates in lines else None
        degree = next(
            (
                line for index, line in enumerate(lines[1:], start=1)
                if index != dates_index
            ),
            "",
        )
        out.append({
            "school": lines[0][:140],
            "degree": degree[:200],
            "dates": dates[:120],
        })
    return _dedupe_dict_list(out)


def _parse_sdui_skills(rows: list[list[str]], max_items: int = 20) -> list[str]:
    skills: list[str] = []
    for raw_lines in rows:
        for candidate in _clean_sdui_lines(raw_lines, "skills"):
            if candidate.casefold() in {"skills", "show all skills"}:
                continue
            if re.fullmatch(r"[\d,]+(?: endorsements?)?", candidate, re.I):
                continue
            if candidate not in skills:
                skills.append(candidate[:160])
            break
        if len(skills) >= max_items:
            break
    return skills[:max_items]


async def _row_lines(row) -> list[str]:
    """Semantic SDUI fallback when LinkedIn replaces all legacy classes."""
    return await _safe_texts(row, "h3, h4, p, span[aria-hidden='true']")


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
        "main section[id*='experience' i] li, "
        "main section[id*='experience' i] [role='listitem'], "
        "main [id$='Experience'] li, "
        "main [id$='Experience'] [role='listitem'], "
        "main [id*='Experience' i] [data-view-name*='entity' i], "
        "main [data-view-name*='experience' i] [role='listitem']"
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
            lines = await _row_lines(row)
            if lines:
                if not item["title"]:
                    item["title"] = lines[0][:160]
                if not item["company"] and len(lines) > 1:
                    item["company"] = lines[1][:160]
                if not item["dates"]:
                    item["dates"] = _line_matching(
                        lines[1:], r"\b(?:19|20)\d{2}\b|\bpresent\b"
                    )[:120]
                if not item["location"]:
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
            if len(out) >= 20:
                break
        except Exception:
            continue
    out = _dedupe_dict_list(out)
    # Always merge the semantic fallback. LinkedIn can expose one classic row
    # while the remaining virtualized rows only exist in the SDUI card; using
    # ``if out: return out`` was the reason scans often contained About plus a
    # single/empty Experience section.
    semantic = _parse_sdui_experience_rows(
        await _evaluate_sdui_rows(page, "experience")
    )
    return _dedupe_dict_list(out + semantic)[:20]


async def _scrape_education(page) -> list[dict]:
    rows = await page.query_selector_all(
        "main section:has(#education) "
        "div[data-view-name='profile-component-entity'], "
        "main section:has(#education) ul > li, "
        "main section.education-section ul > li, "
        "main section[data-section='education'] ul > li, "
        "main section[id*='education' i] li, "
        "main section[id*='education' i] [role='listitem'], "
        "main [id$='Education'] li, "
        "main [id$='Education'] [role='listitem'], "
        "main [id*='Education' i] [data-view-name*='entity' i], "
        "main [data-view-name*='education' i] [role='listitem']"
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
            lines = await _row_lines(row)
            if lines:
                if not item["school"]:
                    item["school"] = lines[0][:140]
                if not item["degree"] and len(lines) > 1:
                    item["degree"] = lines[1][:200]
                if not item["dates"]:
                    item["dates"] = _line_matching(
                        lines[1:], r"\b(?:19|20)\d{2}\b|\bpresent\b"
                    )[:120]
            if not any(item.values()):
                continue
            out.append(item)
            if len(out) >= 20:
                break
        except Exception:
            continue
    out = _dedupe_dict_list(out)
    semantic = _parse_sdui_education_rows(
        await _evaluate_sdui_rows(page, "education")
    )
    return _dedupe_dict_list(out + semantic)[:20]


async def _scrape_skills(page, max_items: int = 20) -> list[str]:
    skills: list[str] = []
    els = await page.query_selector_all(
        "main section:has(#skills) "
        "div[data-view-name='profile-component-entity'] .t-bold span[aria-hidden='true'], "
        "main section:has(#skills) li .t-bold span[aria-hidden='true'], "
        "main section.skills-section "
        ".pv-skill-category-entity__name span[aria-hidden='true'], "
        "main section[data-section='skills'] li span[class*='skill-name'], "
        "main section[id*='skills' i] li h3, "
        "main section[id*='skills' i] [role='listitem'] h3, "
        "main .skills-section-list span.t-bold, "
        "main [id$='Skills'] li h3, "
        "main [id$='Skills'] [role='listitem'] h3, "
        "main [id*='Skills' i] [data-view-name*='entity' i] h3, "
        "body [role='dialog'] li h3, "
        "body [role='dialog'] [role='listitem'] h3"
    )
    for el in els:
        try:
            txt = _collapse_whitespace(await _safe_text(el))
            if not txt:
                continue
            if txt.casefold() in {"skills", "show all skills"}:
                continue
            if re.fullmatch(r"[\d,]+(?:\s+endorsements?)?", txt, re.I):
                continue
            if txt not in skills:
                skills.append(txt[:160])
                if len(skills) >= max_items:
                    break
        except Exception:
            continue
    # Merge semantic rows even when a legacy selector found a partial list.
    # The first visible eight skills are often classic DOM nodes while the
    # remaining entries arrive only after the Skills card is expanded.
    for lines in await _evaluate_sdui_rows(page, "skills"):
        if not lines:
            continue
        candidate = next(
            (
                value for value in _clean_sdui_lines(lines, "skills")
                if value.casefold() not in {"skills", "show all skills"}
            ),
            "",
        )
        if not candidate or re.fullmatch(r"[\d,]+(?: endorsements?)?", candidate, re.I):
            continue
        if candidate not in skills:
            skills.append(candidate[:160])
        if len(skills) >= max_items:
            break
    return skills[:max_items]


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
