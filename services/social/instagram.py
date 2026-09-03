"""Instagram Reels integration (Meta Graph API).

Ported from social_scheduler/services/instagram.py. Behaviour-preserving
apart from:

* ``get_auth_url`` takes the caller's signed ``state`` and URL-encodes the
  query (the original interpolated raw values and hardcoded the state).
* ``refresh_access_token`` renews a long-lived user token (Meta has no
  refresh tokens; a long-lived token is exchanged for a fresh one while it is
  still valid). The worker calls it when ``expires_at`` has passed.
* Errors from the Graph API include Meta's error code where available.
* ``get_instagram_account_info`` checks every Page ``/me/accounts`` returns
  (the original only looked at the first) and, when the list is empty, asks
  ``debug_token`` which permissions the token really carries so the user is
  told *why* no Page was found instead of a generic "No Facebook Page found".
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp

from core.config import settings

from .meta_graph import PAGES_SHOW_LIST, signed_in_account_name, token_scopes

logger = logging.getLogger(__name__)

# The Graph API reaches an Instagram Business/Creator account *through* a
# Facebook Page, so connecting Instagram is really a Facebook Login whose
# ``/me/accounts`` must list a Page carrying ``instagram_business_account``.
# These are the three ways that lookup fails without the Graph API returning
# an ``error`` object; each message names the fix. They are surfaced verbatim
# to the user by the OAuth callback (``_frontend_redirect`` keeps the first
# 300 characters), so keep them under that.
NO_LINKED_INSTAGRAM_ACCOUNT = (
    "The connected Facebook account administers Facebook Pages, but none of them has an "
    "Instagram Business/Creator account linked. Link one (Instagram → Profile → Menu → "
    "Settings → Accounts Center → Linked accounts → Instagram), then reconnect."
)
MISSING_PAGES_PERMISSION = (
    "The Facebook sign-in was granted without the 'See a list of your Pages' permission "
    "(pages_show_list), so no Facebook Page can be listed. Disconnect Instagram and "
    "reconnect, approving every permission on Facebook's screen."
)
_NO_PAGE_REASON = (
    "does not administer any Facebook Page (or shared none with this app). This sign-in "
    "is separate from the Facebook Page connected elsewhere: sign in with the account "
    "that manages the Page linked to your Instagram, then reconnect."
)


def no_page_message(signed_in_name: Optional[str] = None) -> str:
    """``NO_FACEBOOK_PAGE`` naming the Facebook account that actually signed in.

    An empty ``/me/accounts`` in a fresh browser is almost always a sign-in to
    a *different* Facebook account than the one that administers the Page.
    Naming the account that completed the OAuth (from ``/me``) turns the
    message from "which account?" into "that's not the account you meant".
    When the name cannot be determined the generic wording is used.
    """
    if signed_in_name:
        name = signed_in_name.strip()
        if len(name) > 28:  # keep the message inside the 300-char redirect limit
            name = name[:28].rstrip() + "…"
        who = f" ({name})"
    else:
        who = ""
    return f"The signed-in Facebook account{who} {_NO_PAGE_REASON}"


NO_FACEBOOK_PAGE = no_page_message()


class InstagramService:
    """Service for Instagram Reels publishing."""

    GRAPH_API = "https://graph.facebook.com/v18.0"
    SCOPES = "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement"

    def __init__(self):
        self.app_id = settings.INSTAGRAM_APP_ID
        self.app_secret = settings.INSTAGRAM_APP_SECRET
        self.redirect_uri = settings.INSTAGRAM_REDIRECT_URI

    # ── OAuth ────────────────────────────────────────────────────────────────

    def get_auth_url(self, state: str, *, code_verifier=None) -> str:
        """Generate the OAuth authorization URL.

        ``code_verifier`` is accepted for the uniform service interface used
        by the API routes; Meta's OAuth does not use PKCE, so it is ignored.
        """
        params = {
            "client_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.SCOPES,
            "response_type": "code",
            "state": state,
        }
        return f"https://www.facebook.com/v18.0/dialog/oauth?{urlencode(params)}"

    async def exchange_code(self, code: str, *, code_verifier=None) -> Dict[str, Any]:
        """Exchange an authorization code for a (long-lived) access token.

        ``code_verifier`` is accepted for the uniform service interface used
        by the API routes; Meta's OAuth does not use PKCE, so it is ignored.
        """
        params = {
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "redirect_uri": self.redirect_uri,
            "code": code,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.GRAPH_API}/oauth/access_token", params=params) as response:
                data = await response.json()
        _raise_on_error(data, "Instagram token exchange failed")

        # The code grants a short-lived (~1h) token; swap it for the 60-day one
        # immediately so the stored connection is useful for scheduled posts.
        long_lived = await self.get_long_lived_token(data.get("access_token"))
        return {
            "access_token": long_lived["access_token"],
            "refresh_token": None,  # Meta issues no refresh tokens
            "expires_in": long_lived.get("expires_in") or data.get("expires_in"),
        }

    async def get_long_lived_token(self, short_token: str) -> Dict[str, Any]:
        """Exchange a short-lived token for a long-lived (60-day) token."""
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "fb_exchange_token": short_token,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.GRAPH_API}/oauth/access_token", params=params) as response:
                data = await response.json()
        _raise_on_error(data, "Instagram long-lived token failed")
        return {"access_token": data.get("access_token"), "expires_in": data.get("expires_in")}

    async def refresh_access_token(
        self, refresh_token: Optional[str], current_access_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Renew a long-lived token (must be called while it is still valid)."""
        if not current_access_token:
            raise Exception("Instagram token expired. Reconnect Instagram.")
        try:
            renewed = await self.get_long_lived_token(current_access_token)
        except Exception as exc:
            raise Exception(f"Instagram token renewal failed: {exc}. Reconnect Instagram.") from exc
        return {
            "access_token": renewed["access_token"],
            "refresh_token": None,
            "expires_in": renewed.get("expires_in"),
        }

    # ── Account ──────────────────────────────────────────────────────────────

    async def get_instagram_account_info(self, access_token: str) -> Dict[str, Any]:
        """Find the Instagram Business/Creator account behind the user's Pages.

        Lists every Facebook Page the user administers together with its
        ``instagram_business_account`` in one call and picks the first Page
        that has one — the Page linked to Instagram is frequently not the
        first Page in the list. When ``/me/accounts`` comes back empty the
        token is inspected via ``debug_token`` so the error distinguishes a
        sign-in that lacked the ``pages_show_list`` permission from an
        account that simply administers no Page.
        """
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.GRAPH_API}/me/accounts",
                params={"fields": "id,name,instagram_business_account", "access_token": access_token},
            ) as response:
                pages_data = await response.json()
            _raise_on_error(pages_data, "Failed to get Facebook pages")
            pages = pages_data.get("data") or []
            if not pages:
                raise Exception(await self._diagnose_no_pages(session, access_token))

            page, ig_account = _first_page_with_instagram(pages)
            if page is None:
                logger.info(
                    "Instagram connect: none of %d Facebook Page(s) has an Instagram account linked: %s",
                    len(pages),
                    ", ".join(f"{p.get('name') or '?'} ({p.get('id')})" for p in pages),
                )
                raise Exception(NO_LINKED_INSTAGRAM_ACCOUNT)
            ig_account_id = str(ig_account["id"])

            # Get account name
            async with session.get(
                f"{self.GRAPH_API}/{ig_account_id}",
                params={"fields": "username", "access_token": access_token},
            ) as response:
                name_data = await response.json()
            _raise_on_error(name_data, "Failed to get Instagram username")
            username = name_data.get("username", "Instagram Account")

        return {
            "account_id": ig_account_id,
            "account_name": username,
            "extra_data": {"page_id": str(page["id"])},
        }

    get_account_info = get_instagram_account_info

    async def _diagnose_no_pages(self, session: aiohttp.ClientSession, access_token: str) -> str:
        """Explain an empty ``/me/accounts`` list.

        Facebook lets the user untick individual permissions on the consent
        screen; without ``pages_show_list`` the call legitimately returns an
        empty list with no error. ``debug_token`` (authenticated with the app
        token ``app_id|app_secret``) reports which scopes the token actually
        carries. If that call fails for any reason, fall back to the
        "no Page" explanation rather than masking the original problem.
        """
        scopes = await token_scopes(session, self.GRAPH_API, access_token, self.app_id, self.app_secret)
        signed_in_as = await signed_in_account_name(session, self.GRAPH_API, access_token)
        if scopes is None:
            logger.warning("Instagram connect: /me/accounts was empty and the token could not be inspected")
            return no_page_message(signed_in_as)
        logger.info(
            "Instagram connect: /me/accounts was empty; token scopes=%s, signed in as %r",
            sorted(scopes), signed_in_as,
        )
        if PAGES_SHOW_LIST not in scopes:
            return MISSING_PAGES_PERMISSION
        return no_page_message(signed_in_as)

    # ── Publish ──────────────────────────────────────────────────────────────

    async def publish_reel(
        self, ig_user_id: str, video_url: str, caption: str, access_token: str
    ) -> Dict[str, str]:
        """Publish a video as an Instagram Reel (container → processing → publish)."""
        if not ig_user_id:
            raise Exception("Instagram account id is missing. Reconnect Instagram.")

        # Step 1: Create media container
        container_data = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption[:2200],  # Max 2200 chars
            "share_to_feed": "true",
            "access_token": access_token,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.GRAPH_API}/{ig_user_id}/media", data=container_data) as response:
                container_response = await response.json()
            _raise_on_error(container_response, "Instagram container creation failed")
            creation_id = container_response.get("id")
            if not creation_id:
                raise Exception("Failed to create Instagram media container")

            # Step 2: Wait for video processing
            await self._wait_for_processing(session, creation_id, access_token)

            # Step 3: Publish the container
            publish_data = {"creation_id": creation_id, "access_token": access_token}
            async with session.post(
                f"{self.GRAPH_API}/{ig_user_id}/media_publish", data=publish_data
            ) as response:
                publish_response = await response.json()
            _raise_on_error(publish_response, "Instagram publish failed")
            media_id = publish_response.get("id", "")

            # Resolve the permalink; fall back to the reel URL shape if it fails.
            post_url = f"https://www.instagram.com/reel/{media_id}/"
            try:
                async with session.get(
                    f"{self.GRAPH_API}/{media_id}",
                    params={"fields": "permalink", "access_token": access_token},
                ) as response:
                    link_data = await response.json()
                post_url = link_data.get("permalink") or post_url
            except Exception:
                pass

        return {"media_id": media_id, "post_url": post_url}

    async def _wait_for_processing(
        self,
        session: aiohttp.ClientSession,
        creation_id: str,
        access_token: str,
        max_attempts: int = 20,
        poll_seconds: float = 15.0,
    ) -> None:
        """Wait for Instagram video processing to complete."""
        for _attempt in range(max_attempts):
            async with session.get(
                f"{self.GRAPH_API}/{creation_id}",
                params={"fields": "status_code,status", "access_token": access_token},
            ) as response:
                data = await response.json()
            _raise_on_error(data, "Instagram processing status failed")

            status_code = data.get("status_code")
            if status_code == "FINISHED":
                return
            if status_code == "ERROR":
                raise Exception(f"Instagram video processing failed: {data.get('status', 'Unknown error')}")
            await asyncio.sleep(poll_seconds)
        raise Exception("Instagram video processing timed out")


def _first_page_with_instagram(pages: List[Dict[str, Any]]):
    """Return ``(page, instagram_business_account)`` for the first Page that
    has a linked Instagram account, else ``(None, None)``."""
    for page in pages:
        if not isinstance(page, dict):
            continue
        ig_account = page.get("instagram_business_account")
        if isinstance(ig_account, dict) and ig_account.get("id"):
            return page, ig_account
    return None, None


def _raise_on_error(data: Any, prefix: str) -> None:
    if isinstance(data, dict) and "error" in data:
        err = data["error"] or {}
        if isinstance(err, dict):
            message = err.get("message", "Unknown error")
            code = err.get("code")
            raise Exception(f"{prefix}: {message}" + (f" (code {code})" if code else ""))
        raise Exception(f"{prefix}: {err}")
