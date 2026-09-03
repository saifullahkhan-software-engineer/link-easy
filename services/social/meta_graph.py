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
