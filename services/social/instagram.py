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
import ipaddress
import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse

import aiohttp

from core.config import settings

from .meta_graph import (
    GRAPH_API_BASE,
    GRAPH_API_VERSION,
    OAUTH_DIALOG,
    PAGES_SHOW_LIST,
    signed_in_account_name,
    token_scopes,
)

logger = logging.getLogger(__name__)

# Host for Instagram's resumable video upload. Graph API calls go to
# graph.facebook.com; the video bytes themselves go here (Meta documents the
# two hosts separately for this flow).
RUPLOAD_BASE = "https://rupload.facebook.com/ig-api-upload"

#: Uploads are big and Meta's processing is slow, so this flow gets its own
#: generous timeouts instead of aiohttp's 5-minute default.
_UPLOAD_TIMEOUT = aiohttp.ClientTimeout(total=3600, connect=30, sock_read=300)

#: How many times a single direct-upload step is retried when the failure is a
#: transient transport error (connection reset, timeout) rather than a Meta
#: rejection. Reels can be large, so one dropped connection on a flaky uplink
#: must not throw away the whole publish. Overridable for operators.
_DIRECT_UPLOAD_ATTEMPTS = int(os.getenv("INSTAGRAM_DIRECT_UPLOAD_ATTEMPTS", "3"))
_DIRECT_UPLOAD_RETRY_BASE_SECONDS = float(os.getenv("INSTAGRAM_DIRECT_UPLOAD_RETRY_BASE_SECONDS", "1.5"))


async def _retry_transient(action, attempts: int = _DIRECT_UPLOAD_ATTEMPTS):
    """Run an async ``action``, retrying only transient transport failures.

    Meta's *business* errors (an ``ERROR`` container status, a rejected upload,
    an auth failure) are raised immediately so the caller can fall back to the
    URL flow or surface them to the user. Only network-level interruptions are
    retried; each attempt re-runs the step from the start, which is safe here —
    a container lost before its id was read is simply abandoned, and the byte
    upload restarts from offset 0 against the same container.
    """
    last: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return await action()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last = exc
            if attempt >= attempts:
                raise Exception(
                    f"Instagram upload transport error ({exc.__class__.__name__}): {exc}; "
                    "response_body=<none: no HTTP response was received>"
                ) from exc
            delay = _DIRECT_UPLOAD_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "Instagram upload step hit a transient error (%s); retrying %d/%d in %.1fs",
                exc.__class__.__name__,
                attempt + 1,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
    raise last  # pragma: no cover — the loop always raises on its last attempt


def is_public_video_url(url: Optional[str]) -> bool:
    """True when Instagram's servers could actually download ``url``.

    The URL-flow needs a video Meta can fetch from the internet. An instance
    running on a laptop produces ``http://localhost:8000/...`` (or no
    PUBLIC_API_URL at all), which is exactly the case the direct upload exists
    for — so this decides between the two flows and produces the error text
    when neither can work.
    """
    value = (url or "").strip()
    if not value:
        return False
    try:
        host = (urlparse(value).hostname or "").strip().lower()
    except ValueError:
        return False
    if not host or urlparse(value).scheme not in ("http", "https"):
        return False
    if host in ("localhost", "0.0.0.0", "::1") or host.endswith(".localhost"):
        return False
    if host.startswith("[") and host.endswith("]"):  # bracketed IPv6
        host = host[1:-1]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True  # a hostname — assume it resolves publicly
    # A literal LAN/loopback/link-local address is reachable from this laptop
    # and from nowhere Meta operates, so it is not "public" even though it
    # looks like a real URL (http://192.168.1.5:8000/... is the classic case).
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )

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

    # Derived from services/social/meta_graph.py so the Instagram and Facebook
    # Page integrations always speak the same (currently supported) Graph API
    # version — this was pinned to v18.0, which Meta retired in Jan 2026.
    GRAPH_API = GRAPH_API_BASE
    SCOPES = "instagram_basic,instagram_content_publish"

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
        return f"{OAUTH_DIALOG}?{urlencode(params)}"

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
        self,
        ig_user_id: str,
        video_url: str,
        caption: str,
        access_token: str,
        *,
        video_path: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
    ) -> Dict[str, str]:
        """Publish a video as an Instagram Reel.

        Two ways to get the bytes to Meta:

        * **direct upload** (default, ``video_path`` + ``INSTAGRAM_DIRECT_UPLOAD``)
          — create a *resumable* container, stream the local file to
          ``rupload.facebook.com``, wait for processing, publish. Nothing has
          to be publicly reachable, which is what lets an instance running on
          a laptop publish without a tunnel or a CDN.
        * **URL flow** — Instagram downloads the video from ``video_url``, so
          it must be public.

        The direct path is tried first when it is available and the URL path
        is the fallback. Falling back is safe against double-posting: every
        failure below happens *before* ``media_publish`` succeeds, so at worst
        an unpublished container is abandoned.
        """
        if not ig_user_id:
            raise Exception("Instagram account id is missing. Reconnect Instagram.")

        # Instagram's caption ceiling; the API rejects a longer one.
        caption = (caption or "")[:2200]

        url_is_public = is_public_video_url(video_url)

        # The video is uploaded by the app to its own storage, so the bytes are
        # on this server for BOTH "publish now" and scheduled posts. The direct
        # (resumable) upload is therefore the default delivery and never needs
        # a public URL; the URL flow stays as the fallback for an instance that
        # has a public address (or that explicitly disabled direct upload).
        if settings.INSTAGRAM_DIRECT_UPLOAD and video_path:
            normalized_path = video_path
            temporary_path: Optional[str] = None
            try:
                if settings.INSTAGRAM_NORMALIZE_VIDEO:
                    normalized_path = temporary_path = await self._normalize_video(video_path)
                return await self._publish_reel_direct(
                    ig_user_id, normalized_path, caption, access_token, thumbnail_url=thumbnail_url
                )
            except Exception as exc:
                # A missing local file surfaces as FileNotFoundError; if the
                # URL flow can take over (a public video_url) it does, otherwise
                # the original error is what the worker translates for the user.
                if not url_is_public:
                    raise
                logger.warning(
                    "Instagram direct upload failed (%s); retrying with the public video URL",
                    exc.__class__.__name__,
                )
            finally:
                if temporary_path:
                    try:
                        os.remove(temporary_path)
                    except OSError:
                        pass

        if not url_is_public:
            if settings.INSTAGRAM_DIRECT_UPLOAD:
                # Direct upload was requested but the stored file is gone (or
                # the direct upload failed above). The URL flow needs a public
                # address, so name both ways out.
                raise Exception(
                    "Instagram could not be given the video. The direct upload did not succeed "
                    "(the stored file may be missing) and this instance has no publicly reachable "
                    "video URL — set PUBLIC_API_URL or restore the uploaded file."
                )
            raise Exception(
                "Instagram direct upload is disabled on this instance and the stored video URL is "
                f"not publicly reachable ({video_url or 'unset'}). Enable INSTAGRAM_DIRECT_UPLOAD — "
                "the video is already stored on this server — or set PUBLIC_API_URL."
            )
        return await self._publish_reel_by_url(
            ig_user_id, video_url, caption, access_token, thumbnail_url=thumbnail_url
        )

    async def _normalize_video(self, video_path: str) -> str:
        """Create a temporary, broadly compatible MP4 for Meta's processor."""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "Instagram video normalization requires ffmpeg, but ffmpeg is not installed"
            )
        fd, output_path = tempfile.mkstemp(prefix="instagram-", suffix=".mp4")
        os.close(fd)
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", video_path,
            "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart", output_path,
        ]

        def _run() -> None:
            try:
                completed = subprocess.run(
                    command, capture_output=True, text=True, timeout=900, check=False
                )
            except Exception:
                try:
                    os.remove(output_path)
                except OSError:
                    pass
                raise
            if completed.returncode != 0:
                try:
                    os.remove(output_path)
                except OSError:
                    pass
                detail = (completed.stderr or "ffmpeg returned a non-zero exit code").strip()
                raise RuntimeError(f"Instagram video normalization failed: {detail[-2000:]}")

        await asyncio.to_thread(_run)
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("Instagram video normalization produced an empty file")
        logger.info("Normalized Instagram video %s → %s", video_path, output_path)
        return output_path

    # ── Direct (resumable) upload ────────────────────────────────────────────

    async def _publish_reel_direct(
        self, ig_user_id: str, video_path: str, caption: str, access_token: str,
        *, thumbnail_url: Optional[str] = None
    ) -> Dict[str, str]:
        """Container → stream the file to rupload → wait → publish."""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        file_size = os.path.getsize(video_path)
        if file_size == 0:
            raise ValueError("Video file is empty")

        async with aiohttp.ClientSession(timeout=_UPLOAD_TIMEOUT) as session:
            container_id, upload_uri = await _retry_transient(
                lambda: self._create_resumable_container(
                    session, ig_user_id, caption, access_token, thumbnail_url=thumbnail_url
                )
            )
            await _retry_transient(
                lambda: self._upload_video_bytes(session, upload_uri, video_path, file_size, access_token)
            )
            await _retry_transient(
                lambda: self._wait_for_processing(session, container_id, access_token)
            )
            return await self._publish_container(session, ig_user_id, container_id, access_token)

    async def _create_resumable_container(
        self, session: aiohttp.ClientSession, ig_user_id: str, caption: str, access_token: str,
        *, thumbnail_url: Optional[str] = None
    ) -> Tuple[str, str]:
        """Open a resumable upload session; returns ``(container_id, upload_uri)``.

        ``upload_type=resumable`` is what makes this a *session* rather than a
        URL fetch — the response carries the rupload URI the bytes go to.
        """
        payload = {
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
            "share_to_feed": "true",
            "access_token": access_token,
        }
        if thumbnail_url and is_public_video_url(thumbnail_url):
            payload["cover_url"] = thumbnail_url
        async with session.post(f"{self.GRAPH_API}/{ig_user_id}/media", data=payload) as response:
            data = await response.json(content_type=None)
        _raise_on_error(data, "Instagram resumable container creation failed")
        container_id = str(data.get("id") or "")
        if not container_id:
            raise Exception("Instagram did not return a media container id")
        # Meta returns the full rupload URI; build it if a response omits it.
        upload_uri = data.get("uri") or f"{RUPLOAD_BASE}/{GRAPH_API_VERSION}/{container_id}"
        return container_id, str(upload_uri)

    async def _upload_video_bytes(
        self,
        session: aiohttp.ClientSession,
        upload_uri: str,
        video_path: str,
        file_size: int,
        access_token: str,
    ) -> None:
        """Stream the local file to Meta's upload host.

        ``offset`` is 0 because a Reel is uploaded in one pass here; the
        protocol exists so an interrupted upload can be resumed by sending the
        rest with the offset Meta reports in ``video_status``.
        """
        headers = {
            # rupload takes an OAuth scheme header, not the Graph API's
            # access_token form field.
            "Authorization": f"OAuth {access_token}",
            "offset": "0",
            "file_size": str(file_size),
            # Meta's own sample posts the raw file with `curl --data-binary`,
            # which sends this content type; matching it keeps the request
            # byte-for-byte the documented one.
            "Content-Type": "application/x-www-form-urlencoded",
        }
        with open(video_path, "rb") as handle:
            async with session.post(upload_uri, data=handle, headers=headers) as response:
                status = response.status
                raw = await response.text()

        data = _maybe_json(raw)
        if status >= 400 or (isinstance(data, dict) and not data.get("success")):
            # The failure body nests the real reason inside debug_info.message.
            debug = data.get("debug_info") if isinstance(data, dict) else None
            detail = str(debug.get("message") or debug.get("type") or "") if isinstance(debug, dict) else ""
            if isinstance(data, dict):
                detail = detail or str(data.get("message") or "rejected")
            raise Exception(
                f"Instagram video upload failed (HTTP {status}): "
                f"{detail or 'rejected'}; response_body={_format_response_body(data, raw)}"
            )
        if not isinstance(data, dict):
            # An empty 200, or a proxy's HTML error page. Not treated as a
            # failure: the container status poll that follows is the real gate,
            # and a false failure here would abandon a perfectly good upload.
            logger.warning(
                "Instagram upload answered HTTP %s with a non-JSON body (%d bytes); "
                "relying on the container status",
                status,
                len(raw or ""),
            )
        logger.info("Instagram direct upload complete: %s bytes → %s", file_size, upload_uri.rsplit("/", 1)[0])

    # ── URL flow ─────────────────────────────────────────────────────────────

    async def _publish_reel_by_url(
        self, ig_user_id: str, video_url: str, caption: str, access_token: str,
        *, thumbnail_url: Optional[str] = None
    ) -> Dict[str, str]:
        """The classic flow: Instagram downloads the video from ``video_url``."""
        container_data = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": access_token,
        }
        if thumbnail_url and is_public_video_url(thumbnail_url):
            container_data["cover_url"] = thumbnail_url
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.GRAPH_API}/{ig_user_id}/media", data=container_data) as response:
                container_response = await response.json(content_type=None)
            _raise_on_error(container_response, "Instagram container creation failed")
            creation_id = container_response.get("id")
            if not creation_id:
                raise Exception("Failed to create Instagram media container")

            await self._wait_for_processing(session, creation_id, access_token)
            return await self._publish_container(session, ig_user_id, creation_id, access_token)

    async def _publish_container(
        self, session: aiohttp.ClientSession, ig_user_id: str, creation_id: str, access_token: str
    ) -> Dict[str, str]:
        """Publish a processed container and resolve the Reel's permalink."""
        publish_data = {"creation_id": creation_id, "access_token": access_token}
        async with session.post(f"{self.GRAPH_API}/{ig_user_id}/media_publish", data=publish_data) as response:
            publish_response = await response.json(content_type=None)
        _raise_on_error(publish_response, "Instagram publish failed")
        media_id = publish_response.get("id", "")

        # Resolve the permalink; fall back to the reel URL shape if it fails.
        post_url = f"https://www.instagram.com/reel/{media_id}/"
        try:
            async with session.get(
                f"{self.GRAPH_API}/{media_id}",
                params={"fields": "permalink", "access_token": access_token},
            ) as response:
                link_data = await response.json(content_type=None)
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
        """Wait for Instagram video processing to complete.

        ``video_status`` is requested too: for a direct upload it carries the
        upload/processing phase detail, which is what makes a failure here
        diagnosable instead of a bare "ERROR".
        """
        for _attempt in range(max_attempts):
            async with session.get(
                f"{self.GRAPH_API}/{creation_id}",
                params={"fields": "status_code,status,video_status", "access_token": access_token},
            ) as response:
                data = await response.json(content_type=None)
            _raise_on_error(data, "Instagram processing status failed")

            status_code = data.get("status_code")
            if status_code == "FINISHED":
                return
            if status_code == "ERROR":
                detail = data.get("status", "Unknown error")
                video_status = data.get("video_status")
                if isinstance(video_status, dict):
                    phases = {
                        phase: (video_status.get(phase) or {}).get("status")
                        for phase in ("uploading_phase", "processing_phase")
                    }
                    detail = f"{detail} ({phases})"
                raise Exception(
                    f"Instagram video processing failed: {detail}; "
                    f"response_body={_format_response_body(data, data)}"
                )
            await asyncio.sleep(poll_seconds)
        raise Exception(
            "Instagram video processing timed out; response_body="
            "The API never returned FINISHED or ERROR within the polling window"
        )


def _maybe_json(raw: Any) -> Any:
    """Parse a response body that is *usually* JSON but not always.

    The upload host answers ``{"success": true, ...}`` on the happy path, but
    an empty body or an intermediary's HTML page shows up in the wild; the
    caller distinguishes the cases instead of crashing on a decode error.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _format_response_body(data: Any, raw: Any, max_chars: int = 12000) -> str:
    """Return a bounded, readable upstream body for post/debug diagnostics."""
    if isinstance(data, (dict, list)):
        body = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    elif isinstance(raw, (bytes, bytearray)):
        body = raw.decode("utf-8", "replace")
    else:
        body = str(raw or "")
    body = body.strip() or "<empty response body>"
    if len(body) > max_chars:
        return body[:max_chars] + f"… [truncated; {len(body)} chars total]"
    return body


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
            raise Exception(
                f"{prefix}: {message}"
                + (f" (code {code})" if code else "")
                + f"; response_body={_format_response_body(data, data)}"
            )
        raise Exception(f"{prefix}: {err}; response_body={_format_response_body(data, data)}")
