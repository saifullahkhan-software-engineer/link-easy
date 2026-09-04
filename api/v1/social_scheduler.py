"""
Social post scheduler — API routes.
FILE: api/v1/social_scheduler.py

Ported from the standalone social_scheduler/main.py into the main app:

* every route authenticates with ``get_current_user`` and scopes its queries
  to ``current_user.email`` (rows of other users 404, never 403 — no
  existence oracle);
* video uploads are stored under a server-generated name and referenced by
  ``upload_id``; the client never supplies a filesystem path;
* OAuth is complete: ``/platforms/{platform}/auth-url`` → provider →
  ``/platforms/{platform}/callback`` → frontend settings page. The callback
  is a bare browser redirect (no Authorization header), so the caller's
  identity travels in a short-lived signed ``state`` JWT minted by the
  auth-url route — that same token is the CSRF check. For YouTube the state
  also carries the PKCE ``code_verifier`` so the callback's token exchange
  uses the same verifier that produced the authorization URL's challenge;
* tokens are AES-256-GCM encrypted before they touch the database
  (services/social/connections.py) and never appear in a response.

Route map (prefix /api/v1/social-scheduler):

  GET    /posts                       list (optional ?status=&from=&to=)
  POST   /posts                       schedule a post
  GET    /posts/{id}
  PATCH  /posts/{id}                  edit / reschedule / cancel
  DELETE /posts/{id}
  POST   /upload                      multipart video upload → upload_id (+ duration)
  POST   /uploads/{id}/trim           re-encode [start,end) in place → fresh upload meta
  POST   /uploads/{id}/thumbnail      frame of the clip OR uploaded image → public URL
  POST   /parse-copy                  pasted message → per-platform copy (Groq)
  GET    /platforms                   connection status for all 4 platforms
  GET    /platforms/youtube/playlists channel playlists for the upload editor
  GET    /share-targets               saved manual-share destinations (FB Groups)
  POST   /share-targets               save one
  DELETE /share-targets/{id}          remove one
  GET    /platforms/{p}/auth-url      start OAuth (returns provider URL)
  GET    /platforms/{p}/callback      provider redirect target (unauthenticated;
                                      identity comes from the signed state)
  DELETE /platforms/{p}               disconnect
  GET    /platforms/credentials                     operator app-credential status (admin)
  PUT    /platforms/credentials/{p}                 save app credentials (admin)
  DELETE /platforms/credentials/{p}                 remove saved app credentials (admin)
  GET    /stats
  GET    /calendar?month=YYYY-MM
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db, require_admin
from api.rate_limit_deps import rate_limit
from core.config import settings
from models.social_scheduler import (
    PlatformCredential,
    ShareTarget,
    SocialPlatform,
    SocialPlatformConnection,
    SocialPost,
    SocialPostResult,
    SocialPostStatus,
)
from models.user import User
from schemas.social_scheduler import (
    PLATFORM_LABELS,
    PLATFORM_VALUES,
    CalendarDay,
    ParseCopyRequest,
    ParseCopyResponse,
    PlatformAuthUrlResponse,
    PlatformConnectionResponse,
    PlatformCredentialsIn,
    PlatformCredentialsMessageResponse,
    PlatformCredentialsResponse,
    PlatformDisconnectResponse,
    PostCreate,
    PostDeleteResponse,
    PostResponse,
    PostUpdate,
    ShareTargetDeleteResponse,
    ShareTargetIn,
    ShareTargetResponse,
    StatsResponse,
    ThumbnailResponse,
    TrimRequest,
    UploadResponse,
    YouTubePlaylist,
    YouTubePlaylistListResponse,
)
from services.ai.copy_parser import (
    CopyParseError,
    CopyProviderUnavailable,
    get_copy_parser,
)
from services.social import get_service
from services.social.connections import apply_tokens, read_tokens, reconnect_required
from services.social.video_editor import (
    VideoEditError,
    extract_frame,
    ffmpeg_available,
    probe_duration,
    trim_video,
    write_jpeg_thumbnail,
)
from services.social.credentials import (
    apply_credentials,
    configured_from_credentials,
    credential_field_names,
    delete_credentials,
    effective_credentials,
    load_credential,
    platform_configured,
    upsert_credentials,
)
from services.social.pkce import generate_code_verifier, is_valid_code_verifier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/social-scheduler", tags=["social-scheduler"])

# Public path under which uploaded videos are served (mounted in main.py).
UPLOADS_URL_PREFIX = "/uploads/social"
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/x-m4v", "video/webm", "application/octet-stream"}
# Upload ids are the server-generated basename: <uuid>.<ext>
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}\.(mp4|mov|m4v|webm)$")
OAUTH_STATE_TTL = timedelta(minutes=10)
OAUTH_STATE_TOKEN_TYPE = "social_oauth_state"


# ── helpers ──────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    """Timestamps come back naive from SQLite and aware from Postgres."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _platform_or_404(platform: str) -> str:
    value = (platform or "").strip().lower()
    if value not in PLATFORM_VALUES:
        raise HTTPException(status_code=404, detail=f"Unknown platform '{platform}'")
    return value


def _upload_dir() -> str:
    path = os.path.abspath(settings.UPLOAD_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def _upload_path(upload_id: str) -> str:
    """Absolute path of an upload by id. Rejects anything that is not one of ours."""
    if not _UPLOAD_ID_RE.match(upload_id or ""):
        raise HTTPException(status_code=400, detail="Invalid upload_id")
    return os.path.join(_upload_dir(), upload_id)


def _public_video_url(request: Request, upload_id: str) -> str:
    base = settings.PUBLIC_API_URL.rstrip("/") if settings.PUBLIC_API_URL else str(request.base_url).rstrip("/")
    return f"{base}{UPLOADS_URL_PREFIX}/{upload_id}"


def _upload_file_or_404(upload_id: str) -> str:
    """Resolve a stored upload to its path or raise a client-friendly error."""
    path = _upload_path(upload_id)  # 400 when upload_id is not one of ours
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Upload not found — upload the video again")
    return path


def _thumbnail_path(upload_id: str) -> str:
    """Absolute path of the single JPEG thumbnail attached to an upload.

    The thumb reuses the upload's uuid stem with a fixed ``.thumb.jpg`` name,
    so replacing it (video frame → uploaded image → another frame) just
    overwrites one file and the public URL never changes.
    """
    stem = (upload_id or "")[:32]
    return os.path.join(_upload_dir(), f"{stem}.thumb.jpg")


def _public_thumbnail_url(request: Request, upload_id: str) -> str:
    thumb_name = os.path.basename(_thumbnail_path(upload_id))
    base = settings.PUBLIC_API_URL.rstrip("/") if settings.PUBLIC_API_URL else str(request.base_url).rstrip("/")
    return f"{base}{UPLOADS_URL_PREFIX}/{thumb_name}"


def _require_ffmpeg() -> None:
    if not ffmpeg_available():
        raise HTTPException(
            status_code=503,
            detail="Video editing is not available on this instance (no ffmpeg binary).",
        )


async def _probe_duration_optional(path: str) -> Optional[float]:
    """Best-effort duration probe; None when ffmpeg is unavailable or fails."""
    if not ffmpeg_available():
        return None
    try:
        return await asyncio.to_thread(probe_duration, path)
    except VideoEditError as exc:
        logger.warning("Could not probe video duration for %s: %s", path, exc)
        return None


async def _owned_post(db: AsyncSession, post_id: str, owner_email: str) -> SocialPost:
    result = await db.execute(
        select(SocialPost).where(SocialPost.id == post_id, SocialPost.owner_email == owner_email)
    )
    post = result.scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


async def _owned_connection(
    db: AsyncSession, owner_email: str, platform: str
) -> Optional[SocialPlatformConnection]:
    result = await db.execute(
        select(SocialPlatformConnection).where(
            SocialPlatformConnection.owner_email == owner_email,
            SocialPlatformConnection.platform == platform,
        )
    )
    return result.scalar_one_or_none()


def _connection_response(
    platform: str,
    conn: Optional[SocialPlatformConnection],
    configured: bool,
) -> PlatformConnectionResponse:
    if conn is None:
        return PlatformConnectionResponse(
            platform=platform, label=PLATFORM_LABELS[platform], connected=False, configured=configured
        )
    return PlatformConnectionResponse(
        platform=platform,
        label=PLATFORM_LABELS[platform],
        connected=True,
        configured=configured,
        account_name=conn.account_name or "",
        account_id=conn.account_id or "",
        expires_at=conn.expires_at,
        reconnect_required=reconnect_required(conn),
        connected_at=conn.created_at,
        updated_at=conn.updated_at,
    )


async def _platform_configured_map(db: AsyncSession) -> dict[str, bool]:
    """Effective per-platform ``configured`` flag for the caller's DB session.

    A ``platform_credentials`` DB row overrides the environment pair
    (services/social/credentials.py), so configuration is no longer purely an
    environment question — it must be resolved per request.
    """
    result = await db.execute(select(PlatformCredential))
    rows = {row.platform: row for row in result.scalars().all()}
    configured_map: dict[str, bool] = {}
    for platform in PLATFORM_VALUES:
        row = rows.get(platform)
        creds = effective_credentials(platform, row)
        configured_map[platform] = configured_from_credentials(platform, creds)
    return configured_map


def _redirect_uri(request: Request, platform: str) -> str:
    """The callback URL registered with the provider (must match exactly)."""
    configured = {
        "youtube": settings.YOUTUBE_REDIRECT_URI,
        "instagram": settings.INSTAGRAM_REDIRECT_URI,
        "tiktok": settings.TIKTOK_REDIRECT_URI,
        "facebook": settings.FACEBOOK_REDIRECT_URI,
    }[platform]
    if configured:
        return configured
    base = settings.PUBLIC_API_URL.rstrip("/") if settings.PUBLIC_API_URL else str(request.base_url).rstrip("/")
    return f"{base}{router.prefix}/platforms/{platform}/callback"


def _service_for(request: Request, platform: str):
    service = get_service(platform)
    service.redirect_uri = _redirect_uri(request, platform)
    return service


def _mint_oauth_state(
    owner_email: str, platform: str, code_verifier: Optional[str] = None
) -> str:
    now = _now()
    payload = {
        "sub": owner_email,
        "platform": platform,
        "nonce": uuid.uuid4().hex,
        "iat": now,
        "exp": now + OAUTH_STATE_TTL,
        "token_type": OAUTH_STATE_TOKEN_TYPE,
    }
    # YouTube requires PKCE. The verifier is generated once, signed into the
    # state (integrity-protected, 10-minute TTL) and restored at the callback
    # so the exact verifier that produced the authorization URL's
    # code_challenge is sent in the token exchange. google-auth-oauthlib's
    # auto-generated verifier must NOT be used — the callback builds a fresh
    # Flow and would otherwise lose it ("Missing code verifier").
    if code_verifier:
        payload["code_verifier"] = code_verifier
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _read_oauth_state(state: Optional[str], platform: str) -> dict:
    """Return the validated signed-state payload, else raise.

    Signature, algorithm, expiry, ``token_type``, platform binding and owner
    are all verified exactly as before; the optional PKCE verifier is
    additionally checked against the RFC 7636 shape so a malformed claim is
    rejected rather than sent to Google.
    """
    if not state:
        raise ValueError("missing state")
    try:
        payload = jwt.decode(state, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise ValueError(f"invalid state: {exc}") from exc
    if payload.get("token_type") != OAUTH_STATE_TOKEN_TYPE or payload.get("platform") != platform:
        raise ValueError("state does not match this platform")
    owner = payload.get("sub")
    if not owner:
        raise ValueError("state carries no user")
    verifier = payload.get("code_verifier")
    if verifier is not None and not is_valid_code_verifier(verifier):
        raise ValueError("state carries an invalid PKCE code verifier")
    return payload


def _frontend_redirect(platform: str, *, connected: bool = False, error: Optional[str] = None) -> RedirectResponse:
    params = {"platform": platform}
    if connected:
        params["connected"] = "1"
    if error:
        params["error"] = error[:300]
    return RedirectResponse(url=f"{settings.social_oauth_return_url}?{urlencode(params)}", status_code=302)


def _humanize_delta(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "now"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if not days and minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return "in " + " ".join(parts) if parts else "in less than a minute"


# ── posts ────────────────────────────────────────────────────────────────────


@router.get("/posts", response_model=list[PostResponse])
async def list_posts(
    status_filter: Optional[str] = Query(None, alias="status"),
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(SocialPost).where(SocialPost.owner_email == current_user.email)
    if status_filter:
        wanted = [s.strip().lower() for s in status_filter.split(",") if s.strip()]
        stmt = stmt.where(SocialPost.status.in_(wanted))
    if date_from:
        stmt = stmt.where(SocialPost.scheduled_at >= date_from)
    if date_to:
        stmt = stmt.where(SocialPost.scheduled_at <= date_to)
    stmt = stmt.order_by(SocialPost.scheduled_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().unique().all()


@router.post("/posts", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
async def create_post(
    payload: PostCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    video_path = _upload_path(payload.upload_id)
    if not os.path.isfile(video_path):
        raise HTTPException(status_code=400, detail="Upload not found — upload the video again")

    # Platforms that are not connected can still be scheduled (the worker
    # reports a clear per-platform failure), but tell the user up front.
    # Direct uploads use the same durable queue, but are dispatched immediately
    # after the database commit instead of waiting for the next Beat tick.
    scheduled_at = _now() if payload.publish_now else payload.scheduled_at
    if scheduled_at is None:
        raise HTTPException(status_code=400, detail="scheduled_at is required when scheduling a post")
    if not payload.publish_now and scheduled_at < _now() - timedelta(minutes=1):
        raise HTTPException(status_code=400, detail="scheduled_at must be in the future")

    post = SocialPost(
        owner_email=current_user.email,
        title=payload.title,
        caption=payload.caption,
        hashtags=payload.hashtags,
        video_path=video_path,
        video_url=_public_video_url(request, payload.upload_id),
        thumbnail=payload.thumbnail,
        platforms=payload.platforms,
        scheduled_at=scheduled_at,
        status=SocialPostStatus.PENDING.value,
        youtube_title=payload.youtube_title,
        instagram_caption=payload.instagram_caption,
        tiktok_caption=payload.tiktok_caption,
        platform_copy=payload.platform_copy,
        youtube_playlist_ids=payload.youtube_playlist_ids,
        # Stored as plain dicts: the column is JSON, so pydantic models would
        # not serialise.
        facebook_groups=[group.model_dump() for group in payload.facebook_groups],
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)

    if payload.publish_now:
        # The pending row is intentional: if Redis/Celery is temporarily
        # unavailable, the normal one-minute dispatcher can still pick it up.
        try:
            from worker.celery_app import celery_app

            celery_app.send_task("tasks.publish_social_post", args=[post.id])
        except Exception as exc:  # pragma: no cover - broker availability is deployment-specific
            logger.warning("Could not dispatch direct social upload %s: %s", post.id, exc)

    return post


@router.get("/posts/{post_id}", response_model=PostResponse)
async def get_post(
    post_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _owned_post(db, post_id, current_user.email)


@router.patch("/posts/{post_id}", response_model=PostResponse)
async def update_post(
    post_id: str,
    payload: PostUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = await _owned_post(db, post_id, current_user.email)
    if post.status == SocialPostStatus.POSTING.value:
        raise HTTPException(status_code=409, detail="This post is being published right now and cannot be edited")

    update_data = payload.model_dump(exclude_unset=True)
    new_status = update_data.pop("status", None)
    # ``model_dump`` has already turned the groups into plain dicts, so the
    # *presence* of the key is what matters here ("not sent" vs "clear the
    # list") and the values are read back off the validated payload below.
    groups_were_sent = "facebook_groups" in update_data
    update_data.pop("facebook_groups", None)

    if update_data and post.status == SocialPostStatus.POSTED.value:
        raise HTTPException(status_code=409, detail="A published post cannot be edited")

    for field, value in update_data.items():
        setattr(post, field, value)
    if groups_were_sent:
        # The column is JSON, so it takes plain dicts, not pydantic models.
        post.facebook_groups = [group.model_dump() for group in payload.facebook_groups or []]

    if new_status == SocialPostStatus.CANCELLED.value:
        if post.status != SocialPostStatus.PENDING.value:
            raise HTTPException(status_code=409, detail="Only a scheduled post can be cancelled")
        post.status = SocialPostStatus.CANCELLED.value
    elif new_status == SocialPostStatus.PENDING.value and post.status in (
        SocialPostStatus.FAILED.value,
        SocialPostStatus.CANCELLED.value,
    ):
        # Re-queue: wipe the previous per-platform outcomes so the worker
        # publishes everything again from a clean slate.
        for result_row in list(post.results):
            await db.delete(result_row)
        post.status = SocialPostStatus.PENDING.value

    if post.status == SocialPostStatus.PENDING.value and _aware(post.scheduled_at) < _now() - timedelta(minutes=1):
        raise HTTPException(status_code=400, detail="scheduled_at must be in the future")

    await db.commit()
    await db.refresh(post)
    return post


@router.delete("/posts/{post_id}", response_model=PostDeleteResponse)
async def delete_post(
    post_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = await _owned_post(db, post_id, current_user.email)
    if post.status == SocialPostStatus.POSTING.value:
        raise HTTPException(status_code=409, detail="This post is being published right now and cannot be deleted")

    video_path = post.video_path
    await db.delete(post)
    await db.commit()

    # Remove the file only if no other post of this user still references it.
    other = await db.execute(select(func.count()).select_from(SocialPost).where(SocialPost.video_path == video_path))
    if other.scalar_one() == 0:
        # The thumbnail shares the upload's uuid stem, so removing the clip
        # clears its thumbnail too.
        thumb_path = _thumbnail_path(os.path.basename(video_path))
        for candidate in (video_path, thumb_path):
            try:
                if os.path.commonpath([os.path.abspath(candidate), _upload_dir()]) == _upload_dir():
                    os.remove(candidate)
            except FileNotFoundError:
                pass
            except Exception as exc:  # never fail the delete over a stray file
                logger.warning("Could not remove upload %s: %s", candidate, exc)

    return PostDeleteResponse(message="Post deleted", id=post_id)


# ── upload ───────────────────────────────────────────────────────────────────


@router.post("/upload", response_model=UploadResponse)
async def upload_video(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext or '(none)'}'. Upload an MP4, MOV, M4V or WEBM video.",
        )
    if file.content_type and file.content_type not in ALLOWED_VIDEO_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported content type {file.content_type}")

    upload_id = f"{uuid.uuid4().hex}{ext}"
    destination = _upload_path(upload_id)
    limit = settings.MAX_UPLOAD_SIZE
    written = 0
    try:
        with open(destination, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Video is larger than the {limit // (1024 * 1024)} MB limit",
                    )
                out.write(chunk)
    except HTTPException:
        _safe_remove(destination)
        raise
    except Exception as exc:
        _safe_remove(destination)
        logger.exception("Video upload failed for %s", current_user.email)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc
    finally:
        await file.close()

    if written == 0:
        _safe_remove(destination)
        raise HTTPException(status_code=400, detail="The uploaded file is empty")

    # Report the clip's duration so the upload editor can draw its trim
    # scrubber. Best-effort: when ffmpeg is absent (or the file is unusual) the
    # value is None and the page simply hides the trim controls.
    duration = await _probe_duration_optional(destination)
    if duration is not None:
        logger.info("Uploaded %s for %s: %.2fs", upload_id, current_user.email, duration)

    return UploadResponse(
        upload_id=upload_id,
        filename=file.filename or upload_id,
        size_bytes=written,
        content_type=file.content_type or "video/mp4",
        video_url=_public_video_url(request, upload_id),
        duration_seconds=duration,
    )


@router.post("/uploads/{upload_id}/trim", response_model=UploadResponse)
async def trim_upload(
    upload_id: str,
    payload: TrimRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Trim the stored clip to ``[start, end)`` and re-encode it in place.

    The trimmed file *replaces* the original upload on disk (same upload_id,
    same public URL), so whatever the editor keeps is exactly what the worker
    later streams to the platforms — no change to scheduling or publishing is
    needed. Returning the same shape as ``POST /upload`` lets the frontend swap
    its upload state with the response and re-render a preview.
    """
    _require_ffmpeg()
    path = _upload_file_or_404(upload_id)
    extension = os.path.splitext(upload_id)[1].lower()

    try:
        total = await asyncio.to_thread(probe_duration, path)
    except VideoEditError as exc:
        raise HTTPException(status_code=422, detail=f"Could not read the video: {exc}")

    start = min(max(payload.start, 0.0), total)
    end = total if payload.end is None else min(max(payload.end, 0.0), total)
    if end - start < 0.1:
        raise HTTPException(
            status_code=400,
            detail="The trimmed clip would be too short. Keep at least 0.1 s.",
        )
    if end < start:
        raise HTTPException(status_code=400, detail="The end time must be after the start time.")

    # Nothing worth re-encoding: keep the original file untouched.
    changed = start > 0.05 or end < total - 0.05
    if changed:
        temporary = f"{path}.{uuid.uuid4().hex[:8]}.tmp{extension or '.mp4'}"
        try:
            await asyncio.to_thread(
                trim_video, path, temporary, start, end - start, extension
            )
        except VideoEditError as exc:
            _safe_remove(temporary)
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            _safe_remove(temporary)
            logger.exception("Trim failed for %s", upload_id)
            raise HTTPException(status_code=500, detail="Could not trim the video.") from exc
        # Atomic replace so a crash mid-encode never leaves a half-written file
        # at the upload's real path.
        os.replace(temporary, path)

    new_size = os.path.getsize(path)
    logger.info("Trimmed %s for %s → [%.2f, %.2f] (%.2fs)", upload_id, current_user.email, start, end, end - start)
    return UploadResponse(
        upload_id=upload_id,
        filename=os.path.basename(upload_id),
        size_bytes=new_size,
        content_type="video/mp4" if extension != ".webm" else "video/webm",
        video_url=_public_video_url(request, upload_id),
        duration_seconds=round(end - start, 3),
    )


@router.post("/uploads/{upload_id}/thumbnail", response_model=ThumbnailResponse)
async def set_thumbnail(
    upload_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    file: Optional[UploadFile] = File(default=None),
    at: Optional[float] = Form(default=None),
):
    """Attach a thumbnail to an upload, from the video or from the user's PC.

    Two modes, chosen by whether a file is sent:

    * **File present** — the image is validated/normalised and stored (source
      ``"upload"``). Pillow re-encodes it to a bounded JPEG so a PNG/WebP/HEIC
      and a giant photo both end up as one small, consistent file.
    * **File absent** — a JPEG still is extracted from the clip at ``at``
      seconds (source ``"video_frame"``). ``at`` is clamped to the clip's
      duration, so a stale time from before a trim still works.

    Either way the image is written to ``<stem>.thumb.jpg`` next to the video
    and served at the returned public URL. It replaces any thumbnail already
    set for this upload.
    """
    path = _upload_file_or_404(upload_id)
    destination = _thumbnail_path(upload_id)
    source = "upload"
    frame_at: Optional[float] = None

    if file is not None:
        if file.content_type and not file.content_type.lower().startswith("image/"):
            raise HTTPException(status_code=400, detail="Thumbnail must be an image file.")
        data = await file.read(2 * 1024 * 1024 + 1)
        if not data:
            raise HTTPException(status_code=400, detail="The thumbnail file is empty.")
        if len(data) > 2 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Thumbnail image must be smaller than 2 MB.")
        try:
            await asyncio.to_thread(write_jpeg_thumbnail, data, destination)
        except VideoEditError as exc:
            _safe_remove(destination)
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            _safe_remove(destination)
            logger.exception("Thumbnail image save failed for %s", upload_id)
            raise HTTPException(status_code=500, detail="Could not save that thumbnail image.") from exc
        logger.info("Thumbnail image set for %s by %s", upload_id, current_user.email)
    else:
        _require_ffmpeg()
        try:
            total = await asyncio.to_thread(probe_duration, path)
        except VideoEditError as exc:
            raise HTTPException(status_code=422, detail=f"Could not read the video: {exc}")
        frame_at = 0.0 if at is None else min(max(at, 0.0), max(total - 0.01, 0.0))
        try:
            await asyncio.to_thread(extract_frame, path, destination, frame_at)
        except VideoEditError as exc:
            _safe_remove(destination)
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            _safe_remove(destination)
            logger.exception("Thumbnail frame failed for %s", upload_id)
            raise HTTPException(status_code=500, detail="Could not capture a thumbnail frame.") from exc
        source = "video_frame"
        logger.info("Thumbnail frame @%.2fs set for %s by %s", frame_at, upload_id, current_user.email)

    return ThumbnailResponse(
        upload_id=upload_id,
        thumbnail_url=_public_thumbnail_url(request, upload_id),
        source=source,
        at_seconds=frame_at,
    )


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except Exception:
        pass


# ── AI copy extraction ───────────────────────────────────────────────────────


@router.post(
    "/parse-copy",
    response_model=ParseCopyResponse,
    dependencies=[Depends(rate_limit("social:parse-copy"))],
)
async def parse_platform_copy(
    payload: ParseCopyRequest,
    current_user: User = Depends(get_current_user),
):
    """Split one pasted message into per-platform title/description/hashtags.

    The upload page sends the whole multi-platform message a user pasted and
    gets back the exact ``platform_copy`` structure its editor already keeps,
    so the response can be dropped into the form (and then into ``POST
    /posts``) unchanged.

    Groq reads the message; ``services/ai/copy_parser.py`` owns the prompt,
    the JSON repair and the field validation. The API key is read from this
    process's environment only — it is never accepted from, returned to or
    visible to the client.

    * 400 — nothing pasted;
    * 413 — the message is longer than ``GROQ_MAX_SOURCE_CHARS``;
    * 502 — the provider answered with something unusable (not JSON, or a
      shape the schema rejects);
    * 503 — no provider key is configured on this deployment.

    The pasted text is untrusted data: it is fenced as such in the prompt and
    only ever parsed as JSON, never interpreted (see the module docstring of
    the copy parser for the prompt-injection reasoning).
    """
    source_text = payload.source_text or ""
    if not source_text.strip():
        raise HTTPException(status_code=400, detail="Paste a message to extract the platform copy from")

    limit = settings.GROQ_MAX_SOURCE_CHARS
    if len(source_text) > limit:
        raise HTTPException(
            status_code=413,
            detail=(
                f"The pasted message is {len(source_text):,} characters — "
                f"the maximum is {limit:,}. Paste a shorter message."
            ),
        )

    started = time.perf_counter()
    parser = get_copy_parser()
    try:
        platform_copy = await parser.aparse(source_text)
    except CopyProviderUnavailable:
        # A deployment without a key. The upload page falls back to its own
        # parser, so this is a "feature off here" message, not a bug.
        raise HTTPException(
            status_code=503,
            detail="AI copy extraction is not configured on this instance. Use 'Use text to fill fields' instead.",
        )
    except CopyParseError:
        # The parser logged what went wrong (exception class, model name) —
        # the client gets advice, not the model's raw reply.
        raise HTTPException(
            status_code=502,
            detail="The AI service did not return usable copy. Try again, or fill the fields yourself.",
        )

    # Safe diagnostics: sizes and shape only. No key, no pasted text, no
    # model reply — any of those could carry the user's content.
    logger.info(
        "Copy parsed in %dms: %d source chars → %s (provider=%s)",
        int((time.perf_counter() - started) * 1000),
        len(source_text),
        ", ".join(
            f"{platform}:{sum(1 for value in fields if value)}"
            for platform, fields in platform_copy.model_dump().items()
        ),
        parser.provider,
    )
    return ParseCopyResponse(platform_copy=platform_copy)


# ── platform connections ─────────────────────────────────────────────────────


@router.get("/platforms", response_model=list[PlatformConnectionResponse])
async def list_platforms(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(SocialPlatformConnection).where(SocialPlatformConnection.owner_email == current_user.email)
    )
    by_platform = {c.platform: c for c in result.scalars().all()}
    configured_map = await _platform_configured_map(db)
    return [
        _connection_response(p, by_platform.get(p), configured_map[p])
        for p in PLATFORM_VALUES
    ]


@router.get("/platforms/{platform}/auth-url", response_model=PlatformAuthUrlResponse)
async def platform_auth_url(
    platform: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    platform = _platform_or_404(platform)
    if not await platform_configured(db, platform):
        raise HTTPException(
            status_code=503,
            detail=f"{PLATFORM_LABELS[platform]} is not configured on this instance "
            f"(missing OAuth app credentials). Ask the operator to set them.",
        )
    code_verifier = generate_code_verifier() if platform == "youtube" else None
    state = _mint_oauth_state(current_user.email, platform, code_verifier=code_verifier)
    try:
        service = _service_for(request, platform)
        row = await load_credential(db, platform)
        apply_credentials(service, platform, effective_credentials(platform, row))
        auth_url = service.get_auth_url(state, code_verifier=code_verifier)
    except Exception as exc:
        logger.exception("Could not build %s auth URL", platform)
        raise HTTPException(status_code=502, detail=f"Could not start {PLATFORM_LABELS[platform]} sign-in: {exc}")
    return PlatformAuthUrlResponse(platform=platform, auth_url=auth_url)


@router.get("/platforms/{platform}/callback", include_in_schema=False)
async def platform_oauth_callback(
    platform: str,
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Provider redirect target. Unauthenticated by necessity — identity is
    taken from the signed ``state`` minted by ``platform_auth_url``. Always
    ends in a redirect to the frontend settings page so the user is never
    left on a JSON page."""
    try:
        platform = _platform_or_404(platform)
    except HTTPException:
        return _frontend_redirect("unknown", error="Unknown platform")

    if error:
        return _frontend_redirect(platform, error=error_description or error)

    try:
        state_payload = _read_oauth_state(state, platform)
        owner_email = state_payload["sub"]
    except ValueError as exc:
        logger.warning("Rejected %s OAuth callback: %s", platform, exc)
        return _frontend_redirect(platform, error="Sign-in expired or was tampered with. Try again.")

    if not code:
        return _frontend_redirect(platform, error="The provider returned no authorization code")

    user = (await db.execute(select(User).where(User.email == owner_email))).scalar_one_or_none()
    if user is None:
        return _frontend_redirect(platform, error="Account not found")

    service = _service_for(request, platform)
    try:
        # The callback's token exchange authenticates with the OAuth app's
        # id/secret, which may come from a DB override rather than the env —
        # apply the effective pair so an operator-configured app works here.
        row = await load_credential(db, platform)
        apply_credentials(service, platform, effective_credentials(platform, row))
        # Only YouTube carries a verifier; the other providers receive None
        # and ignore it (uniform service interface).
        tokens = await service.exchange_code(
            code, code_verifier=state_payload.get("code_verifier")
        )
        info = await service.get_account_info(tokens["access_token"])
    except Exception as exc:
        logger.warning("%s OAuth for %s failed: %s", platform, owner_email, exc)
        return _frontend_redirect(platform, error=str(exc))

    conn = await _owned_connection(db, owner_email, platform)
    if conn is None:
        conn = SocialPlatformConnection(owner_email=owner_email, platform=platform, encrypted_access_token="")
        db.add(conn)
    try:
        apply_tokens(
            conn,
            access_token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            expires_in=tokens.get("expires_in"),
        )
    except ValueError as exc:
        return _frontend_redirect(platform, error=str(exc))
    conn.account_id = info.get("account_id") or ""
    conn.account_name = info.get("account_name") or ""
    conn.extra_data = info.get("extra_data") or {}

    try:
        await db.commit()
    except IntegrityError:
        # Two callbacks raced for the same (owner, platform); the other one won.
        await db.rollback()
        return _frontend_redirect(platform, connected=True)

    logger.info("%s connected for %s (%s)", platform, owner_email, conn.account_name)
    return _frontend_redirect(platform, connected=True)


@router.delete("/platforms/{platform}", response_model=PlatformDisconnectResponse)
async def disconnect_platform(
    platform: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    platform = _platform_or_404(platform)
    conn = await _owned_connection(db, current_user.email, platform)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"{PLATFORM_LABELS[platform]} is not connected")
    await db.delete(conn)
    await db.commit()
    return PlatformDisconnectResponse(message=f"{PLATFORM_LABELS[platform]} disconnected", platform=platform)


# ── Manual share targets (Facebook Groups) ───────────────────────────────────
# Meta removed the Groups API on 22 Apr 2024: there is no endpoint that posts a
# Reel into a group, and none is planned. These routes only store the
# destinations a user cares about, so the upload page can offer a picker and a
# published post can show a checklist. Nothing here is ever posted for them.
@router.get("/share-targets", response_model=list[ShareTargetResponse])
async def list_share_targets(
    platform: str = Query("facebook", pattern="^facebook$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ShareTarget)
        .where(ShareTarget.owner_email == current_user.email, ShareTarget.platform == platform)
        .order_by(ShareTarget.name.asc())
    )
    return result.scalars().all()


@router.post("/share-targets", response_model=ShareTargetResponse, status_code=status.HTTP_201_CREATED)
async def create_share_target(
    payload: ShareTargetIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Saving the same group twice is a no-op rather than a 409: the picker's
    # "add a group" field is used inline, and re-adding what is already there
    # should just select it.
    existing = (
        await db.execute(
            select(ShareTarget).where(
                ShareTarget.owner_email == current_user.email,
                ShareTarget.platform == "facebook",
                ShareTarget.url == payload.url,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if payload.name and existing.name != payload.name:
            existing.name = payload.name
            await db.commit()
            await db.refresh(existing)
        return existing

    target = ShareTarget(
        owner_email=current_user.email,
        platform="facebook",
        name=payload.name,
        url=payload.url,
    )
    db.add(target)
    try:
        await db.commit()
    except IntegrityError:  # raced with another tab saving the same URL
        await db.rollback()
        target = (
            await db.execute(
                select(ShareTarget).where(
                    ShareTarget.owner_email == current_user.email,
                    ShareTarget.platform == "facebook",
                    ShareTarget.url == payload.url,
                )
            )
        ).scalar_one()
    await db.refresh(target)
    return target


@router.delete("/share-targets/{target_id}", response_model=ShareTargetDeleteResponse)
async def delete_share_target(
    target_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target = (
        await db.execute(
            select(ShareTarget).where(
                ShareTarget.id == target_id, ShareTarget.owner_email == current_user.email
            )
        )
    ).scalar_one_or_none()
    # 404, not 403 — the same no-existence-oracle rule as the post routes.
    if target is None:
        raise HTTPException(status_code=404, detail="Share target not found")
    await db.delete(target)
    await db.commit()
    return ShareTargetDeleteResponse(id=target_id, message="Share target removed")


# ── YouTube playlist picker ──────────────────────────────────────────────────
# The upload editor needs the connected channel's playlists so a Short can be
# filed into one or more of them. Read-only, per user, and it uses the caller's
# own stored (decrypted) token — no other account's playlists are reachable
# through it. Registered before the ``{platform}`` routes so the literal path
# wins over the parameterised ones.
@router.get("/platforms/youtube/playlists", response_model=YouTubePlaylistListResponse)
async def list_youtube_playlists(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = await _owned_connection(db, current_user.email, "youtube")
    if conn is None:
        raise HTTPException(
            status_code=409,
            detail="YouTube is not connected. Connect it in Settings, then pick playlists.",
        )
    try:
        tokens = read_tokens(conn)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    service = get_service("youtube")
    apply_credentials(
        service, "youtube", effective_credentials("youtube", await load_credential(db, "youtube"))
    )

    # A token Google has already expired cannot list anything, so renew it the
    # same way the worker does before publishing. YouTube hands out refresh
    # tokens (unlike Facebook), so this is normally silent; if the renewal
    # fails the user is told to reconnect instead of being shown raw OAuth.
    if tokens.is_expired:
        try:
            renewed = await service.refresh_access_token(
                tokens.refresh_token, current_access_token=tokens.access_token
            )
            apply_tokens(
                conn,
                access_token=renewed.get("access_token", ""),
                refresh_token=renewed.get("refresh_token"),
                expires_in=renewed.get("expires_in"),
            )
            conn.updated_at = _now()
            await db.commit()
            tokens = read_tokens(conn)
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail=f"YouTube access has expired and could not be renewed: {exc}",
            )

    try:
        items = await service.list_playlists(tokens.access_token, tokens.refresh_token)
    except Exception as exc:
        # A 502, not a 500: this is an upstream (Google) failure. The picker
        # degrades gracefully — scheduling and publishing work without it.
        raise HTTPException(status_code=502, detail=str(exc)[:300] or "Could not load playlists")

    return YouTubePlaylistListResponse(
        playlists=[YouTubePlaylist(**item) for item in items],
        channel=conn.account_name or "",
    )


# ── Platform app credentials (operator-set DB overrides of the env pair) ─────
# GET/PUT/DELETE /platforms/credentials[/{platform}]. These are deployment
# settings (the OAuth app the whole instance signs in with), so they are
# admin-gated like every other operator surface; the *status* (configured or
# not) is public to authenticated users via GET /platforms above. Secrets are
# write-only — no response ever contains one.


@router.get("/platforms/credentials", response_model=list[PlatformCredentialsResponse])
async def list_platform_credentials(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_admin),
):
    result = await db.execute(select(PlatformCredential))
    rows = {row.platform: row for row in result.scalars().all()}
    response: list[PlatformCredentialsResponse] = []
    for platform in PLATFORM_VALUES:
        row = rows.get(platform)
        creds = effective_credentials(platform, row)
        # The identifier is not secret and may be echoed back to the operator
        # who saved it; the secret never leaves the server.
        identifier = row.client_id if row is not None and row.client_id else ""
        response.append(
            PlatformCredentialsResponse(
                platform=platform,
                label=PLATFORM_LABELS[platform],
                configured=configured_from_credentials(platform, creds),
                source=creds.get("source", "none"),
                identifier=identifier,
                has_secret=bool(row and row.client_secret),
                updated_at=row.updated_at if row is not None else None,
            )
        )
    return response


@router.put(
    "/platforms/credentials/{platform}",
    response_model=PlatformCredentialsMessageResponse,
)
async def put_platform_credentials(
    platform: str,
    payload: PlatformCredentialsIn,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_admin),
):
    platform = _platform_or_404(platform)
    identifier_field, secret_field = credential_field_names(platform)
    identifier = (getattr(payload, identifier_field) or "").strip()
    secret = (getattr(payload, secret_field) or "").strip()
    if not identifier or not secret:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Both {identifier_field} and {secret_field} are required to "
                f"set up {PLATFORM_LABELS[platform]} app credentials."
            ),
        )
    await upsert_credentials(db, platform, identifier, secret)
    logger.info("%s app credentials saved via the settings page", platform)
    return PlatformCredentialsMessageResponse(
        message=f"{PLATFORM_LABELS[platform]} app credentials saved",
        platform=platform,
    )


@router.delete(
    "/platforms/credentials/{platform}",
    response_model=PlatformCredentialsMessageResponse,
)
async def delete_platform_credentials(
    platform: str,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_admin),
):
    platform = _platform_or_404(platform)
    removed = await delete_credentials(db, platform)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"{PLATFORM_LABELS[platform]} has no app credentials saved in the database",
        )
    logger.info("%s app credentials removed via the settings page", platform)
    return PlatformCredentialsMessageResponse(
        message=f"{PLATFORM_LABELS[platform]} app credentials removed — environment values (if any) apply again",
        platform=platform,
    )


# ── stats / calendar ─────────────────────────────────────────────────────────


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now = _now()
    week_from_now = now + timedelta(days=7)
    owner = current_user.email

    status_counts = dict(
        (
            await db.execute(
                select(SocialPost.status, func.count())
                .where(SocialPost.owner_email == owner)
                .group_by(SocialPost.status)
            )
        ).all()
    )
    scheduled_this_week = (
        await db.execute(
            select(func.count()).select_from(SocialPost).where(
                and_(
                    SocialPost.owner_email == owner,
                    SocialPost.status == SocialPostStatus.PENDING.value,
                    SocialPost.scheduled_at >= now,
                    SocialPost.scheduled_at <= week_from_now,
                )
            )
        )
    ).scalar_one()
    next_post_at = (
        await db.execute(
            select(func.min(SocialPost.scheduled_at)).where(
                SocialPost.owner_email == owner,
                SocialPost.status == SocialPostStatus.PENDING.value,
                SocialPost.scheduled_at >= now,
            )
        )
    ).scalar_one()
    if next_post_at is not None:
        next_post_at = _aware(next_post_at)

    per_platform: dict[str, dict[str, int]] = {p: {"posted": 0, "failed": 0} for p in PLATFORM_VALUES}
    for platform, result_status, count in (
        await db.execute(
            select(SocialPostResult.platform, SocialPostResult.status, func.count())
            .where(SocialPostResult.owner_email == owner)
            .group_by(SocialPostResult.platform, SocialPostResult.status)
        )
    ).all():
        if platform in per_platform and result_status in per_platform[platform]:
            per_platform[platform][result_status] = count

    connected = (
        await db.execute(
            select(SocialPlatformConnection.platform).where(SocialPlatformConnection.owner_email == owner)
        )
    ).scalars().all()

    return StatsResponse(
        scheduled_this_week=scheduled_this_week,
        total_scheduled=status_counts.get(SocialPostStatus.PENDING.value, 0),
        total_published=status_counts.get(SocialPostStatus.POSTED.value, 0),
        total_failed=status_counts.get(SocialPostStatus.FAILED.value, 0),
        next_post_at=next_post_at,
        next_post_in=_humanize_delta(next_post_at - now) if next_post_at else None,
        connected_platforms=sorted(connected),
        per_platform=per_platform,
    )


@router.get("/calendar", response_model=list[CalendarDay])
async def get_calendar(
    month: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM (UTC); defaults to the current month"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now = _now()
    try:
        year, mon = (int(x) for x in month.split("-")) if month else (now.year, now.month)
        first = datetime(year, mon, 1, tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    last = first + timedelta(days=monthrange(year, mon)[1])

    result = await db.execute(
        select(SocialPost)
        .where(
            SocialPost.owner_email == current_user.email,
            SocialPost.scheduled_at >= first,
            SocialPost.scheduled_at < last,
        )
        .order_by(SocialPost.scheduled_at)
    )
    days: dict[str, list[SocialPost]] = {}
    for post in result.scalars().unique().all():
        days.setdefault(_aware(post.scheduled_at).astimezone(timezone.utc).strftime("%Y-%m-%d"), []).append(post)
    return [CalendarDay(date=day, posts=posts) for day, posts in sorted(days.items())]


__all__ = ["router", "SocialPlatform"]
