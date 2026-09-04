"""Helpers shared by the Meta Graph API integrations (Facebook Page, Instagram).

Both connect flows are a Facebook Login followed by ``GET /me/accounts``. An
empty Page list there is ambiguous: the person may genuinely administer no
Facebook Page, or they may have unticked *"See a list of your Pages"*
(``pages_show_list``) on Facebook's consent screen — in which case the Graph
API returns an empty list with **no** ``error`` object. ``token_scopes``
resolves that by asking ``debug_token`` which permissions the token really
carries, so each service can tell the user the actual fix.
"""
import logging
from typing import Optional, Set

import aiohttp

logger = logging.getLogger(__name__)

# The permission that gates ``/me/accounts``. Without it the edge returns an
# empty list rather than an error.
PAGES_SHOW_LIST = "pages_show_list"

# The Graph API version used by every Meta call in this project (Facebook
# Page and Instagram share it — they are one platform). Versions expire on
# a ~2-year clock (v18.0 died Jan 2026, v20.0 dies Sep 24, 2026), so all
# endpoints below derive from these constants: bumping the version here is
# the whole migration. Check the live support window at
# developers.facebook.com/docs/graph-api/changelog before bumping — pick a
# version that is *not* the brand-newest (v25.0 is supported through
# July 2028; v26.0 shipped July 29, 2026).
GRAPH_API_VERSION = "v25.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
OAUTH_DIALOG = f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth"


async def signed_in_account_name(
    session: aiohttp.ClientSession,
    graph_api: str,
    access_token: str,
) -> Optional[str]:
    """Best-effort name of the Facebook user ``access_token`` belongs to.

    ``GET /me?fields=id,name`` answers with the token owner's profile with no
    permission beyond the implicit ``public_profile``. Used only to make an
    empty-Page diagnosis actionable: an empty ``/me/accounts`` is most often
    a sign-in to the *wrong* Facebook account — the usual case in a fresh
    browser that carries a different Facebook session — and naming the
    account that just signed in is what reveals it. Returns ``None`` on any
    failure; callers fall back to their generic message, mirroring
    ``token_scopes`` (a failing lookup must never replace the original
    problem with a lookup error).
    """
    try:
        async with session.get(
            f"{graph_api}/me",
            params={"fields": "id,name", "access_token": access_token},
        ) as response:
            data = await response.json()
        if isinstance(data, dict):
            name = data.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    except Exception as exc:
        logger.warning("Meta /me name lookup failed: %s", exc)
    return None


async def token_scopes(
    session: aiohttp.ClientSession,
    graph_api: str,
    access_token: str,
    app_id: str,
    app_secret: str,
) -> Optional[Set[str]]:
    """Return the permissions ``access_token`` carries, or ``None`` when
    ``debug_token`` cannot say (Graph error, malformed payload, network).

    ``debug_token`` is authenticated with the *app* access token
    (``app_id|app_secret``); the token under inspection goes in
    ``input_token``. Callers treat ``None`` as "unknown" and fall back to
    their most general message — a failing diagnosis must never replace the
    original problem with a debug_token error.
    """
    try:
        async with session.get(
            f"{graph_api}/debug_token",
            params={"input_token": access_token, "access_token": f"{app_id}|{app_secret}"},
        ) as response:
            debug = await response.json()
        if not isinstance(debug, dict):
            raise ValueError(f"unexpected debug_token payload: {type(debug).__name__}")
        if "error" in debug:
            err = debug["error"] or {}
            detail = err.get("message", err) if isinstance(err, dict) else err
            raise ValueError(f"debug_token failed: {detail}")
        raw_scopes = (debug.get("data") or {}).get("scopes")
        if not isinstance(raw_scopes, (list, tuple, set)):
            raise ValueError("debug_token response carries no data.scopes list")
        return {str(scope) for scope in raw_scopes}
    except Exception as exc:
        logger.warning("Meta debug_token inspection failed: %s", exc)
        return None
