"""YouTube Shorts integration.

Ported from social_scheduler/services/youtube.py. Behaviour-preserving apart
from the fixes needed for the OAuth routes and the worker to actually work:

* ``get_auth_url``/``exchange_code`` use ``google_auth_oauthlib.flow.Flow``
  with a *web* client config and an explicit ``redirect_uri``. The original
  used ``InstalledAppFlow`` and never set ``redirect_uri``, so Google rejected
  the authorization URL ("Missing required parameter: redirect_uri").
* ``get_auth_url`` takes the caller's signed ``state`` (CSRF protection for
  the callback) instead of omitting it.
* ``refresh_access_token`` is exposed so the worker can renew an expired
  token and persist it (the original refreshed implicitly and discarded it).
* The blocking google-api-python-client calls run in a worker thread
  (``asyncio.to_thread``) so they no longer stall the API's event loop.
"""
import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.config import settings


class YouTubeService:
    """Service for YouTube Shorts upload and management."""

    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]
    TOKEN_URI = "https://oauth2.googleapis.com/token"
    AUTH_URI = "https://accounts.google.com/o/oauth2/auth"

    def __init__(self):
        self.client_id = settings.YOUTUBE_CLIENT_ID
        self.client_secret = settings.YOUTUBE_CLIENT_SECRET
        self.redirect_uri = settings.YOUTUBE_REDIRECT_URI

    # ── OAuth ────────────────────────────────────────────────────────────────

    def _client_config(self) -> dict:
        return {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": self.AUTH_URI,
                "token_uri": self.TOKEN_URI,
                "redirect_uris": [self.redirect_uri],
            }
        }

    def _flow(self):
        from google_auth_oauthlib.flow import Flow

        return Flow.from_client_config(
            self._client_config(), scopes=self.SCOPES, redirect_uri=self.redirect_uri
        )

    def get_auth_url(self, state: str) -> str:
        """Generate the OAuth authorization URL."""
        auth_url, _ = self._flow().authorization_url(
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
            state=state,
        )
        return auth_url

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        """Exchange an authorization code for tokens."""

        def _exchange():
            flow = self._flow()
            flow.fetch_token(code=code)
            return flow.credentials

        credentials = await asyncio.to_thread(_exchange)
        return {
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "expires_in": _seconds_until(credentials.expiry),
        }

    async def refresh_access_token(
        self, refresh_token: Optional[str], current_access_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Mint a new access token from the refresh token."""
        if not refresh_token:
            raise Exception("YouTube did not issue a refresh token. Reconnect YouTube.")

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=self.TOKEN_URI,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )
        try:
            await asyncio.to_thread(credentials.refresh, Request())
        except Exception as exc:  # google.auth.exceptions.RefreshError etc.
            raise Exception(f"YouTube token refresh failed: {exc}. Reconnect YouTube.") from exc
        return {
            "access_token": credentials.token,
            # Google keeps the same refresh token unless the user revoked it.
            "refresh_token": credentials.refresh_token or refresh_token,
            "expires_in": _seconds_until(credentials.expiry),
        }

    # ── Account ──────────────────────────────────────────────────────────────

    async def get_channel_info(self, access_token: str, refresh_token: Optional[str] = None) -> Dict[str, str]:
        """Get YouTube channel information."""
        from googleapiclient.errors import HttpError

        def _fetch():
            youtube = self._client(access_token, refresh_token)
            return youtube.channels().list(part="snippet", mine=True).execute()

        try:
            response = await asyncio.to_thread(_fetch)
        except HttpError as e:
            raise Exception(f"YouTube API error: {e}")

        items = response.get("items") or []
        if not items:
            raise Exception(
                "The connected Google account has no YouTube channel. Create one, then reconnect."
            )
        channel = items[0]
        return {
            "account_id": channel.get("id", ""),
            "account_name": channel.get("snippet", {}).get("title", "YouTube Channel"),
            "extra_data": {},
        }

    get_account_info = get_channel_info

    # ── Publish ──────────────────────────────────────────────────────────────

    async def upload_short(
        self,
        video_path: str,
        title: str,
        description: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        on_tokens_callback=None,
    ) -> Dict[str, str]:
        """Upload a video as a YouTube Short."""
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        # Validate video file
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        if os.path.getsize(video_path) == 0:
            raise ValueError("Video file is empty")

        # Ensure #Shorts is in title and description
        short_title = title if "#Shorts" in title else f"{title} #Shorts"
        short_description = f"{description}\n\n#Shorts"

        body = {
            "snippet": {
                "title": short_title[:100],  # Max 100 chars
                "description": short_description[:5000],
                "categoryId": "22",  # People & Blogs
                "tags": ["Shorts", "Short"],
                "defaultLanguage": "en",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        def _upload():
            youtube = self._client(access_token, refresh_token)
            media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
            return youtube.videos().insert(part="snippet,status", body=body, media_body=media).execute()

        try:
            response = await asyncio.to_thread(_upload)
        except HttpError as e:
            error_detail = e.error_details[0] if e.error_details else {}
            reason = error_detail.get("reason", "unknown") if isinstance(error_detail, dict) else "unknown"
            message = error_detail.get("message", str(e)) if isinstance(error_detail, dict) else str(e)

            # Provide actionable error messages
            if reason == "youtubeSignupRequired":
                raise Exception("Create a YouTube channel for the connected Google account, then reconnect it.")
            elif reason == "uploadLimitExceeded":
                raise Exception("The channel has reached its daily upload limit. Try again later.")
            elif reason in ("quotaExceeded", "dailyLimitExceeded"):
                raise Exception("The Google Cloud project has exhausted its YouTube API quota.")
            elif e.resp.status == 401:
                raise Exception("Reconnect YouTube to grant a fresh upload token.")
            elif e.resp.status == 403:
                raise Exception("Confirm YouTube Data API v3 is enabled and the account owns a YouTube channel.")
            raise Exception(f"YouTube upload failed: {message}")
        except (FileNotFoundError, ValueError):
            raise
        except Exception as e:
            raise Exception(f"YouTube upload error: {e}")

        video_id = response.get("id", "")
        if not video_id:
            raise Exception("YouTube API did not return a video ID")
        return {
            "video_id": video_id,
            "video_url": f"https://www.youtube.com/shorts/{video_id}",
        }

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _client(self, access_token: str, refresh_token: Optional[str]):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=self.TOKEN_URI,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )
        return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def _seconds_until(expiry: Optional[datetime]) -> Optional[int]:
    if expiry is None:
        return None
    if expiry.tzinfo is None:  # google-auth returns naive UTC
        expiry = expiry.replace(tzinfo=timezone.utc)
    return max(0, int((expiry - datetime.now(timezone.utc)).total_seconds()))
