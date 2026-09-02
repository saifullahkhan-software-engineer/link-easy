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
  auth-url route — that same token is the CSRF check;
* tokens are AES-256-GCM encrypted before they touch the database
  (services/social/connections.py) and never appear in a response.

Route map (prefix /api/v1/social-scheduler):

  GET    /posts                       list (optional ?status=&from=&to=)
  POST   /posts                       schedule a post
  GET    /posts/{id}
  PATCH  /posts/{id}                  edit / reschedule / cancel
  DELETE /posts/{id}
  POST   /upload                      multipart video upload → upload_id
  GET    /platforms                   connection status for all 3 platforms
  GET    /platforms/{p}/auth-url      start OAuth (returns provider URL)
  GET    /platforms/{p}/callback      provider redirect target (unauthenticated;
                                      identity comes from the signed state)
  DELETE /platforms/{p}               disconnect
  GET    /stats
  GET    /calendar?month=YYYY-MM
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db
from core.config import settings
from models.social_scheduler import (
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
    PlatformAuthUrlResponse,
    PlatformConnectionResponse,
    PlatformDisconnectResponse,
    PostCreate,
    PostDeleteResponse,
    PostResponse,
    PostUpdate,
    StatsResponse,
    UploadResponse,
)
from services.social import get_service
from services.social.connections import apply_tokens, reconnect_required

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


def _connection_response(platform: str, conn: Optional[SocialPlatformConnection]) -> PlatformConnectionResponse:
    configured = settings.social_platform_configured(platform)
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


def _redirect_uri(request: Request, platform: str) -> str:
    """The callback URL registered with the provider (must match exactly)."""
    configured = {
        "youtube": settings.YOUTUBE_REDIRECT_URI,
        "instagram": settings.INSTAGRAM_REDIRECT_URI,
        "tiktok": settings.TIKTOK_REDIRECT_URI,
    }[platform]
    if configured:
        return configured
    base = settings.PUBLIC_API_URL.rstrip("/") if settings.PUBLIC_API_URL else str(request.base_url).rstrip("/")
    return f"{base}{router.prefix}/platforms/{platform}/callback"


def _service_for(request: Request, platform: str):
    service = get_service(platform)
    service.redirect_uri = _redirect_uri(request, platform)
    return service


def _mint_oauth_state(owner_email: str, platform: str) -> str:
    now = _now()
    return jwt.encode(
        {
            "sub": owner_email,
            "platform": platform,
            "nonce": uuid.uuid4().hex,
            "iat": now,
            "exp": now + OAUTH_STATE_TTL,
            "token_type": OAUTH_STATE_TOKEN_TYPE,
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


def _read_oauth_state(state: Optional[str], platform: str) -> str:
    """Return the owner email carried by a valid state token, else raise."""
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
    return owner


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
    if payload.scheduled_at < _now() - timedelta(minutes=1):
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
        scheduled_at=payload.scheduled_at,
        status=SocialPostStatus.PENDING.value,
        youtube_title=payload.youtube_title,
        instagram_caption=payload.instagram_caption,
        tiktok_caption=payload.tiktok_caption,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
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

    if update_data and post.status == SocialPostStatus.POSTED.value:
        raise HTTPException(status_code=409, detail="A published post cannot be edited")

    for field, value in update_data.items():
        setattr(post, field, value)

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
        try:
            if os.path.commonpath([os.path.abspath(video_path), _upload_dir()]) == _upload_dir():
                os.remove(video_path)
        except FileNotFoundError:
            pass
        except Exception as exc:  # never fail the delete over a stray file
            logger.warning("Could not remove upload %s: %s", video_path, exc)

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

    return UploadResponse(
        upload_id=upload_id,
        filename=file.filename or upload_id,
        size_bytes=written,
        content_type=file.content_type or "video/mp4",
        video_url=_public_video_url(request, upload_id),
    )


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except Exception:
        pass


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
    return [_connection_response(p, by_platform.get(p)) for p in PLATFORM_VALUES]


@router.get("/platforms/{platform}/auth-url", response_model=PlatformAuthUrlResponse)
async def platform_auth_url(
    platform: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    platform = _platform_or_404(platform)
    if not settings.social_platform_configured(platform):
        raise HTTPException(
            status_code=503,
            detail=f"{PLATFORM_LABELS[platform]} is not configured on this instance "
            f"(missing OAuth app credentials). Ask the operator to set them.",
        )
    state = _mint_oauth_state(current_user.email, platform)
    try:
        auth_url = _service_for(request, platform).get_auth_url(state)
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
        owner_email = _read_oauth_state(state, platform)
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
        tokens = await service.exchange_code(code)
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
