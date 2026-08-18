"""
System / Redis / Celery queue inspection and management.

Provides endpoints to view remaining, paused, faulty jobs in both:
- Redis (Celery queues, locks, rate-limit keys, raw keys)
- Postgres (campaigns, leads, campaign_jobs, feed_scroll, whatsapp)

And actions to delete unnecessary Redis keys / purge queues / clear locks.

Frontend: /app/system-queues or /app/redis-jobs

All endpoints require authentication (get_current_user) but no special role.
If you want to restrict to admin, wrap with require_roles.

Endpoints:

GET  /system/queues/overview           -> combined snapshot
GET  /system/queues/redis-info         -> redis INFO + db size + queue lengths
GET  /system/queues/redis-keys         -> list keys with pattern, type, ttl, len
POST /system/queues/redis-keys/delete  -> delete specific keys
POST /system/queues/purge              -> purge celery queue(s)
POST /system/queues/clear-locks        -> clear session_lock / profile_lock / semaphore
POST /system/queues/clear-rate-limits  -> delete rate:* keys
GET  /system/queues/celery-inspect     -> active / scheduled / reserved / revoked if worker up
GET  /system/queues/db-stats           -> paused/failed counts from Postgres tables
POST /system/queues/revoke             -> revoke a celery task id
POST /system/queues/cleanup-stale       -> revoke stale reserved/ETA automation tasks
POST /system/queues/flush-pattern      -> delete keys by pattern (with safety limit)
"""

from typing import Any, Dict, List, Optional
import ast
import json
import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, text

from api.dependencies import get_current_user, get_db
from core.config import settings
from core.logging_config import get_logger
from models.user import User

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/system", tags=["system"])


def _get_redis(decode: bool = True):
    try:
        client = redis.from_url(settings.REDIS_URL, decode_responses=decode)
        # quick ping to validate connection
        client.ping()
        return client
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e}")


def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


# ─── helpers for redis introspection ────────────────────────────────────────────

def _queue_lengths(r) -> Dict[str, int]:
    """Return lengths of common celery / app queues."""
    # Celery default queues + our custom ones mentioned in docs/celery_scheduler.md
    candidate_queues = [
        "celery",
        "default",
        "linkedin_sessions",
        "celery@unknown",  # sometimes shows up
    ]
    # Discover additional list keys that look like queues (but not locks)
    try:
        # Scan for list-type keys under 100 to avoid heavy scan
        extras = []
        for key in r.scan_iter(match="*queue*", count=200):
            if r.type(key) == "list":
                extras.append(key)
        for key in r.scan_iter(match="celery*", count=200):
            if r.type(key) == "list" and key not in candidate_queues:
                extras.append(key)
        candidate_queues = list(dict.fromkeys(candidate_queues + extras))
    except Exception:
        pass

    lengths = {}
    for q in candidate_queues:
        try:
            l = r.llen(q)
            if l > 0 or q in ("celery", "default"):
                lengths[q] = l
        except Exception:
            # key may not be list or not exist
            pass

    # Also try to count unacked / kombu
    try:
        for k in r.scan_iter(match="*unacked*", count=200):
            try:
                if r.type(k) in ("list", "zset", "hash"):
                    if r.type(k) == "list":
                        lengths[k] = r.llen(k)
                    elif r.type(k) == "zset":
                        lengths[k] = r.zcard(k)
                    else:
                        lengths[k] = r.hlen(k)
            except Exception:
                continue
    except Exception:
        pass

    return lengths


def _locks_info(r) -> Dict[str, Any]:
    locks = {
        "session_locks": [],
        "profile_locks": [],
        "playwright_semaphore": None,
        "other_locks": [],
    }
    try:
        for key in r.scan_iter(match="session_lock:*", count=500):
            ttl = r.ttl(key)
            locks["session_locks"].append({"key": key, "ttl": ttl})
    except Exception as e:
        logger.warning(f"Failed to scan session locks: {e}")

    try:
        for key in r.scan_iter(match="profile_lock:*", count=500):
            ttl = r.ttl(key)
            # For redis lock, value contains token; type may be string
            locks["profile_locks"].append({"key": key, "ttl": ttl})
    except Exception as e:
        logger.warning(f"Failed to scan profile locks: {e}")

    try:
        sem = r.get("playwright:semaphore")
        if sem is not None:
            locks["playwright_semaphore"] = {"key": "playwright:semaphore", "count": _safe_int(sem), "ttl": r.ttl("playwright:semaphore")}
        # Also check lock key for semaphore code
        if r.exists("playwright:lock"):
            locks["other_locks"].append({"key": "playwright:lock", "ttl": r.ttl("playwright:lock")})
    except Exception:
        pass

    # generic locks with 'lock' in name but not above
    try:
        for key in r.scan_iter(match="*lock*", count=500):
            if key.startswith("session_lock:") or key.startswith("profile_lock:") or key in ("playwright:lock",):
                continue
            locks["other_locks"].append({"key": key, "ttl": r.ttl(key), "type": r.type(key)})
    except Exception:
        pass

    return locks


def _rate_limit_info(r) -> Dict[str, Any]:
    info = {"count": 0, "sample_keys": [], "by_action": {}}
    try:
        count = 0
        by_action: Dict[str, int] = {}
        sample = []
        for key in r.scan_iter(match="rate:*", count=500):
            count += 1
            # rate:{email}:{action}:{date}
            parts = key.split(":")
            if len(parts) >= 3:
                action = parts[2] if len(parts) == 4 else parts[1]
                by_action[action] = by_action.get(action, 0) + 1
            if len(sample) < 20:
                try:
                    ttl = r.ttl(key)
                    val = r.get(key)
                    sample.append({"key": key, "value": val, "ttl": ttl})
                except Exception:
                    sample.append({"key": key})
        info["count"] = count
        info["sample_keys"] = sample
        info["by_action"] = by_action
    except Exception as e:
        logger.warning(f"rate limit scan failed: {e}")
    return info


def _redis_keys_list(r, pattern: str = "*", limit: int = 200, offset: int = 0, key_type: Optional[str] = None):
    """List redis keys with pagination, type, ttl, and size hint."""
    all_keys = []
    try:
        # Use SCAN for efficiency
        scanned = []
        for k in r.scan_iter(match=pattern or "*", count=1000):
            scanned.append(k)

        # sort for stability
        scanned.sort()

        # apply offset/limit
        page = scanned[offset: offset + limit]
        result = []
        for key in page:
            try:
                t = r.type(key)
                if key_type and t != key_type:
                    continue
                ttl = r.ttl(key)
                # size hint based on type
                size = None
                try:
                    if t == "list":
                        size = r.llen(key)
                    elif t == "set":
                        size = r.scard(key)
                    elif t == "zset":
                        size = r.zcard(key)
                    elif t == "hash":
                        size = r.hlen(key)
                    elif t == "string":
                        v = r.get(key)
                        if v is not None:
                            size = len(str(v))
                        else:
                            size = 0
                except Exception:
                    size = None

                result.append({
                    "key": key,
                    "type": t,
                    "ttl": ttl,
                    "size": size,
                })
            except Exception as e:
                result.append({"key": key, "type": "unknown", "error": str(e)})

        return {
            "total_matched": len(scanned),
            "offset": offset,
            "limit": limit,
            "pattern": pattern,
            "keys": result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Redis scan failed: {e}")


# ─── celery inspect (best-effort) ─────────────────────────────────────────────

def _celery_inspect():
    """Try to get active / scheduled / reserved tasks via celery control."""
    try:
        from worker.celery_app import celery_app
        insp = celery_app.control.inspect(timeout=2.0)
        # Each of these returns dict {worker_name: [tasks]}
        data = {}
        for method in ("active", "scheduled", "reserved", "revoked", "registered", "stats"):
            try:
                fn = getattr(insp, method)
                res = fn()
                data[method] = res or {}
            except Exception as e:
                data[method] = {"error": str(e)}

        # Also try to flatten for frontend convenience
        flat = {
            "workers": list((data.get("active") or {}).keys()),
            "active_count": sum(len(v) for v in (data.get("active") or {}).values() if isinstance(v, list)),
            "scheduled_count": sum(len(v) for v in (data.get("scheduled") or {}).values() if isinstance(v, list)),
            "reserved_count": sum(len(v) for v in (data.get("reserved") or {}).values() if isinstance(v, list)),
            "raw": data,
        }
        return flat
    except Exception as e:
        return {
            "workers": [],
            "active_count": 0,
            "scheduled_count": 0,
            "reserved_count": 0,
            "error": f"Inspection failed (worker may be offline): {e}",
            "raw": {}
        }


# ─── db stats ─────────────────────────────────────────────────────────────────

async def _db_stats(db: AsyncSession) -> Dict[str, Any]:
    stats: Dict[str, Any] = {}

    # Campaigns by status
    try:
        from models.campaign import Campaign
        q = select(Campaign.status, func.count(Campaign.id)).group_by(Campaign.status)
        res = await db.execute(q)
        campaigns_by_status = {str(status.value if hasattr(status, 'value') else status): cnt for status, cnt in res.all()}
        stats["campaigns"] = campaigns_by_status

        # detailed lists for paused / failed / draft / active
        for st in ("paused", "failed", "active", "draft"):
            q = select(Campaign).where(Campaign.status == st).limit(100)
            res = await db.execute(q)
            rows = res.scalars().all()
            stats[f"campaigns_{st}"] = [
                {"id": c.id, "name": c.name, "account_email": c.account_email,
                 "status": c.status.value if hasattr(c.status, 'value') else str(c.status),
                 "created_at": c.created_at.isoformat() if c.created_at else None,
                 "started_at": c.started_at.isoformat() if c.started_at else None}
                for c in rows
            ]
    except Exception as e:
        stats["campaigns"] = {"error": str(e)}

    # Leads
    try:
        from models.lead import Lead
        q = select(Lead.status, func.count(Lead.id)).group_by(Lead.status)
        res = await db.execute(q)
        stats["leads_by_status"] = {str(s.value if hasattr(s, 'value') else s): cnt for s, cnt in res.all()}
    except Exception as e:
        stats["leads_by_status"] = {"error": str(e)}

    # Campaign jobs by status
    try:
        from models.campaign_job import CampaignJob
        q = select(CampaignJob.status, func.count(CampaignJob.id)).group_by(CampaignJob.status)
        res = await db.execute(q)
        stats["campaign_jobs"] = {str(s.value if hasattr(s, 'value') else s): cnt for s, cnt in res.all()}

        # failed jobs sample
        q = select(CampaignJob).where(CampaignJob.status == "failed").order_by(CampaignJob.created_at.desc()).limit(100)
        res = await db.execute(q)
        failed = res.scalars().all()
        stats["campaign_jobs_failed"] = [
            {
                "id": j.id,
                "campaign_id": j.campaign_id,
                "lead_id": j.lead_id,
                "step_type": j.step_type,
                "status": j.status.value if hasattr(j.status, 'value') else str(j.status),
                "error_message": (j.error_message or "")[:500],
                "action_message": (j.action_message or "")[:500],
                "celery_task_id": j.celery_task_id,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            }
            for j in failed
        ]

        # remaining / queued / running
        q = select(CampaignJob).where(CampaignJob.status.in_(["queued", "running"])).order_by(CampaignJob.created_at.desc()).limit(100)
        res = await db.execute(q)
        remaining = res.scalars().all()
        stats["campaign_jobs_remaining"] = [
            {
                "id": j.id,
                "campaign_id": j.campaign_id,
                "lead_id": j.lead_id,
                "step_type": j.step_type,
                "status": j.status.value if hasattr(j.status, 'value') else str(j.status),
                "celery_task_id": j.celery_task_id,
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            for j in remaining
        ]
    except Exception as e:
        stats["campaign_jobs"] = {"error": str(e)}

    # Feed scroll jobs
    try:
        from models.feed_scroll_job import FeedScrollJob
        q = select(FeedScrollJob.status, func.count(FeedScrollJob.id)).group_by(FeedScrollJob.status)
        res = await db.execute(q)
        stats["feed_scroll"] = {str(s.value if hasattr(s, 'value') else s): cnt for s, cnt in res.all()}

        q = select(FeedScrollJob).where(FeedScrollJob.status.in_(["paused", "failed", "active"])).limit(100)
        res = await db.execute(q)
        rows = res.scalars().all()
        stats["feed_scroll_jobs_detailed"] = [
            {
                "id": r.id,
                "name": getattr(r, "name", None),
                "owner_email": getattr(r, "owner_email", None),
                "status": r.status.value if hasattr(r.status, 'value') else str(r.status),
                "account_email": getattr(r, "account_email", None),
                "next_scan_at": r.next_scan_at.isoformat() if getattr(r, "next_scan_at", None) else None,
                "last_scanned_at": r.last_scanned_at.isoformat() if getattr(r, "last_scanned_at", None) else None,
            }
            for r in rows
        ]
    except Exception as e:
        stats["feed_scroll"] = {"error": str(e)}

    # WhatsApp filter jobs
    try:
        from models.whatsapp import WhatsAppScanFilter
        q = select(WhatsAppScanFilter.status, func.count(WhatsAppScanFilter.id)).group_by(WhatsAppScanFilter.status)
        res = await db.execute(q)
        stats["whatsapp_filters"] = {str(s.value if hasattr(s, 'value') else s): cnt for s, cnt in res.all()}

        q = select(WhatsAppScanFilter).where(WhatsAppScanFilter.status.in_(["paused", "active", "failed"])).limit(100)
        res = await db.execute(q)
        rows = res.scalars().all()
        stats["whatsapp_filters_detailed"] = [
            {
                "id": r.id,
                "name": getattr(r, "name", None) or getattr(r, "group_name", None) or str(r.id),
                "status": r.status.value if hasattr(r.status, 'value') else str(r.status),
                "next_scan_at": r.next_scan_at.isoformat() if getattr(r, "next_scan_at", None) else None,
                "last_scan_at": r.last_scan_at.isoformat() if getattr(r, "last_scan_at", None) else None,
                "owner_email": getattr(r, "owner_email", None),
            }
            for r in rows
        ]
    except Exception as e:
        # Table may not exist in older DB; keep error but not fatal
        stats["whatsapp_filters"] = {"error": str(e)}

    return stats


# ─── stale task cleanup ────────────────────────────────────────────────────────

_LEGACY_AUTOMATION_TASKS = {
    "tasks.connect_whatsapp",
    "tasks.reconcile_stalled_leads",
    "tasks.execute_campaign_step",
    "tasks.step1_visit_profile",
    "tasks.step1_visit_and_like",
    "tasks.step2_send_connection",
    "tasks.step3_send_message",
    "tasks.step4_followup_if_pending",
    "tasks.step5_thanks_if_accepted",
}


def _inspection_request(item: Any) -> dict:
    """Normalize Celery's active/reserved/scheduled request shapes."""
    if not isinstance(item, dict):
        return {}
    request = item.get("request")
    return request if isinstance(request, dict) else item


def _inspection_args(request: dict) -> list:
    args = request.get("args", [])
    if isinstance(args, list):
        return args
    if not isinstance(args, str):
        return []
    try:
        parsed = json.loads(args)
    except Exception:
        try:
            parsed = ast.literal_eval(args)
        except Exception:
            return []
    return parsed if isinstance(parsed, list) else []


async def _cleanup_stale_automation_tasks(db: AsyncSession, user: User) -> dict:
    """Revoke only queued/scheduled work that has no active DB owner.

    Celery's worker inspect API can see reserved and ETA tasks, but not every
    message still sitting in a broker list. This endpoint is deliberately
    conservative: it never terminates an active browser task and never purges
    unrelated user work. The task bodies also re-check status, so cleanup is a
    safety net rather than the correctness boundary.
    """
    from models.campaign import Campaign, CampaignStatus
    from models.feed_scroll_job import FeedScrollJob, FeedScrollJobStatus
    from models.linkedin_account import LinkedInAccount
    from models.whatsapp import WhatsAppScanFilter

    # Cleanup is scoped to the authenticated owner's jobs. A normal workspace
    # user must not revoke another user's reserved task just because both share
    # the same Redis/Celery installation.
    active_accounts = {
        row[0]
        for row in (
            await db.execute(
                select(Campaign.account_email)
                .join(LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email)
                .where(
                    Campaign.status == CampaignStatus.ACTIVE,
                    LinkedInAccount.owner_email == user.email,
                )
            )
        ).all()
    }
    active_feed_ids = {
        row[0]
        for row in (
            await db.execute(
                select(FeedScrollJob.id).where(
                    FeedScrollJob.status == FeedScrollJobStatus.ACTIVE,
                    FeedScrollJob.owner_email == user.email,
                )
            )
        ).all()
    }
    active_filter_ids = {
        row[0]
        for row in (
            await db.execute(
                select(WhatsAppScanFilter.id).where(
                    WhatsAppScanFilter.status == "active",
                    WhatsAppScanFilter.owner_email == user.email,
                )
            )
        ).all()
    }

    snapshot = _celery_inspect()
    raw = snapshot.get("raw") or {}
    candidates = []
    for bucket in ("scheduled", "reserved"):
        by_worker = raw.get(bucket) or {}
        if not isinstance(by_worker, dict):
            continue
        for worker_tasks in by_worker.values():
            if isinstance(worker_tasks, list):
                candidates.extend(worker_tasks)

    revoked = []
    for item in candidates:
        request = _inspection_request(item)
        task_name = request.get("name") or ""
        task_id = request.get("id")
        args = _inspection_args(request)
        stale = task_name in _LEGACY_AUTOMATION_TASKS

        if task_name == "tasks.run_account_session":
            stale = not args or args[0] not in active_accounts
        elif task_name == "tasks.run_feed_scroll":
            stale = not args or str(args[0]) not in {str(value) for value in active_feed_ids}
        elif task_name == "tasks.check_whatsapp_messages":
            filter_id = args[0] if args else None
            try:
                filter_id_value = int(filter_id) if filter_id is not None else None
            except (TypeError, ValueError):
                filter_id_value = None
            stale = (
                not active_filter_ids
                if filter_id_value is None
                else filter_id_value not in {int(value) for value in active_filter_ids}
            )

        if not stale or not task_id:
            continue
        try:
            from worker.celery_app import celery_app

            celery_app.control.revoke(task_id, terminate=False)
            revoked.append({"id": task_id, "name": task_name, "args": args})
        except Exception as exc:
            logger.warning("Could not revoke stale Celery task %s: %s", task_id, exc)

    # Remove abandoned dispatcher leases for rows that no longer exist/active.
    # The worker's token-aware release prevents this cleanup from deleting a
    # newly claimed lease for another task.
    r = _get_redis(decode=True)
    deleted_leases = []
    for pattern, valid_ids in (
        ("linkeasy:scheduler:feed:*", {str(value) for value in active_feed_ids}),
        ("linkeasy:scheduler:whatsapp:*", {str(value) for value in active_filter_ids}),
    ):
        prefix = pattern[:-1]
        for key in r.scan_iter(match=pattern, count=200):
            identifier = str(key)[len(prefix):]
            if identifier not in valid_ids and r.delete(key):
                deleted_leases.append(key)
    for key in r.scan_iter(match="linkeasy:scheduler:account:*", count=200):
        account_email = str(key).split("linkeasy:scheduler:account:", 1)[-1]
        if account_email not in active_accounts and r.delete(key):
            deleted_leases.append(key)

    logger.info(
        "Stale automation cleanup by %s: revoked=%d leases=%d",
        user.email,
        len(revoked),
        len(deleted_leases),
    )
    return {
        "revoked_count": len(revoked),
        "revoked": revoked,
        "deleted_lease_count": len(deleted_leases),
        "deleted_leases": deleted_leases,
        "inspected_count": len(candidates),
    }


# ─── routes ───────────────────────────────────────────────────────────────────

@router.get("/queues/overview")
async def get_overview(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Combined overview for the UI — redis queue lengths, locks, db stats, celery inspect."""
    r = _get_redis(decode=True)
    redis_info = {}
    try:
        info = r.info()
        redis_info = {
            "redis_version": info.get("redis_version"),
            "used_memory_human": info.get("used_memory_human"),
            "connected_clients": info.get("connected_clients"),
            "uptime_in_seconds": info.get("uptime_in_seconds"),
            "total_keys": r.dbsize(),
        }
    except Exception as e:
        redis_info = {"error": str(e), "total_keys": 0}

    queues = _queue_lengths(r)
    locks = _locks_info(r)
    rate = _rate_limit_info(r)
    celery = _celery_inspect()
    dbstats = await _db_stats(db)

    return {
        "redis": redis_info,
        "queues": queues,
        "locks": locks,
        "rate_limits": rate,
        "celery": celery,
        "db": dbstats,
    }


@router.post("/queues/cleanup-stale")
async def cleanup_stale_queues(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Revoke stale reserved/ETA automation tasks without touching active work."""
    return await _cleanup_stale_automation_tasks(db, user)


@router.get("/queues/redis-info")
async def redis_info(_user: User = Depends(get_current_user)):
    r = _get_redis(decode=True)
    try:
        info = r.info()
        return {
            "redis_version": info.get("redis_version"),
            "used_memory_human": info.get("used_memory_human"),
            "connected_clients": info.get("connected_clients"),
            "uptime_in_seconds": info.get("uptime_in_seconds"),
            "total_keys": r.dbsize(),
            "queues": _queue_lengths(r),
            "locks": _locks_info(r),
            "rate_limits": _rate_limit_info(r),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/queues/celery-inspect")
async def celery_inspect(_user: User = Depends(get_current_user)):
    return _celery_inspect()


@router.get("/queues/db-stats")
async def db_stats(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return await _db_stats(db)


@router.get("/queues/redis-keys")
async def list_redis_keys(
    pattern: str = Query(default="*", description="Redis glob pattern, e.g. session_lock:* or celery*"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    key_type: Optional[str] = Query(default=None, description="Filter by redis type: string, list, set, zset, hash, stream"),
    _user: User = Depends(get_current_user),
):
    r = _get_redis(decode=True)
    return _redis_keys_list(r, pattern=pattern, limit=limit, offset=offset, key_type=key_type)


@router.post("/queues/redis-keys/delete")
async def delete_redis_keys(
    payload: Dict[str, Any],
    _user: User = Depends(get_current_user),
):
    """Delete specific redis keys by list."""
    keys: List[str] = payload.get("keys") or []
    if not keys:
        raise HTTPException(status_code=400, detail="No keys provided")
    if len(keys) > 500:
        raise HTTPException(status_code=400, detail="Too many keys — max 500 at once")

    # Safety: never allow deleting all keys via this endpoint (use flush-pattern with limit)
    r = _get_redis(decode=True)
    deleted = 0
    errors = []
    for k in keys:
        # extra safety: block dangerous patterns like "*" or empty
        if not k or k == "*":
            errors.append({"key": k, "error": "Refused to delete wildcard/empty key"})
            continue
        try:
            deleted += r.delete(k)
        except Exception as e:
            errors.append({"key": k, "error": str(e)})

    # Also try to purge celery revoke if key looks like a celery task id? Not needed.

    return {"deleted": deleted, "requested": len(keys), "errors": errors}


@router.post("/queues/flush-pattern")
async def flush_by_pattern(
    payload: Dict[str, Any],
    _user: User = Depends(get_current_user),
):
    """Delete keys matching a pattern, with safety limits."""
    pattern = payload.get("pattern")
    limit = int(payload.get("limit", 100))
    dry_run = bool(payload.get("dry_run", False))

    if not pattern or pattern.strip() in ("*", ""):
        raise HTTPException(status_code=400, detail="Refusing to delete with wildcard-only pattern '*' — provide a more specific pattern")

    if limit > 1000:
        limit = 1000

    r = _get_redis(decode=True)
    matched = []
    try:
        for key in r.scan_iter(match=pattern, count=500):
            matched.append(key)
            if len(matched) >= limit:
                break
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scan failed: {e}")

    if dry_run:
        return {"matched": len(matched), "keys": matched[:100], "dry_run": True, "deleted": 0}

    deleted = 0
    for k in matched:
        try:
            deleted += r.delete(k)
        except Exception:
            continue

    logger.info(f"Flushed pattern {pattern}: matched {len(matched)}, deleted {deleted} by user {_user.email}")

    return {"pattern": pattern, "matched": len(matched), "deleted": deleted, "keys_sample": matched[:20]}


@router.post("/queues/purge")
async def purge_queue(
    payload: Dict[str, Any],
    _user: User = Depends(get_current_user),
):
    """Purge a celery queue (remove pending tasks)."""
    queue_name = payload.get("queue_name") or payload.get("queue") or "celery"
    # Normalize: allow "all"
    r = _get_redis(decode=True)

    purged_via_redis = 0
    purged_via_celery = 0
    celery_error = None

    # Try celery control purge first (best-effort, needs worker)
    try:
        from worker.celery_app import celery_app
        if queue_name == "all":
            # purge all queues
            # celery_app.control.purge() purges all queues by default
            res = celery_app.control.purge()
            # res is dict of worker -> purged count
            purged_via_celery = sum(res.values()) if isinstance(res, dict) else 0
        else:
            res = celery_app.control.purge()
            purged_via_celery = sum(res.values()) if isinstance(res, dict) else 0
            # also clear redis list for that queue name
            try:
                purged_via_redis = r.llen(queue_name)
                r.delete(queue_name)
            except Exception:
                pass
    except Exception as e:
        celery_error = str(e)
        # fallback to redis delete
        try:
            if queue_name == "all":
                # Try common queues
                for q in ["celery", "default", "linkedin_sessions"]:
                    try:
                        purged_via_redis += r.llen(q)
                        r.delete(q)
                    except Exception:
                        pass
            else:
                purged_via_redis = r.llen(queue_name)
                r.delete(queue_name)
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"Purge failed: celery_error={celery_error}, redis_error={e2}")

    logger.info(f"Queue purge requested by {_user.email}: queue={queue_name} redis_deleted={purged_via_redis} celery_purged={purged_via_celery}")

    return {
        "queue": queue_name,
        "deleted_via_redis": purged_via_redis,
        "purged_via_celery": purged_via_celery,
        "celery_error": celery_error,
    }


@router.post("/queues/clear-locks")
async def clear_locks(
    payload: Dict[str, Any],
    _user: User = Depends(get_current_user),
):
    """Clear session locks, profile locks, and/or semaphore."""
    types_to_clear = payload.get("types") or ["session", "profile", "semaphore"]
    # can also be explicit keys list
    explicit_keys: List[str] = payload.get("keys") or []

    r = _get_redis(decode=True)
    deleted = 0
    details = []

    if explicit_keys:
        for k in explicit_keys:
            try:
                deleted += r.delete(k)
                details.append({"key": k, "deleted": True})
            except Exception as e:
                details.append({"key": k, "deleted": False, "error": str(e)})
        return {"deleted": deleted, "details": details}

    if "session" in types_to_clear:
        try:
            for key in r.scan_iter(match="session_lock:*", count=500):
                deleted += r.delete(key)
                details.append({"key": key, "type": "session_lock"})
        except Exception as e:
            details.append({"error": f"session_lock scan failed: {e}"})

    if "profile" in types_to_clear:
        try:
            for key in r.scan_iter(match="profile_lock:*", count=500):
                deleted += r.delete(key)
                details.append({"key": key, "type": "profile_lock"})
        except Exception as e:
            details.append({"error": f"profile_lock scan failed: {e}"})

    if "semaphore" in types_to_clear:
        try:
            if r.exists("playwright:semaphore"):
                r.delete("playwright:semaphore")
                deleted += 1
                details.append({"key": "playwright:semaphore", "type": "semaphore"})
            if r.exists("playwright:lock"):
                r.delete("playwright:lock")
                deleted += 1
                details.append({"key": "playwright:lock", "type": "semaphore_lock"})
        except Exception as e:
            details.append({"error": f"semaphore clear failed: {e}"})

    if "other" in types_to_clear:
        try:
            for key in r.scan_iter(match="*lock*", count=500):
                if key.startswith("session_lock:") or key.startswith("profile_lock:") or key in ("playwright:lock",):
                    continue
                # skip rate keys
                if key.startswith("rate:"):
                    continue
                deleted += r.delete(key)
                details.append({"key": key, "type": "other_lock"})
        except Exception as e:
            details.append({"error": f"other lock scan failed: {e}"})

    logger.info(f"Clear locks by {_user.email}: types={types_to_clear} deleted={deleted}")

    return {"deleted": deleted, "details": details[:100]}


@router.post("/queues/clear-rate-limits")
async def clear_rate_limits(
    payload: Dict[str, Any],
    _user: User = Depends(get_current_user),
):
    pattern = payload.get("pattern") or "rate:*"
    dry_run = bool(payload.get("dry_run", False))
    limit = int(payload.get("limit", 1000))
    r = _get_redis(decode=True)

    matched = []
    for key in r.scan_iter(match=pattern, count=500):
        matched.append(key)
        if len(matched) >= limit:
            break

    if dry_run:
        return {"pattern": pattern, "matched": len(matched), "keys_sample": matched[:20], "dry_run": True}

    deleted = 0
    for k in matched:
        try:
            deleted += r.delete(k)
        except Exception:
            continue

    logger.info(f"Clear rate limits by {_user.email}: pattern={pattern} deleted={deleted}")
    return {"pattern": pattern, "matched": len(matched), "deleted": deleted}


@router.post("/queues/revoke")
async def revoke_task(
    payload: Dict[str, Any],
    _user: User = Depends(get_current_user),
):
    task_id = payload.get("task_id")
    terminate = bool(payload.get("terminate", False))

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")

    try:
        from worker.celery_app import celery_app
        celery_app.control.revoke(task_id, terminate=terminate)
        # Also try to remove from redis backend result
        r = _get_redis(decode=True)
        # Celery result keys are often "celery-task-meta-<id>"
        try:
            r.delete(f"celery-task-meta-{task_id}")
        except Exception:
            pass

        logger.info(f"Task revoked by {_user.email}: {task_id} terminate={terminate}")
        return {"task_id": task_id, "revoked": True, "terminate": terminate}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Revoke failed: {e}")


@router.delete("/queues/db/campaign-jobs/{job_id}")
async def delete_campaign_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    from models.campaign_job import CampaignJob
    res = await db.execute(select(CampaignJob).where(CampaignJob.id == job_id))
    job = res.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Revoke celery task if exists
    if job.celery_task_id:
        try:
            from worker.celery_app import celery_app
            celery_app.control.revoke(job.celery_task_id, terminate=False)
        except Exception:
            pass

    await db.delete(job)
    await db.commit()
    return {"deleted": job_id}


@router.post("/queues/db/campaign-jobs/bulk-delete")
async def bulk_delete_campaign_jobs(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    status_filter = payload.get("status")  # e.g. "failed"
    older_than_days = payload.get("older_than_days")
    limit = int(payload.get("limit", 100))
    dry_run = bool(payload.get("dry_run", False))

    from models.campaign_job import CampaignJob
    from datetime import datetime, timedelta, timezone

    q = select(CampaignJob)
    if status_filter:
        q = q.where(CampaignJob.status == status_filter)
    if older_than_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(older_than_days))
        q = q.where(CampaignJob.created_at < cutoff)

    q = q.limit(limit)
    res = await db.execute(q)
    jobs = res.scalars().all()

    if dry_run:
        return {"matched": len(jobs), "dry_run": True, "sample": [{"id": j.id, "status": str(j.status), "campaign_id": j.campaign_id} for j in jobs[:20]]}

    # revoke celery tasks
    task_ids = [j.celery_task_id for j in jobs if j.celery_task_id]
    if task_ids:
        try:
            from worker.celery_app import celery_app
            for tid in task_ids:
                try:
                    celery_app.control.revoke(tid, terminate=False)
                except Exception:
                    pass
        except Exception:
            pass

    deleted = 0
    for j in jobs:
        await db.delete(j)
        deleted += 1
    await db.commit()

    logger.info(f"Bulk delete campaign jobs by {_user.email}: status={status_filter} deleted={deleted}")

    return {"deleted": deleted, "matched": len(jobs)}
