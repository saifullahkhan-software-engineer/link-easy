"""Instagram Reels integration (Meta Graph API).

Ported from social_scheduler/services/instagram.py. Behaviour-preserving
apart from:

* ``get_auth_url`` takes the caller's signed ``state`` and URL-encodes the
  query (the original interpolated raw values and hardcoded the state).
* ``refresh_access_token`` renews a long-lived user token (Meta has no
  refresh tokens; a long-lived token is exchanged for a fresh one while it is
  still valid). The worker calls it when ``expires_at`` has passed.
* Errors from the Graph API include Meta's error code where available.
"""
import asyncio
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import aiohttp

from core.config import settings


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

    async def get_instagram_account_info(self, access_token: str) -> Dict[str, str]:
        """Get the Instagram Business account linked to the user's first Page."""
        async with aiohttp.ClientSession() as session:
            # Get Facebook pages
            async with session.get(
                f"{self.GRAPH_API}/me/accounts", params={"access_token": access_token}
            ) as response:
                pages_data = await response.json()
            _raise_on_error(pages_data, "Failed to get Facebook pages")
            pages = pages_data.get("data", [])
            if not pages:
                raise Exception(
                    "No Facebook Page found. You need a Facebook Page linked to your Instagram Business account."
                )
            page = pages[0]

            # Get Instagram account linked to the page
            async with session.get(
                f"{self.GRAPH_API}/{page['id']}",
                params={"fields": "instagram_business_account", "access_token": access_token},
            ) as response:
                ig_data = await response.json()
            _raise_on_error(ig_data, "Failed to get Instagram account")
            ig_account = ig_data.get("instagram_business_account")
            if not ig_account:
                raise Exception("No Instagram Business/Creator account linked to your Facebook Page.")
            ig_account_id = ig_account["id"]

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
            "extra_data": {"page_id": page["id"]},
        }

    get_account_info = get_instagram_account_info

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


def _raise_on_error(data: Any, prefix: str) -> None:
    if isinstance(data, dict) and "error" in data:
        err = data["error"] or {}
        if isinstance(err, dict):
            message = err.get("message", "Unknown error")
            code = err.get("code")
            raise Exception(f"{prefix}: {message}" + (f" (code {code})" if code else ""))
        raise Exception(f"{prefix}: {err}")
