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
* PKCE is fully deterministic: the verifier is generated once by the caller
  and passed into ``_flow`` for BOTH the authorization URL and the token
  exchange, with ``autogenerate_code_verifier=False``. The authorization URL
  carries the matching S256 ``code_challenge`` and the callback's
  ``fetch_token`` sends the exact same ``code_verifier``, which is what Google
  validates against the stored challenge. Without this the callback built a
  fresh Flow whose auto-generated verifier never matched, and Google rejected
  the exchange with ``invalid_grant: Missing code verifier``.
* ``SCOPES`` now also requests ``userinfo.profile``: the OAuth client's scope
  set changed from ``youtube.readonly`` + ``youtube.upload`` to those two plus
  ``userinfo.profile``. ``get_channel_info`` uses the extra scope to enrich
  the stored connection metadata with the Google account profile
  (name/email/picture via the People API) — best-effort only, so an existing
  connection whose token predates the scope change (or a project without the
  People API enabled) still connects fine with just the channel info.
"""
import asyncio
import json
import os
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.config import settings
from services.social.pkce import generate_code_verifier


class YouTubeService:
    """Service for YouTube Shorts upload and management."""

    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
        # Playlist management. ``youtube.readonly`` can *list* playlists but
        # ``playlistItems.insert`` — filing the uploaded Short into one —
        # requires the full read/write scope (Google accepts youtube,
        # youtube.force-ssl or youtubepartner; this is the least privileged of
        # the three that is not a partner-only scope). Adding a scope changes
        # the consent screen, so a connection made before this change has to
        # reconnect YouTube once to pick it up; until then a playlist insert
        # comes back 403 and is recorded as a note on the post instead of
        # failing an upload that already succeeded.
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.force-ssl",
        # Added to the OAuth client's scope set; used to store which Google
        # account (name/email/picture) is connected, alongside the channel.
        "https://www.googleapis.com/auth/userinfo.profile",
    ]
    TOKEN_URI = "https://oauth2.googleapis.com/token"
    AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
    UPLOAD_RETRY_ATTEMPTS = 4

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

    def _flow(self, code_verifier: Optional[str] = None):
        from google_auth_oauthlib.flow import Flow

        return Flow.from_client_config(
            self._client_config(),
            scopes=self.SCOPES,
            redirect_uri=self.redirect_uri,
            code_verifier=code_verifier,
            # Never let the library generate its own verifier: the verifier
            # chosen at authorization-url time must be the one used to fetch
            # the token, or Google rejects the code ("Missing code verifier").
            autogenerate_code_verifier=False,
        )

    def get_auth_url(
        self, state: str, *, code_verifier: Optional[str] = None
    ) -> str:
        """Generate the OAuth authorization URL.

        ``code_verifier`` must be the value the caller will persist in the
        signed ``state`` and pass back to :meth:`exchange_code`. When omitted
        (direct service use outside the API routes) a fresh verifier is
        generated so the URL is still valid; the API routes always supply one
        and keep it in the signed state.
        """
        if code_verifier is None:
            code_verifier = generate_code_verifier()
        auth_url, _ = self._flow(code_verifier).authorization_url(
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
            state=state,
        )
        return auth_url

    async def exchange_code(
        self, code: str, *, code_verifier: Optional[str] = None
    ) -> Dict[str, Any]:
        """Exchange an authorization code for tokens.

        The verifier generated for the authorization URL is required here —
        without it the token exchange is rejected by Google.
        """
        if not code_verifier:
            raise ValueError(
                "YouTube OAuth state is missing its PKCE code verifier. Start the connection again."
            )

        def _exchange():
            flow = self._flow(code_verifier)
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
        """Get YouTube channel information, enriched (best-effort) with the
        Google account profile from the ``userinfo.profile`` scope."""
        from googleapiclient.errors import HttpError

        def _fetch():
            last_error = None
            for attempt in range(1, self.UPLOAD_RETRY_ATTEMPTS + 1):
                try:
                    youtube = self._client("youtube", "v3", access_token, refresh_token)
                    return youtube.channels().list(part="snippet", mine=True).execute()
                except (ssl.SSLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
                    last_error = exc
                    if attempt < self.UPLOAD_RETRY_ATTEMPTS:
                        time.sleep(2 ** (attempt - 1))
            raise last_error

        try:
            response = await asyncio.to_thread(_fetch)
        except HttpError as e:
            raise Exception(f"YouTube API error: {e}")
        except (ssl.SSLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            raise Exception(
                "YouTube connection failed after retries. Check your internet connection, "
                f"VPN/proxy/antivirus HTTPS inspection, then try again: {exc}"
            ) from exc

        items = response.get("items") or []
        if not items:
            raise Exception(
                "The connected Google account has no YouTube channel. Create one, then reconnect."
            )
        channel = items[0]
        return {
            "account_id": channel.get("id", ""),
            "account_name": channel.get("snippet", {}).get("title", "YouTube Channel"),
            "extra_data": await self._google_profile(access_token, refresh_token),
        }

    async def _google_profile(
        self, access_token: str, refresh_token: Optional[str]
    ) -> Dict[str, str]:
        """Fetch the signed-in Google account's profile via the People API.

        Uses the ``userinfo.profile`` scope. Fails open — returns ``{}`` if
        the scope is not in the token yet (connections made before the scope
        change) or the People API is not enabled on the project — the
        connection itself must never break over account metadata.
        """
        try:
            def _fetch():
                people = self._client("people", "v1", access_token, refresh_token)
                return people.people().get(
                    "me", params={"personFields": "names,emailAddresses,photos"}
                ).execute()

            profile = await asyncio.to_thread(_fetch)
        except Exception:
            return {}

        extra: Dict[str, str] = {}
        names = profile.get("names") or []
        if names and names[0].get("displayName"):
            extra["google_name"] = names[0]["displayName"]
        emails = profile.get("emailAddresses") or []
        if emails and emails[0]:
            extra["google_email"] = emails[0]
        photos = profile.get("photos") or []
        if photos and photos[0].get("url"):
            extra["google_picture"] = photos[0]["url"]
        return extra

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
        from googleapiclient.errors import HttpError, ResumableUploadError
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
            youtube = self._client("youtube", "v3", access_token, refresh_token)
            media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
            last_error = None
            for attempt in range(1, self.UPLOAD_RETRY_ATTEMPTS + 1):
                try:
                    response = None
                    while response is None:
                        _, response = request.next_chunk()
                    return response
                except HttpError as exc:
                    # Retry only server-side failures. A 4xx response is a
                    # definitive provider rejection and must retain its body.
                    if getattr(exc.resp, "status", 0) < 500:
                        raise
                    last_error = exc
                except (
                    ResumableUploadError,
                    ssl.SSLError,
                    socket.timeout,
                    TimeoutError,
                    ConnectionError,
                    OSError,
                ) as exc:
                    last_error = exc
                if attempt < self.UPLOAD_RETRY_ATTEMPTS:
                    time.sleep(2 ** (attempt - 1))
            raise last_error

        try:
            response = await asyncio.to_thread(_upload)
        except HttpError as e:
            error_detail = e.error_details[0] if e.error_details else {}
            reason = error_detail.get("reason", "unknown") if isinstance(error_detail, dict) else "unknown"
            message = error_detail.get("message", str(e)) if isinstance(error_detail, dict) else str(e)
            response_body = _google_response_body(e)

            # Provide actionable error messages
            if reason == "youtubeSignupRequired":
                raise Exception(
                    "Create a YouTube channel for the connected Google account, then reconnect it; "
                    f"response_body={response_body}"
                )
            elif reason == "uploadLimitExceeded":
                raise Exception(
                    "The channel has reached its daily upload limit. Try again later; "
                    f"response_body={response_body}"
                )
            elif reason in ("quotaExceeded", "dailyLimitExceeded"):
                raise Exception(
                    "The Google Cloud project has exhausted its YouTube API quota; "
                    f"response_body={response_body}"
                )
            elif e.resp.status == 401:
                raise Exception(
                    "Reconnect YouTube to grant a fresh upload token; "
                    f"response_body={response_body}"
                )
            elif e.resp.status == 403:
                raise Exception(
                    "Confirm YouTube Data API v3 is enabled and the account owns a YouTube channel; "
                    f"response_body={response_body}"
                )
            raise Exception(f"YouTube upload failed: {message}; response_body={response_body}")
        except (FileNotFoundError, ValueError):
            raise
        except Exception as e:
            # SSL resets happen before Google can send an HTTP response.
            raise Exception(
                f"YouTube upload transport error ({e.__class__.__name__}): {e}; "
                "response_body=<none: no HTTP response was received>"
            )

        video_id = response.get("id", "")
        if not video_id:
            raise Exception("YouTube API did not return a video ID")
        return {
            "video_id": video_id,
            "video_url": f"https://www.youtube.com/shorts/{video_id}",
        }

    # ── Playlists ────────────────────────────────────────────────────────────

    async def list_playlists(
        self,
        access_token: str,
        refresh_token: Optional[str] = None,
        *,
        max_results: int = 50,
        max_pages: int = 5,
    ) -> list[Dict[str, Any]]:
        """The connected channel's playlists, as shown in the upload editor.

        Paginates up to ``max_pages`` pages of 50 so a channel with more than
        one screenful of playlists still shows them all, then truncates at
        ``max_results`` — the picker is a convenience, not a playlist browser.
        """
        from googleapiclient.errors import HttpError

        def _fetch():
            last_error = None
            for attempt in range(1, self.UPLOAD_RETRY_ATTEMPTS + 1):
                try:
                    youtube = self._client("youtube", "v3", access_token, refresh_token)
                    collected: list[Dict[str, Any]] = []
                    page_token: Optional[str] = None
                    for _page in range(max_pages):
                        params: Dict[str, Any] = {
                            "part": "snippet,contentDetails,status",
                            "mine": True,
                            "maxResults": min(max_results, 50),
                        }
                        if page_token:
                            params["pageToken"] = page_token
                        response = youtube.playlists().list(**params).execute()
                        collected.extend(response.get("items") or [])
                        page_token = response.get("nextPageToken")
                        if not page_token or len(collected) >= max_results:
                            break
                    return collected[:max_results]
                except (ssl.SSLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
                    last_error = exc
                    if attempt < self.UPLOAD_RETRY_ATTEMPTS:
                        time.sleep(2 ** (attempt - 1))
            raise last_error

        try:
            items = await asyncio.to_thread(_fetch)
        except HttpError as exc:
            raise Exception(_http_error_message(exc, "YouTube playlist list failed"))
        except (ssl.SSLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            raise Exception(
                "YouTube playlist list connection failed after retries. Check your internet connection, "
                f"VPN/proxy/antivirus HTTPS inspection, then try again: {exc}"
            ) from exc
        except Exception as exc:
            raise Exception(f"YouTube playlist list error: {exc}")

        playlists = []
        for item in items:
            playlist_id = item.get("id") or ""
            if not playlist_id:
                continue
            playlists.append(
                {
                    "id": playlist_id,
                    "title": (item.get("snippet") or {}).get("title") or "(untitled playlist)",
                    "privacy": ((item.get("status") or {}).get("privacyStatus") or "").lower(),
                    "item_count": int((item.get("contentDetails") or {}).get("itemCount") or 0),
                }
            )
        return playlists

    async def add_to_playlists(
        self,
        video_id: str,
        playlist_ids: list[str],
        access_token: str,
        refresh_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """File one uploaded video into each playlist. Never raises.

        By the time this runs the Short is already public, so a playlist
        problem (a deleted playlist, a token without the manage scope, a full
        playlist) must not turn a successful publish into a failed post. The
        caller records the per-playlist outcome as a note instead.
        """
        from googleapiclient.errors import HttpError

        # Tolerate the shapes a JSON payload can arrive in: nulls, numbers and
        # stray whitespace, plus the same id twice (str(None) is "None", which
        # is why None is checked before it is stringified).
        wanted: list[str] = []
        for raw in playlist_ids or []:
            if raw is None:
                continue
            playlist_id = str(raw).strip()
            if playlist_id and playlist_id not in wanted:
                wanted.append(playlist_id)
        if not video_id or not wanted:
            return {"added": [], "failed": []}

        def _add():
            youtube = self._client("youtube", "v3", access_token, refresh_token)
            added: list[str] = []
            failed: list[Dict[str, str]] = []
            for playlist_id in wanted:
                body = {
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                }
                try:
                    youtube.playlistItems().insert(part="snippet", body=body).execute()
                    added.append(playlist_id)
                except HttpError as exc:
                    failed.append({"playlist_id": playlist_id, "error": _http_error_message(exc, "playlist insert failed")})
                except Exception as exc:
                    failed.append(
                        {"playlist_id": playlist_id, "error": str(exc) or exc.__class__.__name__}
                    )
            return added, failed

        added, failed = await asyncio.to_thread(_add)
        return {"added": added, "failed": failed}

    async def set_thumbnail(
        self,
        video_id: str,
        thumbnail_path: str,
        access_token: str,
        refresh_token: Optional[str] = None,
    ) -> None:
        """Set a custom JPEG thumbnail after the video upload succeeds."""
        from googleapiclient.http import MediaFileUpload

        if not os.path.exists(thumbnail_path):
            raise FileNotFoundError(f"Thumbnail file not found: {thumbnail_path}")

        def _set():
            youtube = self._client("youtube", "v3", access_token, refresh_token)
            media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg", resumable=False)
            return youtube.thumbnails().set(videoId=video_id, media_body=media).execute()

        await asyncio.to_thread(_set)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _client(
        self,
        service_name: str,
        version: str,
        access_token: str,
        refresh_token: Optional[str],
    ):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=self.TOKEN_URI,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )
        return build(service_name, version, credentials=credentials, cache_discovery=False)


def _seconds_until(expiry: Optional[datetime]) -> Optional[int]:
    if expiry is None:
        return None
    if expiry.tzinfo is None:  # google-auth returns naive UTC
        expiry = expiry.replace(tzinfo=timezone.utc)
    return max(0, int((expiry - datetime.now(timezone.utc)).total_seconds()))


def _http_error_message(exc: Exception, prefix: str) -> str:
    """Human-readable text for a googleapiclient ``HttpError``.

    ``str(HttpError)`` is ``<HttpError 403 "…json…">``, which is unreadable in
    a post's result row; the JSON body carries the reason and the message the
    user can act on ("The caller does not have permission", "playlistNotFound").
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    details = getattr(exc, "error_details", None) or []
    message = reason = ""
    if details and isinstance(details[0], dict):
        message = str(details[0].get("message") or "")
        reason = str(details[0].get("reason") or "")

    # A 403 on a playlist call is almost always a connection that predates the
    # playlist scope: Google's own wording ("The caller does not have
    # permission") does not tell the user that reconnecting fixes it, so the
    # fix is stated first and the upstream text kept for support.
    if status == 403:
        return (
            f"{prefix}: the connected Google account has not granted playlist access. "
            "Reconnect YouTube and approve the new permission."
            + (f" (Google said: {message})" if message else "")
            + f"; response_body={_google_response_body(exc)}"
        )
    if message:
        return (
            f"{prefix}: {message}"
            + (f" ({reason})" if reason else "")
            + f"; response_body={_google_response_body(exc)}"
        )
    return f"{prefix}: {exc}; response_body={_google_response_body(exc)}"


def _google_response_body(exc: Exception, max_chars: int = 12000) -> str:
    """Extract the complete bounded body carried by a Google HttpError."""
    content = getattr(exc, "content", None)
    if content is None:
        return "<none: Google error contained no response body>"
    if isinstance(content, (bytes, bytearray)):
        text = content.decode("utf-8", "replace")
    else:
        text = str(content)
    text = text.strip()
    if not text:
        return "<empty response body>"
    try:
        parsed = json.loads(text)
        text = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        pass
    if len(text) > max_chars:
        return text[:max_chars] + f"… [truncated; {len(text)} chars total]"
    return text
