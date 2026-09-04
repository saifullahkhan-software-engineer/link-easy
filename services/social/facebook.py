"""Facebook Page video publishing through the Meta Graph API.

Connecting a Page is a Facebook Login followed by ``GET /me/accounts``; the
Page's own access token (scoped to that Page) is what gets stored and used
for uploads. ``exchange_code`` picks the Page to connect and, when the list
is empty, explains why — the same diagnosis ``services.social.instagram``
performs for Instagram, because both flows fail in the same three ways
without the Graph API returning an ``error`` object.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
import aiohttp

from core.config import settings

from .meta_graph import PAGES_SHOW_LIST, token_scopes

logger = logging.getLogger(__name__)

# The Page task required to post video. ``/me/accounts`` reports the user's
# tasks on each Page (older Page roles) or the ``PROFILE_PLUS_*`` variants
# (New Pages Experience).
CREATE_CONTENT_TASKS = {"CREATE_CONTENT", "PROFILE_PLUS_CREATE_CONTENT", "MANAGE", "PROFILE_PLUS_FULL_CONTROL"}

# User-facing messages. The OAuth callback redirects the user to the settings
# page with ``error[:300]``, so each must stay under 300 characters.
MISSING_PAGES_PERMISSION = (
    "The Facebook sign-in was granted without the 'See a list of your Pages' permission "
    "(pages_show_list), so no Facebook Page can be listed. Disconnect Facebook and "
    "reconnect, approving every permission on Facebook's screen."
)
NO_FACEBOOK_PAGE = (
    "The signed-in Facebook account does not administer any Facebook Page (or shared none "
    "with this app). Sign in with the Facebook account that manages the Page you want to "
    "post to — a Page you only follow or like does not count — then reconnect."
)
NO_PAGE_TOKEN = (
    "Facebook listed your Page(s) but issued no Page access token for any of them, so the "
    "app cannot post on their behalf. Disconnect Facebook and reconnect, approving every "
    "permission on Facebook's screen; if it persists, check your role on the Page."
)


class FacebookService:
    GRAPH_API = "https://graph.facebook.com/v21.0"
    SCOPES = "pages_show_list,pages_manage_posts,business_management"

    def __init__(self):
        self.app_id = settings.FACEBOOK_APP_ID
        self.app_secret = settings.FACEBOOK_APP_SECRET
        self.redirect_uri = settings.FACEBOOK_REDIRECT_URI

    def get_auth_url(self, state: str, *, code_verifier=None) -> str:
        # ``code_verifier`` is accepted for the uniform service interface used
        # by the API routes; Meta's OAuth does not use PKCE, so it is ignored.
        return "https://www.facebook.com/v21.0/dialog/oauth?" + urlencode({
            "client_id": self.app_id, "redirect_uri": self.redirect_uri,
            "scope": self.SCOPES, "response_type": "code", "state": state,
        })

    async def exchange_code(self, code: str, *, code_verifier=None) -> Dict[str, Any]:
        """Exchange the code for a user token, then pick the Page to connect.

        Returns the *Page* access token (scoped to that Page) — that is what
        ``upload_video`` posts with. Every Page ``/me/accounts`` returns is
        considered: the first one the user can create content on wins,
        falling back to the first Page that carries a token at all.
        """
        # ``code_verifier`` is accepted for the uniform service interface used
        # by the API routes; Meta's OAuth does not use PKCE, so it is ignored.
        params = {"client_id": self.app_id, "client_secret": self.app_secret,
                  "redirect_uri": self.redirect_uri, "code": code}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.GRAPH_API}/oauth/access_token", params=params) as r:
                data = await r.json()
            if "error" in data: raise ValueError(data["error"].get("message", "Facebook OAuth failed"))
            user_token = data.get("access_token")
            async with session.get(f"{self.GRAPH_API}/me/accounts", params={
                "fields": "id,name,access_token,tasks", "access_token": user_token}) as r:
                pages = await r.json()
            if pages.get("error"): raise ValueError(pages["error"].get("message", "Could not read Facebook Pages"))
            page_list = pages.get("data") or []
            if not page_list:
                raise ValueError(await self._diagnose_no_pages(session, user_token))

        page = _pick_page(page_list)
        if page is None:
            logger.info(
                "Facebook connect: %d Page(s) listed but none carries an access token: %s",
                len(page_list),
                ", ".join(f"{p.get('name') or '?'} ({p.get('id')})" for p in page_list if isinstance(p, dict)),
            )
            raise ValueError(NO_PAGE_TOKEN)
        if len(page_list) > 1:
            logger.info(
                "Facebook connect: %d Pages listed; connecting %s (%s)",
                len(page_list), page.get("name") or "?", page.get("id"),
            )
        # Store the selected Page token; it is scoped to that Page.
        return {"access_token": page["access_token"], "refresh_token": None,
                "expires_in": data.get("expires_in")}

    async def _diagnose_no_pages(self, session: aiohttp.ClientSession, user_token: Optional[str]) -> str:
        """Explain an empty ``/me/accounts`` (see ``services.social.meta_graph``)."""
        scopes = await token_scopes(session, self.GRAPH_API, user_token or "", self.app_id, self.app_secret)
        if scopes is None:
            logger.warning("Facebook connect: /me/accounts was empty and the token could not be inspected")
            return NO_FACEBOOK_PAGE
        logger.info("Facebook connect: /me/accounts was empty; token scopes=%s", sorted(scopes))
        if PAGES_SHOW_LIST not in scopes:
            return MISSING_PAGES_PERMISSION
        return NO_FACEBOOK_PAGE

    async def refresh_access_token(self, refresh_token: Optional[str], current_access_token: Optional[str] = None):
        raise ValueError("Facebook Page access expired. Reconnect Facebook.")

    async def get_account_info(self, access_token: str) -> Dict[str, str]:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.GRAPH_API}/me", params={"fields": "id,name", "access_token": access_token}) as r:
                data = await r.json()
        if data.get("error"): raise ValueError(data["error"].get("message", "Facebook account lookup failed"))
        return {"account_id": data.get("id", ""), "account_name": data.get("name", "")}

    async def upload_video(self, video_path: str, description: str, access_token: str):
        form = aiohttp.FormData()
        form.add_field("source", open(video_path, "rb"), filename="video.mp4", content_type="video/mp4")
        form.add_field("description", description)
        form.add_field("access_token", access_token)
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.GRAPH_API}/me/videos", data=form) as r:
                data = await r.json()
        if data.get("error"): raise ValueError(data["error"].get("message", "Facebook upload failed"))
        video_id = data.get("id") or data.get("video_id")
        return {"video_id": video_id, "video_url": f"https://www.facebook.com/{video_id}"}


def _pick_page(pages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Choose the Page to connect from ``/me/accounts``.

    Prefers the first Page the user can create content on (that is what an
    upload needs); otherwise the first Page that carries an access token.
    Pages without a token cannot be posted to and are skipped — previously
    a tokenless ``pages[0]`` aborted the connect even when a usable Page
    was next in the list.
    """
    with_token = [p for p in pages if isinstance(p, dict) and p.get("access_token")]
    for page in with_token:
        tasks = page.get("tasks")
        if isinstance(tasks, (list, tuple, set)) and CREATE_CONTENT_TASKS.intersection(map(str, tasks)):
            return page
    return with_token[0] if with_token else None
