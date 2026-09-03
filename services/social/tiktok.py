"""TikTok integration (Content Posting API v2).

Ported from social_scheduler/services/tiktok.py. Behaviour-preserving apart
from:

* ``get_auth_url`` takes the caller's signed ``state`` and URL-encodes the
  query (the original built a state from the event-loop clock and did not
  encode the scope's commas).
* ``refresh_access_token`` keeps the original ``refresh_token`` method under
  the shared cross-platform name so the worker can renew expired tokens.
* Uploads stream the file from disk (``aiohttp`` accepts a file object)
  instead of reading the whole video into memory; TikTok's response is
  checked before the file is opened.
"""
import asyncio
import os
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import aiohttp

from core.config import settings


class TikTokService:
    """Service for TikTok video publishing."""

    TIKTOK_API = "https://open.tiktokapis.com/v2"
    SCOPES = "user.info.basic,video.publish,video.upload"

    def __init__(self):
        self.client_key = settings.TIKTOK_CLIENT_KEY
        self.client_secret = settings.TIKTOK_CLIENT_SECRET
        self.redirect_uri = settings.TIKTOK_REDIRECT_URI

    # ── OAuth ────────────────────────────────────────────────────────────────

    def get_auth_url(self, state: str, *, code_verifier=None) -> str:
        """Generate the OAuth authorization URL.

        ``code_verifier`` is accepted for the uniform service interface used
        by the API routes; TikTok's OAuth does not use PKCE, so it is ignored.
        """
        params = {
            "client_key": self.client_key,
            "response_type": "code",
            "scope": self.SCOPES,
            "redirect_uri": self.redirect_uri,
            "state": state,
        }
        return f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(params)}"

    async def exchange_code(self, code: str, *, code_verifier=None) -> Dict[str, Any]:
        """Exchange an authorization code for access + refresh tokens.

        ``code_verifier`` is accepted for the uniform service interface used
        by the API routes; TikTok's OAuth does not use PKCE, so it is ignored.
        """
        params = {
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }
        data = await self._token_request(params, "TikTok token exchange failed")
        return {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "expires_in": data.get("expires_in"),
        }

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh the access token using a refresh token."""
        params = {
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        data = await self._token_request(params, "TikTok token refresh failed")
        return {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token") or refresh_token,
            "expires_in": data.get("expires_in"),
        }

    async def refresh_access_token(
        self, refresh_token: Optional[str], current_access_token: Optional[str] = None
    ) -> Dict[str, Any]:
        if not refresh_token:
            raise Exception("TikTok did not issue a refresh token. Reconnect TikTok.")
        try:
            return await self.refresh_token(refresh_token)
        except Exception as exc:
            raise Exception(f"{exc}. Reconnect TikTok.") from exc

    async def _token_request(self, params: dict, prefix: str) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.TIKTOK_API}/oauth/token/",
                data=params,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                data = await response.json()
        # TikTok's token endpoint reports errors as {"error": "...", "error_description": "..."}
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            desc = data.get("error_description") or (err.get("message") if isinstance(err, dict) else err)
            raise Exception(f"{prefix}: {desc or 'Unknown error'}")
        return data

    # ── Account ──────────────────────────────────────────────────────────────

    async def get_user_info(self, access_token: str) -> Dict[str, str]:
        """Get TikTok user information."""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.TIKTOK_API}/user/info/",
                params={"fields": "open_id,union_id,avatar_url,display_name"},
                headers={"Authorization": f"Bearer {access_token}"},
            ) as response:
                data = await response.json()
        _raise_on_api_error(data, "TikTok user info failed")
        user_data = data.get("data", {}).get("user", {})
        return {
            "account_id": user_data.get("open_id", ""),
            "account_name": user_data.get("display_name", "TikTok Account"),
            "extra_data": {"union_id": user_data.get("union_id", "")},
        }

    get_account_info = get_user_info

    # ── Publish ──────────────────────────────────────────────────────────────

    async def upload_video(self, video_path: str, caption: str, access_token: str) -> Dict[str, str]:
        """Upload a video to TikTok (init → single-chunk PUT → poll status)."""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        file_size = os.path.getsize(video_path)
        if file_size == 0:
            raise ValueError("Video file is empty")

        auth_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        async with aiohttp.ClientSession() as session:
            # Step 1: Initialize upload
            init_data = {
                "post_info": {
                    "title": caption[:2200],  # Max 2200 chars
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                    "video_cover_timestamp_ms": 1000,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": file_size,  # Single chunk for simplicity
                    "total_chunk_count": 1,
                },
            }
            async with session.post(
                f"{self.TIKTOK_API}/post/publish/video/init/", headers=auth_headers, json=init_data
            ) as response:
                init_response = await response.json()
            _raise_on_api_error(init_response, "TikTok init failed")
            data = init_response.get("data", {})
            publish_id = data.get("publish_id")
            upload_url = data.get("upload_url")
            if not publish_id or not upload_url:
                raise Exception("TikTok did not return upload URL")

            # Step 2: Upload the video file (streamed from disk)
            headers = {
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                "Content-Length": str(file_size),
            }
            with open(video_path, "rb") as video_file:
                async with session.put(upload_url, headers=headers, data=video_file) as response:
                    if not response.ok:
                        error_text = await response.text()
                        raise Exception(f"TikTok file upload failed: {response.status} {error_text}")

            # Step 3: Poll for publish status
            video_url = await self._poll_publish_status(session, publish_id, access_token)

        return {"publish_id": publish_id, "video_url": video_url}

    async def _poll_publish_status(
        self,
        session: aiohttp.ClientSession,
        publish_id: str,
        access_token: str,
        max_attempts: int = 30,
        poll_seconds: float = 10.0,
    ) -> str:
        """Poll for TikTok publish status."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        for _attempt in range(max_attempts):
            await asyncio.sleep(poll_seconds)
            async with session.post(
                f"{self.TIKTOK_API}/post/publish/status/fetch/",
                headers=headers,
                json={"publish_id": publish_id},
            ) as response:
                data = await response.json()
            _raise_on_api_error(data, "TikTok status check failed")

            publish_data = data.get("data", {})
            status = publish_data.get("status")
            if status == "PUBLISH_COMPLETE":
                video_ids = publish_data.get("publicaly_available_post_id", [])
                video_id = video_ids[0] if video_ids else publish_id
                return f"https://www.tiktok.com/@me/video/{video_id}"
            if status == "FAILED":
                raise Exception(f"TikTok publish failed: {publish_data.get('fail_reason', 'Unknown')}")
        raise Exception("TikTok publish timed out")


def _raise_on_api_error(data: Any, prefix: str) -> None:
    """TikTok v2 responses always carry {"error": {"code": "ok" | ..., "message"}}."""
    if not isinstance(data, dict):
        raise Exception(f"{prefix}: unexpected response")
    error = data.get("error")
    if isinstance(error, dict):
        if error.get("code") not in (None, "ok"):
            raise Exception(f"{prefix}: {error.get('message', 'Unknown error')} ({error.get('code')})")
    elif error:
        raise Exception(f"{prefix}: {error}")
