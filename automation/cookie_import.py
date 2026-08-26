"""
LinkedIn session-cookie import.

FILE: automation/cookie_import.py

Why this exists
---------------
``automation/session.py``'s credential login drives the real LinkedIn sign-in
form from the server. From a datacenter IP (Railway, and most other hosts)
that is the single most detection-prone thing the product does: LinkedIn
answers with a CAPTCHA or a ``/checkpoint/challenge`` far more often than it
would for a residential connection, which surfaces as
``LinkedInSessionStatus.CAPTCHA`` / ``CHECKPOINT`` and the account never
connects.

Importing an already-authenticated ``li_at`` cookie skips the sign-in form
entirely: the user logs in from their own browser (their own IP, their own
device) and hands us the resulting session. No password is transmitted, no
password is stored, and no login form is ever submitted from the server.

Honest limitation
-----------------
This does NOT make the traffic look residential. Every later request still
egresses from the server's IP, so LinkedIn can still see "session created in
Lahore, now used from a datacenter" and raise a checkpoint. Cookie import
removes the *login* CAPTCHA; it does not remove the need for a per-account
sticky proxy (the ``proxy_*`` columns on ``LinkedInAccount``). Treat it as a
significant reduction in failure rate, not a guarantee.

Accepted input formats
----------------------
Both are handled by ``parse_cookie_input`` so users never have to care:

1. A raw cookie value — what you get from DevTools → Application → Cookies:
       AQEDATEx...very-long-token
   Also accepts ``li_at=AQEDATEx...`` and a full ``document.cookie`` style
   header (``li_at=...; JSESSIONID="ajax:123"; lang=v=2``).

2. A JSON export from an extension such as EditThisCookie / Cookie-Editor —
   either a bare array or an object with a ``cookies`` key:
       [{"name": "li_at", "value": "AQED...", "domain": ".linkedin.com"}, ...]

Only cookies we actually need are kept (see ``RELEVANT_COOKIES``); everything
else in a paste is discarded so we never inject unrelated tracking state into
the account's durable profile.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from core.logging_config import get_logger

logger = get_logger(__name__)


class CookieImportError(ValueError):
    """Raised when the pasted cookie material cannot be used.

    The message is written to be shown directly to the end user, so it must
    never contain any part of the cookie value itself.
    """


# ``li_at`` is the actual session token — without it there is no session.
LI_AT = "li_at"

# ``JSESSIONID`` is required as the CSRF token for LinkedIn's internal XHR
# API; the feed/messaging surfaces misbehave without it. The rest carry
# locale/consent state and simply make the profile look more like the
# browser the session was born in.
RELEVANT_COOKIES = {
    LI_AT,
    "JSESSIONID",
    "liap",
    "lang",
    "bcookie",
    "bscookie",
    "li_gc",
    "li_mc",
    "lidc",
}

# LinkedIn session tokens are long, opaque and base64url-ish. Deliberately
# permissive — LinkedIn has changed the alphabet before and a false negative
# here would block a perfectly good session — but strict enough to catch the
# common paste mistakes (an empty value, a stray quote, a whole JSON blob
# pasted into the raw field).
_LI_AT_RE = re.compile(r"^[A-Za-z0-9_\-=.:%]{20,}$")

_COOKIE_DOMAIN = ".linkedin.com"


def _clean(value: str) -> str:
    """Strip whitespace and one layer of matching surrounding quotes."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def _validate_li_at(value: str) -> str:
    """Validate the session token, raising a user-facing error if unusable."""
    value = _clean(value)
    if not value:
        raise CookieImportError(
            "No li_at value found. Copy the li_at cookie from your browser "
            "(DevTools → Application → Cookies → linkedin.com) and paste it here."
        )
    if value.lower().startswith(("http://", "https://")):
        raise CookieImportError(
            "That looks like a URL, not a cookie. Paste the li_at cookie "
            "VALUE, not the address of the page."
        )
    if not _LI_AT_RE.match(value):
        # Never echo the value back — it is a live credential.
        raise CookieImportError(
            "That does not look like a valid li_at cookie value. It should be "
            "a single long string of letters, digits and dashes with no "
            "spaces. Re-copy it from DevTools → Application → Cookies."
        )
    return value


def _cookie_dict(name: str, value: str) -> dict[str, Any]:
    """Build a Playwright cookie for ``.linkedin.com``.

    Domain/path/flags are set by us rather than taken from the paste: an
    export from a different domain (or with ``hostOnly``) would otherwise be
    silently ignored by the browser after injection, producing a confusing
    "cookie imported but still logged out" state.
    """
    cookie: dict[str, Any] = {
        "name": name,
        "value": value,
        "domain": _COOKIE_DOMAIN,
        "path": "/",
        "secure": True,
        "sameSite": "None",
    }
    # JSESSIONID is read by LinkedIn's own JS to build the CSRF header, so it
    # must NOT be httpOnly. li_at is a pure session token and should be.
    cookie["httpOnly"] = name == LI_AT
    return cookie


def _parse_json_cookies(payload: Any) -> dict[str, str]:
    """Extract ``{name: value}`` from a parsed cookie-export JSON document."""
    if isinstance(payload, dict):
        for key in ("cookies", "Cookies", "data"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            # A plain {"li_at": "...", "JSESSIONID": "..."} mapping.
            if any(isinstance(v, str) for v in payload.values()):
                return {
                    str(k): _clean(str(v))
                    for k, v in payload.items()
                    if isinstance(v, str)
                }
            raise CookieImportError(
                "That JSON does not contain a cookie list. Export your cookies "
                "again — the file should be a list of entries, each with a "
                '"name" and a "value".'
            )

    if not isinstance(payload, list):
        raise CookieImportError(
            "That JSON is not a cookie export. Expected a list of cookie "
            'entries, each with a "name" and a "value".'
        )

    found: dict[str, str] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("Name")
        value = entry.get("value") or entry.get("Value")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        # Ignore cookies from unrelated sites in a full-browser export.
        domain = entry.get("domain") or entry.get("Domain") or ""
        if domain and "linkedin.com" not in str(domain).lower():
            continue
        found[name.strip()] = _clean(value)
    return found


def _parse_header_cookies(raw: str) -> dict[str, str]:
    """Parse a ``name=value; name2=value2`` cookie header string."""
    found: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if name:
            found[name] = _clean(value)
    return found


def parse_cookie_input(raw: str) -> list[dict[str, Any]]:
    """Turn pasted cookie material into Playwright cookie dicts.

    Accepts a bare ``li_at`` value, a ``document.cookie`` header string, or a
    JSON export (array, ``{"cookies": [...]}``, or a flat name→value map).

    Returns:
        Playwright-ready cookie dicts, ``li_at`` guaranteed present and first.

    Raises:
        CookieImportError: with an end-user-safe message. The cookie value is
            never included in the message.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise CookieImportError(
            "Paste your LinkedIn li_at cookie (or a cookie JSON export) to "
            "connect the account."
        )

    text = raw.strip()
    found: dict[str, str] = {}

    # 1) JSON export?
    if text[0] in "[{":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CookieImportError(
                "That looks like JSON but could not be parsed. Re-export your "
                "cookies and paste the whole file contents unchanged."
            ) from exc
        found = _parse_json_cookies(payload)

    # 2) Cookie header / ``name=value`` pairs?
    elif "=" in text and re.search(r"\b(li_at|JSESSIONID|bcookie)\s*=", text):
        found = _parse_header_cookies(text)

    # 3) Otherwise treat the whole paste as a bare li_at value.
    else:
        found = {LI_AT: text}

    # Normalise key casing for the lookup (exports vary: li_at / LI_AT).
    normalised = {k.strip(): v for k, v in found.items() if k and k.strip()}
    lowered = {k.lower(): (k, v) for k, v in normalised.items()}

    if LI_AT not in lowered:
        raise CookieImportError(
            "No li_at cookie found in what you pasted. li_at is the LinkedIn "
            "session cookie — copy it from DevTools → Application → Cookies → "
            "https://www.linkedin.com."
        )

    li_at_value = _validate_li_at(lowered[LI_AT][1])

    cookies: list[dict[str, Any]] = [_cookie_dict(LI_AT, li_at_value)]
    for name, value in normalised.items():
        if name.lower() == LI_AT:
            continue
        if name not in RELEVANT_COOKIES:
            continue
        value = _clean(value)
        if not value:
            continue
        cookies.append(_cookie_dict(name, value))

    logger.info(
        "🍪 Parsed LinkedIn cookie import: %d cookie(s) [%s]",
        len(cookies),
        ", ".join(sorted(c["name"] for c in cookies)),
    )
    return cookies


def cookie_names(cookies: Iterable[dict[str, Any]]) -> list[str]:
    """Sorted cookie names — safe for logs (never includes values)."""
    return sorted(str(c.get("name", "")) for c in cookies)
