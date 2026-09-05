"""
Social post scheduler — Celery tasks.
FILE: worker/tasks/social_scheduler_tasks.py

Tasks:
  tasks.dispatch_due_social_posts   — Beat, every minute: find pending posts
                                      whose scheduled_at has passed, claim a
                                      Redis lease per post and queue one
                                      publish task each.
  tasks.publish_social_post         — publish ONE post to each of its
                                      platforms, refreshing an expired access
                                      token first, and record the outcome on
                                      social_post_results / social_posts.

Ported from social_scheduler/tasks/scheduler.py with the stubbed parts
completed:

  * the per-platform upload/publish calls are made for real and their
    ``platform_id``/``platform_url`` stored;
  * an expired access token is refreshed (YouTube/TikTok: refresh token;
    Instagram: long-lived token renewal) and the new token PERSISTED —
    encrypted — before the upload, instead of failing;
  * a token that cannot be refreshed produces a "Reconnect <platform>"
    failure on that platform only, the other platforms still publish;
  * the post is claimed with an atomic ``pending → posting`` UPDATE so two
    workers can never publish the same post twice, on top of the Redis lease
    that stops Beat re-queueing it while an upload is in flight.

Threading model: the platform services are async (aiohttp / to_thread for
google-api-python-client), so each task runs them on a fresh event loop —
the same pattern as whatsapp_tasks — while DB access stays on the sync
psycopg2 engine shared by all worker modules.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from core.config import settings
from core.logging_config import get_logger
from models.social_scheduler import (
    SocialPlatformConnection,
    SocialPost,
    SocialPostResult,
    SocialPostResultStatus,
    SocialPostStatus,
)
from services.social import get_service
from services.social.connections import PlatformTokens, apply_tokens, read_tokens
from services.social.credentials import apply_credentials_sync
from worker.celery_app import celery_app
from worker.dispatch_lease import claim_dispatch_lease, release_dispatch_lease

logger = get_logger(__name__)

PLATFORM_LABELS = {
    "youtube": "YouTube Shorts",
    "instagram": "Instagram Reels",
    "tiktok": "TikTok",
    "facebook": "Facebook Reels",
}
# A post stuck in "posting" longer than this (worker died mid-upload) is
# handed back to the dispatcher on the next tick.
STALE_POSTING_SECONDS = int(os.getenv("SOCIAL_POSTING_STALE_SECONDS", str(2 * 60 * 60)))
LEASE_SECONDS = STALE_POSTING_SECONDS


# ── Sync DB session for Celery ──────────────────────────────────────────────
def _make_sync_url(async_url: str) -> str:
    """Convert async DATABASE_URL to sync psycopg2 URL for Celery tasks."""
    url = async_url
    for prefix in (
        "postgresql+asyncpg://",
        "postgres+asyncpg://",
    ):
        if url.startswith(prefix):
            url = url.replace(prefix, "postgresql+psycopg2://", 1)
            return url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    if url.startswith("sqlite+aiosqlite://"):
        url = url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return url


_sync_url = _make_sync_url(settings.DATABASE_URL)
# Bounded pool — see worker/sync_engine.py for the PgBouncer/EMAXCONNSESSION
# rationale (the engines share one Postgres behind Railway's 15-client cap).
from worker.sync_engine import make_worker_engine  # noqa: E402

_engine = make_worker_engine(_sync_url)
SyncSession = sessionmaker(bind=_engine, expire_on_commit=False)


@contextmanager
def get_sync_db():
    """Context manager for a sync SQLAlchemy session in Celery tasks."""
    session = SyncSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lease_key(post_id: str) -> str:
    return f"linkeasy:scheduler:social:{post_id}"


# ── Task: Beat dispatcher ────────────────────────────────────────────────────


@celery_app.task(name="tasks.dispatch_due_social_posts")
def dispatch_due_social_posts() -> dict:
    """Queue one publish task per pending post whose scheduled_at has passed.

    The database timestamp is the source of truth: a post cancelled in the UI
    is simply never selected here, and the publish task re-checks the row
    before uploading. A token-owned Redis lease prevents Beat from queueing
    the same post again while its upload is still running.
    """
    now = _now()
    due_ids: list[str] = []
    with get_sync_db() as db:
        due_ids = [
            row[0]
            for row in db.query(SocialPost.id)
            .filter(SocialPost.status == SocialPostStatus.PENDING.value, SocialPost.scheduled_at <= now)
            .order_by(SocialPost.scheduled_at)
            .all()
        ]
        # Self-heal: a post left in "posting" for hours means a worker died
        # mid-upload. Hand it back so it is retried rather than stuck forever.
        stale_before = datetime.fromtimestamp(now.timestamp() - STALE_POSTING_SECONDS, tz=timezone.utc)
        reset = db.execute(
            update(SocialPost)
            .where(SocialPost.status == SocialPostStatus.POSTING.value, SocialPost.updated_at < stale_before)
            .values(status=SocialPostStatus.PENDING.value)
        ).rowcount
        if reset:
            logger.warning("Reset %s social post(s) stuck in 'posting' for > %ss", reset, STALE_POSTING_SECONDS)

    if not due_ids:
        return {"posts_dispatched": 0, "post_ids": []}

    import redis

    redis_client = redis.from_url(settings.REDIS_URL)
    dispatched = 0
    for post_id in due_ids:
        lease_token = claim_dispatch_lease(redis_client, _lease_key(post_id), timeout=LEASE_SECONDS)
        if not lease_token:
            continue
        try:
            celery_app.send_task("tasks.publish_social_post", args=[post_id, lease_token])
            dispatched += 1
        except Exception:
            release_dispatch_lease(redis_client, _lease_key(post_id), lease_token)
            raise

    if dispatched:
        logger.info("📅 Dispatched %s due social post(s)", dispatched)
    return {"posts_dispatched": dispatched, "post_ids": due_ids}


# ── Task: publish one post ───────────────────────────────────────────────────


@celery_app.task(bind=True, name="tasks.publish_social_post", max_retries=0)
def publish_social_post(self, post_id: str, dispatch_token: Optional[str] = None) -> dict:
    logger.info("📤 Publishing social post %s", post_id)
    redis_client = None
    if dispatch_token:
        try:
            import redis

            redis_client = redis.from_url(settings.REDIS_URL)
        except Exception:  # lease release is best-effort; TTL is the safety net
            redis_client = None
    try:
        return publish_post(post_id)
    except Exception as exc:
        logger.error("Social post %s failed: %s", post_id, exc, exc_info=True)
        return {"status": "error", "post_id": post_id, "message": str(exc)}
    finally:
        if redis_client is not None:
            release_dispatch_lease(redis_client, _lease_key(post_id), dispatch_token)


def publish_post(post_id: str) -> dict:
    """Publish a post to every platform it targets and record the outcomes."""
    # 1. Claim the post atomically: only the worker that flips pending→posting
    #    proceeds, so two overlapping ticks can never publish it twice.
    with get_sync_db() as db:
        claimed = db.execute(
            update(SocialPost)
            .where(SocialPost.id == post_id, SocialPost.status == SocialPostStatus.PENDING.value)
            .values(status=SocialPostStatus.POSTING.value, updated_at=_now())
        ).rowcount
        if not claimed:
            current = db.query(SocialPost.status).filter(SocialPost.id == post_id).scalar()
            logger.info("Social post %s not claimed (status=%s) — skipping", post_id, current)
            return {"status": "skipped", "post_id": post_id, "current_status": current}

        post = db.query(SocialPost).filter(SocialPost.id == post_id).one()
        platforms = list(post.platforms or [])
        owner_email = post.owner_email
        snapshot = {
            "id": post.id,
            "title": post.title,
            "caption": post.caption,
            "hashtags": post.hashtags or "",
            "video_path": post.video_path,
            "video_url": post.video_url,
            "thumbnail": post.thumbnail or "",
            "youtube_title": post.youtube_title or "",
            "instagram_caption": post.instagram_caption or "",
            "tiktok_caption": post.tiktok_caption or "",
            "platform_copy": post.platform_copy or {},
            # Chosen in the upload editor; filed into after the upload lands.
            "youtube_playlist_ids": list(post.youtube_playlist_ids or []),
            # Manual destinations — never posted, only reported as a note.
            "facebook_groups": list(post.facebook_groups or []),
        }
        # One pending result row per platform up front, so the UI can show
        # per-platform progress while uploads run.
        for platform in platforms:
            _upsert_result(db, post_id, owner_email, platform, SocialPostResultStatus.PENDING.value)

    logger.info("[%s] claimed; platforms=%s", post_id, platforms)

    # 2. Publish to each platform (network I/O on a private event loop).
    outcomes: dict[str, dict] = {}
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        for platform in platforms:
            outcomes[platform] = loop.run_until_complete(_publish_to_platform(owner_email, snapshot, platform))
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    # 3. Record the outcomes and the post's final status.
    all_success = True
    with get_sync_db() as db:
        for platform, outcome in outcomes.items():
            if outcome["ok"]:
                _upsert_result(
                    db,
                    post_id,
                    owner_email,
                    platform,
                    SocialPostResultStatus.POSTED.value,
                    platform_id=outcome["platform_id"],
                    platform_url=outcome["platform_url"],
                    note=outcome.get("note", ""),
                )
                logger.info("[%s] ✅ %s: %s", post_id, platform, outcome["platform_url"])
                if outcome.get("note"):
                    # The post succeeded; this is a caveat the user should still
                    # see (a playlist that could not be updated, for example).
                    logger.warning("[%s] ⚠️  %s: %s", post_id, platform, outcome["note"])
            else:
                all_success = False
                _upsert_result(
                    db, post_id, owner_email, platform, SocialPostResultStatus.FAILED.value, error=outcome["error"]
                )
                logger.warning("[%s] ❌ %s: %s", post_id, platform, outcome["error"])

        final_status = SocialPostStatus.POSTED.value if all_success and platforms else SocialPostStatus.FAILED.value
        db.execute(
            update(SocialPost)
            .where(SocialPost.id == post_id, SocialPost.status == SocialPostStatus.POSTING.value)
            .values(status=final_status, updated_at=_now())
        )

    if final_status == SocialPostStatus.POSTED.value:
        _cleanup_completed_upload(snapshot["video_path"])

    logger.info("[%s] done: %s", post_id, final_status)
    return {"status": final_status, "post_id": post_id, "results": outcomes}


# ── Per-platform publish ─────────────────────────────────────────────────────


def _join_copy(*parts: str) -> str:
    """Join copy blocks without creating doubled blank lines."""
    return "\n\n".join(str(part).strip() for part in parts if part and str(part).strip())


def _platform_copy(post: dict, platform: str) -> dict[str, str]:
    """Return structured copy for one platform, tolerating legacy snapshots."""
    raw = (post.get("platform_copy") or {}).get(platform) or {}
    return {
        key: str(raw.get(key) or "").strip()
        for key in ("title", "description", "hashtags")
    }


def _thumbnail_path_for_video(video_path: str) -> str:
    """The API stores covers beside the clip as ``<stem>.thumb.jpg``."""
    stem, _extension = os.path.splitext(video_path)
    return f"{stem}.thumb.jpg"


def _cleanup_completed_upload(video_path: str) -> None:
    """Remove a video and thumbnail after every selected platform succeeds."""
    # Retain files when a platform fails so the post can be retried and
    # diagnosed. Cleanup itself is best-effort and must not change a POSTED
    # result into a failure.
    for path in (video_path, _thumbnail_path_for_video(video_path)):
        if not path:
            continue
        try:
            os.remove(path)
            logger.info("Removed completed social upload artifact %s", path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not remove completed social upload artifact %s: %s", path, exc)


async def _set_youtube_thumbnail(service, *, video_id: str, thumbnail_path: str, tokens) -> str:
    """Set a cover if one exists; a cover failure must not undo publication."""
    if not os.path.exists(thumbnail_path):
        return ""
    try:
        await service.set_thumbnail(
            video_id,
            thumbnail_path,
            tokens.access_token,
            tokens.refresh_token,
        )
        return "Custom thumbnail applied"
    except Exception as exc:
        logger.warning("YouTube video %s published but thumbnail failed: %s", video_id, exc)
        return f"Published, but custom thumbnail could not be applied: {str(exc)[:300]}"


async def _publish_to_platform(owner_email: str, post: dict, platform: str) -> dict:
    """Publish one post to one platform. Never raises — returns an outcome dict."""
    label = PLATFORM_LABELS.get(platform, platform)
    try:
        service = get_service(platform)
    except ValueError as exc:
        return _failure(str(exc))

    with get_sync_db() as db:
        # The service was built from env-only settings; overlay the operator's
        # DB credential row (if any) so refresh/publish authenticate with the
        # same app the settings page configured.
        apply_credentials_sync(db, platform, service)
        conn = (
            db.query(SocialPlatformConnection)
            .filter(
                SocialPlatformConnection.owner_email == owner_email,
                SocialPlatformConnection.platform == platform,
            )
            .one_or_none()
        )
        if conn is None:
            return _failure(f"{label} is not connected. Open Settings and connect the account.")
        conn_id = conn.id
        account_id = conn.account_id or ""
        try:
            tokens = read_tokens(conn)
        except ValueError as exc:
            return _failure(str(exc))

    # Refresh + persist an expired token BEFORE trying to publish.
    if tokens.is_expired:
        try:
            tokens = await _refresh_and_persist(service, conn_id, tokens)
        except Exception as exc:
            return _failure(f"{label} access expired and could not be renewed: {exc}")

    video_path = post["video_path"]
    platform_copy = _platform_copy(post, platform)
    common_caption = _join_copy(post["caption"], post["hashtags"])
    structured_caption = _join_copy(
        platform_copy.get("title", ""),
        platform_copy.get("description", ""),
        platform_copy.get("hashtags", ""),
    )
    legacy_caption = {
        "instagram": post.get("instagram_caption", ""),
        "tiktok": post.get("tiktok_caption", ""),
    }.get(platform, "")
    platform_caption = structured_caption or legacy_caption or common_caption
    try:
        if platform == "youtube":
            result = await service.upload_short(
                video_path=video_path,
                title=platform_copy.get("title") or post["youtube_title"] or post["title"],
                description=_join_copy(platform_copy.get("description", ""), platform_copy.get("hashtags", ""))
                or common_caption,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
            )
            thumbnail_path = _thumbnail_path_for_video(video_path)
            thumbnail_note = await _set_youtube_thumbnail(
                service,
                video_id=result["video_id"],
                thumbnail_path=thumbnail_path,
                tokens=tokens,
            )
            note = await _add_to_youtube_playlists(
                service,
                video_id=result["video_id"],
                playlist_ids=post.get("youtube_playlist_ids") or [],
                tokens=tokens,
                label=label,
            )
            return _success(
                result["video_id"],
                result["video_url"],
                note="; ".join(item for item in (thumbnail_note, note) if item),
            )

        if platform == "instagram":
            # The video lives on this server (both "publish now" and scheduled
            # posts store the file under UPLOAD_DIR), so the worker always hands
            # the stored file over and lets the service decide: direct upload by
            # default, URL flow as a fallback for a public instance. No public
            # URL is required, and a non-public video_url is not an error here.
            result = await service.publish_reel(
                ig_user_id=account_id,
                video_url=post["video_url"],
                caption=platform_caption or post["instagram_caption"] or common_caption,
                access_token=tokens.access_token,
                video_path=video_path,
                thumbnail_url=post.get("thumbnail") or None,
            )
            return _success(result["media_id"], result["post_url"])

        if platform == "facebook":
            result = await service.upload_video(
                video_path=video_path,
                description=platform_caption,
                access_token=tokens.access_token,
            )
            return _success(result["video_id"], result["video_url"], note=_facebook_share_note(post))

        if platform == "tiktok":
            result = await service.upload_video(
                video_path=video_path,
                caption=platform_caption or post["tiktok_caption"] or common_caption,
                access_token=tokens.access_token,
            )
            return _success(result["publish_id"], result["video_url"])

        return _failure(f"Unsupported platform: {platform}")
    except FileNotFoundError:
        return _failure("The uploaded video file is missing on the server. Upload it again and reschedule.")
    except Exception as exc:
        return _failure(str(exc) or exc.__class__.__name__)


async def _refresh_and_persist(service, conn_id: str, tokens: PlatformTokens) -> PlatformTokens:
    """Renew an expired token via the platform and store the encrypted result."""
    renewed = await service.refresh_access_token(tokens.refresh_token, current_access_token=tokens.access_token)
    with get_sync_db() as db:
        conn = db.query(SocialPlatformConnection).filter(SocialPlatformConnection.id == conn_id).one()
        apply_tokens(
            conn,
            access_token=renewed.get("access_token"),
            refresh_token=renewed.get("refresh_token"),
            expires_in=renewed.get("expires_in"),
        )
        conn.updated_at = _now()
        db.flush()
        fresh = read_tokens(conn)
    logger.info("Refreshed %s token for connection %s", conn.platform, conn_id)
    return fresh


async def _add_to_youtube_playlists(service, *, video_id: str, playlist_ids: list, tokens, label: str) -> str:
    """File an uploaded Short into the chosen playlists; return a note (or "").

    The video is already public at this point, so nothing here may turn into a
    failed post — a playlist the channel no longer owns, or a connection that
    has not granted the playlist scope yet, is reported as a note the UI shows
    next to the successful publish.
    """
    # None is checked before stringifying: str(None) is "None", which would
    # otherwise be posted as a playlist id.
    wanted = []
    for raw in playlist_ids or []:
        if raw is None:
            continue
        playlist_id = str(raw).strip()
        if playlist_id and playlist_id not in wanted:
            wanted.append(playlist_id)
    if not wanted or not video_id:
        return ""
    try:
        outcome = await service.add_to_playlists(
            video_id, wanted, tokens.access_token, tokens.refresh_token
        )
    except Exception as exc:  # the service already swallows per-playlist errors
        logger.warning("Playlist step failed for %s: %s", video_id, exc)
        return f"Published, but the playlists could not be updated: {str(exc)[:200]}"

    added, failed = outcome.get("added") or [], outcome.get("failed") or []
    if not failed:
        return f"Added to {len(added)} playlist{'' if len(added) == 1 else 's'}"
    reasons = "; ".join(
        f"{item.get('playlist_id', '?')}: {item.get('error', 'unknown error')}" for item in failed
    )
    logger.warning("%s: added to %d/%d playlists (%s)", label, len(added), len(wanted), reasons)
    return (
        f"Published, and added to {len(added)} of {len(wanted)} playlists. "
        f"{label} could not update: {reasons[:400]}"
    )


def _facebook_share_note(post: dict) -> str:
    """Name the groups that still need the Reel, shared by hand.

    Meta removed the Groups API on 22 Apr 2024, so the worker publishes to the
    Page only. The groups chosen on the upload page cannot be posted for the
    user, and a published result row is the one place that reminder survives.
    """
    groups = post.get("facebook_groups") or []
    if not groups:
        return ""
    count = len(groups)
    return (
        f"Published to your Facebook Page. Share it manually to {count} "
        f"group{'' if count == 1 else 's'} from this post's checklist."
    )


def _success(platform_id: str, platform_url: str, *, note: str = "") -> dict:
    return {
        "ok": True,
        "platform_id": platform_id or "",
        "platform_url": platform_url or "",
        "error": "",
        "note": note[:1000],
    }


def _failure(error: str) -> dict:
    return {"ok": False, "platform_id": "", "platform_url": "", "error": (error or "Unknown error")[:2000]}


def _upsert_result(
    db,
    post_id: str,
    owner_email: str,
    platform: str,
    status: str,
    *,
    platform_id: str = "",
    platform_url: str = "",
    error: str = "",
    note: str = "",
) -> None:
    """Create or update the (post, platform) outcome row."""
    row = (
        db.query(SocialPostResult)
        .filter(SocialPostResult.post_id == post_id, SocialPostResult.platform == platform)
        .one_or_none()
    )
    if row is None:
        row = SocialPostResult(post_id=post_id, owner_email=owner_email, platform=platform)
        db.add(row)
    row.status = status
    row.platform_id = platform_id
    row.platform_url = platform_url
    row.error = error
    # A note survives on a successful row (error is only rendered for failures).
    row.note = note
    row.posted_at = _now() if status == SocialPostResultStatus.POSTED.value else None
    row.updated_at = _now()
    db.flush()
