"""
Admin dashboard API.

FILE: api/v1/admin.py

Surfaces the operator view the developer needs:

  GET   /api/v1/admin/me                  → the caller's roles (drives the UI)
  GET   /api/v1/admin/overview            → users, accounts, and jobs summary
  GET   /api/v1/admin/users               → user list with roles and usage
  PUT   /api/v1/admin/users/{email}/roles → assign roles (multi-role)
  GET   /api/v1/admin/settings            → campaign parameters and job limits
  PUT   /api/v1/admin/settings            → update them (validated + clamped)
  GET   /api/v1/admin/rate-limits         → current Postgres rate-limit usage
  DELETE /api/v1/admin/accounts/whatsapp/{id}
                                          → remove one WhatsApp session (filters kept)
  POST  /api/v1/admin/accounts/whatsapp/cleanup-closed
                                          → bulk-delete closed WhatsApp sessions
  DELETE /api/v1/admin/jobs/linkedin/{id} → remove one campaign job (+ its task)
  POST  /api/v1/admin/jobs/linkedin/bulk-delete
                                          → bulk-delete campaign jobs by status/age
  GET   /api/v1/admin/queues/stale-preview → preview stale Celery tasks (all users)
  POST  /api/v1/admin/queues/cleanup-stale → revoke stale tasks + leases

``/me`` is deliberately available to any authenticated user — the frontend
calls it to decide whether to render the Admin Dashboard button. Every other
route goes through :func:`require_admin`, which hard-blocks non-admins once
``ADMIN_API_ENFORCED=true`` and logs the attempt meanwhile.

Cleanup contract: deleting sessions or jobs NEVER deletes a WhatsApp filter
job (``WhatsAppScanFilter``). Filters belong to the login account that owns
them; a removed session only detaches them (``session_id → NULL``) so they
keep working against the owner's remaining devices.
"""
from datetime import datetime, timedelta, timezone

import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db, require_admin
from api.v1.system_queues import (
    _celery_inspect,
    _inspection_args,
    _inspection_request,
    _LEGACY_AUTOMATION_TASKS,
)
from core.config import settings as app_config
from core.logging_config import get_logger
from models.campaign import Campaign
from models.campaign_job import CampaignJob
from models.linkedin_account import LinkedInAccount
from models.rate_limit import RateLimitCounter
from models.roles import UserRole
from models.user import User
from models.whatsapp import WhatsAppRawMessage, WhatsAppScanFilter, WhatsAppSession
from schemas.admin import (
    AdminAccountsResponse,
    AdminLinkedInAccountRow,
    AdminLinkedInJobRow,
    AdminLinkedInJobsResponse,
    AdminOverviewResponse,
    AdminUserRow,
    AdminUsersResponse,
    AdminWhatsAppJobRow,
    AdminWhatsAppJobsResponse,
    AdminWhatsAppSessionRow,
    ClosedSessionItem,
    ClosedSessionsCleanupRequest,
    ClosedSessionsCleanupResponse,
    LinkedInJobsBulkDeleteRequest,
    LinkedInJobsBulkDeleteResponse,
    MyRolesResponse,
    SettingsResponse,
    StaleCleanupRequest,
    StaleCleanupResponse,
    StalePreviewResponse,
    StaleTaskItem,
    UpdateSettingsRequest,
    UpdateUserRolesRequest,
    UpdateUserRolesResponse,
)
from services.app_settings import describe_settings, get_settings_map, set_settings
from services.user_roles import (
    get_user_roles,
    is_admin,
    primary_role,
    set_user_roles,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ── Role discovery ───────────────────────────────────────────────────────────


@router.get("/me", response_model=MyRolesResponse)
async def read_my_roles(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MyRolesResponse:
    """The caller's roles. Any authenticated user may call this."""
    roles = await get_user_roles(db, current_user.email)
    return MyRolesResponse(
        email=current_user.email,
        roles=roles,
        is_admin=UserRole.ADMIN.value in roles,
        admin_api_enforced=app_config.ADMIN_API_ENFORCED,
    )


# ── Overview ─────────────────────────────────────────────────────────────────


async def _safe_execute(db: AsyncSession, statement):
    """Run ``statement``, tolerating a missing table, without killing the session.

    PostgreSQL aborts the *whole* transaction as soon as one statement errors:
    every subsequent query then fails with ``InFailedSQLTransactionError`` until
    a rollback happens. A bare ``try/except`` around a query is therefore not
    enough — the first tolerated failure would poison every later count in the
    same request. Wrapping each attempt in a SAVEPOINT (``db.begin_nested()``)
    confines the rollback to that one statement, so the dashboard degrades
    gracefully (a zero for the missing table) instead of 500-ing.

    Returns ``None`` when the statement could not be run.
    """
    try:
        async with db.begin_nested():
            return await db.execute(statement)
    except Exception as exc:  # pragma: no cover - depends on deployment schema
        logger.debug("admin dashboard query skipped: %s", exc)
        return None


async def _scalar(db: AsyncSession, statement) -> int:
    result = await _safe_execute(db, statement)
    if result is None:
        return 0
    return int(result.scalar() or 0)


async def _group_counts(db: AsyncSession, column, table_column) -> dict[str, int]:
    result = await _safe_execute(
        db, select(column, func.count(table_column)).group_by(column)
    )
    if result is None:
        return {}
    rows = result.all()
    out: dict[str, int] = {}
    for value, count in rows:
        key = str(value.value if hasattr(value, "value") else value)
        out[key] = int(count or 0)
    return out


@router.get("/overview", response_model=AdminOverviewResponse)
async def admin_overview(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminOverviewResponse:
    """Users, accounts, and job totals for the admin dashboard."""
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)

    total_users = await _scalar(db, select(func.count(User.email)))
    verified_users = await _scalar(
        db, select(func.count(User.email)).where(User.is_verified == True)  # noqa: E712
    )
    admin_users = await _scalar(
        db,
        select(func.count(User.email)).where(User.role == UserRole.ADMIN.value),
    )

    linkedin_total = await _scalar(db, select(func.count(LinkedInAccount.id)))
    linkedin_by_status = await _group_counts(
        db, LinkedInAccount.status, LinkedInAccount.id
    )
    whatsapp_total = await _scalar(db, select(func.count(WhatsAppSession.id)))
    whatsapp_connected = await _scalar(
        db,
        select(func.count(WhatsAppSession.id)).where(
            WhatsAppSession.status == "connected"
        ),
    )

    jobs_by_status = await _group_counts(db, CampaignJob.status, CampaignJob.id)
    campaigns_by_status = await _group_counts(db, Campaign.status, Campaign.id)
    jobs_last_24h = await _scalar(
        db,
        select(func.count(CampaignJob.id)).where(CampaignJob.created_at >= day_ago),
    )

    active_windows = await _scalar(
        db,
        select(func.count(RateLimitCounter.id)).where(
            RateLimitCounter.window_started_at >= now - timedelta(hours=1)
        ),
    )
    throttled = await _scalar(
        db,
        select(func.count(RateLimitCounter.id)).where(
            RateLimitCounter.window_started_at >= now - timedelta(hours=24),
            RateLimitCounter.request_count > 1,
        ),
    )

    return AdminOverviewResponse(
        users={
            "total": total_users,
            "verified": verified_users,
            "unverified": max(0, total_users - verified_users),
            "admins": admin_users,
        },
        accounts={
            "linkedin_total": linkedin_total,
            "linkedin_by_status": linkedin_by_status,
            "whatsapp_total": whatsapp_total,
            "whatsapp_connected": whatsapp_connected,
        },
        jobs={
            "by_status": jobs_by_status,
            "total": sum(jobs_by_status.values()),
            "last_24h": jobs_last_24h,
            "campaigns_by_status": campaigns_by_status,
            "campaigns_total": sum(campaigns_by_status.values()),
        },
        rate_limits={
            "active_windows_last_hour": active_windows,
            "counters_with_traffic_24h": throttled,
            "enabled": app_config.RATE_LIMIT_ENABLED,
        },
        generated_at=now,
    )


# ── Accounts ─────────────────────────────────────────────────────────────────


# Statuses that can never do work again. ``connected`` is deliberately absent:
# cleanup endpoints refuse to touch a live session.
CLOSED_SESSION_STATUSES = {"disconnected", "error", "waiting_qr"}
KNOWN_SESSION_STATUSES = CLOSED_SESSION_STATUSES | {"connected"}


def _best_effort_revoke(task_ids: list[str], *, terminate: bool = False) -> list[str]:
    """Revoke Celery tasks, never raising — cleanup must not fail on Redis."""
    if not task_ids:
        return []
    try:
        from worker.celery_app import celery_app
    except Exception as exc:
        logger.warning("Celery unavailable, could not revoke %d task(s): %s", len(task_ids), exc)
        return []
    revoked: list[str] = []
    for task_id in task_ids:
        try:
            celery_app.control.revoke(task_id, terminate=terminate)
            revoked.append(task_id)
        except Exception as exc:
            logger.debug("Could not revoke Celery task %s: %s", task_id, exc)
    return revoked


def _revoke_tasks_for_filters(filter_ids: set[int]) -> list[str]:
    """Revoke queued/active Celery scan tasks that target these filters.

    Best-effort: a single worker-inspect pass, matched by task arg. The
    filters themselves are untouched — only transient queue entries go away.
    """
    if not filter_ids:
        return []
    try:
        snapshot = _celery_inspect()
    except Exception as exc:
        logger.warning("Celery inspect failed during session cleanup: %s", exc)
        return []
    raw = snapshot.get("raw") or {}
    wanted = {int(value) for value in filter_ids}
    task_ids: list[str] = []
    for bucket in ("active", "scheduled", "reserved"):
        by_worker = raw.get(bucket) or {}
        if not isinstance(by_worker, dict):
            continue
        for worker_tasks in by_worker.values():
            if not isinstance(worker_tasks, list):
                continue
            for item in worker_tasks:
                request = _inspection_request(item)
                if request.get("name") != "tasks.check_whatsapp_messages":
                    continue
                args = _inspection_args(request)
                try:
                    filter_id = int(args[0]) if args else None
                except (TypeError, ValueError):
                    filter_id = None
                task_id = request.get("id")
                if filter_id in wanted and task_id:
                    task_ids.append(task_id)
    return _best_effort_revoke(task_ids, terminate=False)


def _delete_whatsapp_leases(filter_ids: set[int]) -> int:
    """Drop scheduler leases for filters so no new scan is queued for them."""
    if not filter_ids:
        return 0
    try:
        import redis

        from core.config import settings

        client = redis.from_url(settings.REDIS_URL)
    except Exception as exc:
        logger.warning("Redis unavailable, could not clear scheduler leases: %s", exc)
        return 0
    deleted = 0
    for filter_id in filter_ids:
        try:
            deleted += int(client.delete(f"linkeasy:scheduler:whatsapp:{filter_id}") or 0)
        except Exception as exc:
            logger.debug("Could not delete lease for filter %s: %s", filter_id, exc)
    return deleted


async def _remove_profile_dir(profile_dir: str | None, session_id: int) -> bool:
    """Remove a deleted session's on-disk browser profile.

    Only an explicit per-session directory is ever removed. Legacy rows with
    ``profile_dir IS NULL`` resolve to the *shared* flat profile directory,
    which other sessions still use — deleting it would log everyone out.
    """
    if not profile_dir:
        return False
    import shutil as _shutil

    try:
        await asyncio.to_thread(_shutil.rmtree, profile_dir)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        logger.warning(
            "Could not remove WhatsApp profile dir for session %s",
            session_id,
            exc_info=True,
        )
        return False


async def _delete_whatsapp_session_rows(
    db: AsyncSession, sessions: list[WhatsAppSession]
) -> tuple[list[ClosedSessionItem], set[int]]:
    """Delete session rows, detaching (never deleting) their filter jobs.

    Returns the per-session summaries plus the ids of every filter that was
    kept and detached, so queue hygiene can target exactly those filters.
    """
    items: list[ClosedSessionItem] = []
    detached_ids: set[int] = set()
    for session in sessions:
        attached = (
            await db.execute(
                select(WhatsAppScanFilter.id).where(
                    WhatsAppScanFilter.session_id == session.id
                )
            )
        ).scalars().all()
        ids = {int(value) for value in attached}
        if ids:
            await db.execute(
                update(WhatsAppScanFilter)
                .where(WhatsAppScanFilter.session_id == session.id)
                .values(session_id=None)
            )
        detached_ids |= ids
        items.append(
            ClosedSessionItem(
                id=session.id,
                status=session.status or "disconnected",
                is_active=bool(session.is_active),
                owner_email=getattr(session, "owner_email", None),
                updated_at=session.updated_at,
                detached_filters=len(ids),
            )
        )
        # Scrub persisted credentials before the row goes away.
        session.cookies_json = None
        session.storage_state_json = None
        session.is_active = False
        session.status = "disconnected"
        await db.delete(session)
    await db.commit()
    return items, detached_ids


@router.delete("/accounts/whatsapp/{session_id}")
async def admin_delete_whatsapp_session(
    session_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Remove one WhatsApp session, its credentials, and its profile dir.

    Filter jobs attached to the session are NOT deleted: they stay with the
    login account that owns them and are simply unlinked from this device
    (``session_id → NULL``), so the owner's remaining devices keep working.
    """
    session = await db.scalar(
        select(WhatsAppSession).where(WhatsAppSession.id == session_id)
    )
    if session is None:
        raise HTTPException(status_code=404, detail="WhatsApp session not found")

    profile_dir = getattr(session, "profile_dir", None)
    items, detached_ids = await _delete_whatsapp_session_rows(db, [session])

    # Best-effort queue hygiene for exactly the detached filters (outside the
    # DB transaction — a down Redis must not resurrect the deleted row).
    revoked = _revoke_tasks_for_filters(detached_ids)
    _delete_whatsapp_leases(detached_ids)
    profile_removed = await _remove_profile_dir(profile_dir, session_id)

    logger.info(
        "🧹 %s removed WhatsApp session %s (filters preserved: %d, tasks revoked: %d)",
        admin.email,
        session_id,
        len(detached_ids),
        len(revoked),
    )
    return {
        "deleted": session_id,
        "detached_filters": len(detached_ids),
        "revoked_tasks": len(revoked),
        "profile_dir_removed": profile_removed,
    }


@router.post("/accounts/whatsapp/cleanup-closed", response_model=ClosedSessionsCleanupResponse)
async def admin_cleanup_closed_whatsapp_sessions(
    payload: ClosedSessionsCleanupRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ClosedSessionsCleanupResponse:
    """Bulk-delete closed WhatsApp sessions (disconnected/error/QR/inactive).

    ``dry_run=true`` only previews the matches. Connected sessions are never
    matched, and filter jobs are never deleted — they are detached and stay
    attached to the login account.
    """
    statuses = {str(value or "").strip().lower() for value in payload.statuses}
    unknown = statuses - KNOWN_SESSION_STATUSES
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown session status: {sorted(unknown)}. "
            f"Known: {sorted(KNOWN_SESSION_STATUSES)}",
        )
    if "connected" in statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Refusing to treat 'connected' sessions as closed — "
            "remove a live session explicitly if you really mean it.",
        )

    conditions = [WhatsAppSession.status.in_(sorted(statuses))]
    if payload.include_inactive:
        conditions.append(WhatsAppSession.is_active.is_(False))
    statement = select(WhatsAppSession).where(or_(*conditions))
    if payload.session_ids:
        statement = statement.where(WhatsAppSession.id.in_(payload.session_ids))
    if payload.older_than_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=payload.older_than_days)
        statement = statement.where(WhatsAppSession.updated_at < cutoff)
    statement = statement.order_by(WhatsAppSession.id.desc()).limit(payload.limit)

    result = await _safe_execute(db, statement)
    matched = list(result.scalars().all()) if result is not None else []

    skipped: list[dict] = []
    if payload.session_ids:
        matched_ids = {session.id for session in matched}
        for wanted in payload.session_ids:
            if wanted not in matched_ids:
                skipped.append({"id": wanted, "reason": "not closed (still connected/active)"})

    if payload.dry_run:
        return ClosedSessionsCleanupResponse(
            dry_run=True,
            matched=len(matched),
            deleted=0,
            skipped=skipped,
            sessions=[
                ClosedSessionItem(
                    id=session.id,
                    status=session.status or "disconnected",
                    is_active=bool(session.is_active),
                    owner_email=getattr(session, "owner_email", None),
                    updated_at=session.updated_at,
                )
                for session in matched
            ],
        )

    errors: list[str] = []
    items: list[ClosedSessionItem] = []
    detached_ids: set[int] = set()
    if matched:
        try:
            items, detached_ids = await _delete_whatsapp_session_rows(db, matched)
        except Exception as exc:
            logger.exception("Closed-session cleanup failed for %s", admin.email)
            errors.append(str(exc))
            return ClosedSessionsCleanupResponse(
                dry_run=False,
                matched=len(matched),
                deleted=0,
                skipped=skipped,
                sessions=[],
                preserved_filters=0,
                errors=errors,
            )

    # Best-effort queue + disk hygiene (never fails the request).
    revoked = _revoke_tasks_for_filters(detached_ids)
    _delete_whatsapp_leases(detached_ids)

    dirs_removed = 0
    for session in matched:
        if await _remove_profile_dir(getattr(session, "profile_dir", None), session.id):
            dirs_removed += 1

    logger.info(
        "🧹 %s cleaned %d closed WhatsApp session(s) (filters preserved: %d, tasks revoked: %d)",
        admin.email,
        len(items),
        len(detached_ids),
        len(revoked),
    )
    return ClosedSessionsCleanupResponse(
        dry_run=False,
        matched=len(matched),
        deleted=len(items),
        skipped=skipped,
        sessions=items,
        preserved_filters=len(detached_ids),
        revoked_tasks=len(revoked),
        profile_dirs_removed=dirs_removed,
        errors=errors,
    )


@router.get("/accounts", response_model=AdminAccountsResponse)
async def admin_accounts(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAccountsResponse:
    """Every LinkedIn account and WhatsApp session across all users."""
    li_result = await _safe_execute(
        db,
        select(LinkedInAccount).order_by(LinkedInAccount.created_at.desc()).limit(500),
    )
    li_rows = li_result.scalars().all() if li_result is not None else []

    wa_result = await _safe_execute(
        db,
        select(WhatsAppSession).order_by(WhatsAppSession.id.desc()).limit(500),
    )
    wa_rows = wa_result.scalars().all() if wa_result is not None else []

    from core.profiles import profile_dir_missing
    from services.whatsapp_browser import whatsapp_profile_dir

    def _wa_profile_dir(row) -> str:
        # Per-user rollout: explicit column wins; legacy rows resolve to the
        # shared flat directory.
        return getattr(row, "profile_dir", None) or whatsapp_profile_dir()

    linkedin = [
        AdminLinkedInAccountRow(
            id=row.id,
            owner_email=row.owner_email,
            linkedin_email=row.linkedin_email,
            label=row.label,
            status=row.status.value if hasattr(row.status, "value") else row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
            profile_missing=profile_dir_missing(row.profile_dir),
        )
        for row in li_rows
    ]
    whatsapp = [
        AdminWhatsAppSessionRow(
            id=row.id,
            status=row.status,
            is_active=bool(row.is_active),
            created_at=row.created_at,
            updated_at=row.updated_at,
            owner_email=getattr(row, "owner_email", None),
            profile_missing=profile_dir_missing(_wa_profile_dir(row)),
        )
        for row in wa_rows
    ]

    return AdminAccountsResponse(
        linkedin=linkedin,
        whatsapp=whatsapp,
        counts={
            "linkedin_total": len(linkedin),
            "linkedin_active": sum(
                1 for row in linkedin if (row.status or "") in ("active", "valid")
            ),
            "whatsapp_total": len(whatsapp),
            "whatsapp_connected": sum(1 for row in whatsapp if row.status == "connected"),
        },
    )


# ── LinkedIn jobs (campaign audit log) ───────────────────────────────────────


@router.get("/jobs/linkedin", response_model=AdminLinkedInJobsResponse)
async def admin_linkedin_jobs(
    limit: int = Query(200, ge=1, le=1000),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminLinkedInJobsResponse:
    """Recent campaign (LinkedIn) jobs across all users, newest first."""
    result = await _safe_execute(
        db,
        select(CampaignJob, Campaign.name)
        .join(Campaign, Campaign.id == CampaignJob.campaign_id, isouter=True)
        .order_by(CampaignJob.created_at.desc())
        .limit(limit),
    )
    rows = result.all() if result is not None else []

    jobs = [
        AdminLinkedInJobRow(
            id=job.id,
            campaign_id=job.campaign_id,
            campaign_name=campaign_name,
            step_type=job.step_type,
            status=job.status.value if hasattr(job.status, "value") else job.status,
            action_message=job.action_message,
            error_message=job.error_message,
            scheduled_at=job.scheduled_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            created_at=job.created_at,
        )
        for job, campaign_name in rows
    ]
    return AdminLinkedInJobsResponse(jobs=jobs, count=len(jobs))


VALID_JOB_STATUSES = {"queued", "running", "done", "failed", "skipped"}


def _job_status_value(job) -> str:
    return str(job.status.value if hasattr(job.status, "value") else job.status)


@router.delete("/jobs/linkedin/{job_id}")
async def admin_delete_linkedin_job(
    job_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Remove one campaign (LinkedIn) job and revoke its Celery task, if any.

    Works for jobs in any state — queued, running, failed, or already done —
    so stuck leftovers can be cleared without waiting for completion.
    """
    job = await db.scalar(select(CampaignJob).where(CampaignJob.id == job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Campaign job not found")

    revoked = _best_effort_revoke([job.celery_task_id] if job.celery_task_id else [])
    await db.delete(job)
    await db.commit()
    logger.info("🧹 %s removed LinkedIn job %s", admin.email, job_id)
    return {"deleted": job_id, "revoked_tasks": len(revoked)}


@router.post("/jobs/linkedin/bulk-delete", response_model=LinkedInJobsBulkDeleteResponse)
async def admin_bulk_delete_linkedin_jobs(
    payload: LinkedInJobsBulkDeleteRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> LinkedInJobsBulkDeleteResponse:
    """Bulk-delete campaign (LinkedIn) jobs by status, age, or explicit ids.

    ``dry_run=true`` only previews the matches. Every deleted job's Celery
    task is revoked first so a removed row cannot keep running in the worker.
    """
    if payload.statuses is not None:
        statuses = {str(value or "").strip().lower() for value in payload.statuses}
        unknown = statuses - VALID_JOB_STATUSES
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown job status: {sorted(unknown)}. "
                f"Known: {sorted(VALID_JOB_STATUSES)}",
            )
    else:
        statuses = set()

    statement = select(CampaignJob)
    if statuses:
        statement = statement.where(CampaignJob.status.in_(sorted(statuses)))
    if payload.campaign_id:
        statement = statement.where(CampaignJob.campaign_id == payload.campaign_id)
    if payload.job_ids:
        statement = statement.where(CampaignJob.id.in_(payload.job_ids))
    if payload.older_than_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=payload.older_than_days)
        statement = statement.where(CampaignJob.created_at < cutoff)
    statement = statement.order_by(CampaignJob.created_at.desc()).limit(payload.limit)

    result = await _safe_execute(db, statement)
    matched = list(result.scalars().all()) if result is not None else []

    def _sample(job) -> dict:
        return {
            "id": job.id,
            "status": _job_status_value(job),
            "campaign_id": job.campaign_id,
            "step_type": job.step_type,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }

    if payload.dry_run:
        return LinkedInJobsBulkDeleteResponse(
            dry_run=True,
            matched=len(matched),
            deleted=0,
            sample=[_sample(job) for job in matched[:20]],
        )

    revoked = _best_effort_revoke([job.celery_task_id for job in matched if job.celery_task_id])
    for job in matched:
        await db.delete(job)
    await db.commit()

    logger.info(
        "🧹 %s bulk-deleted %d LinkedIn job(s) statuses=%s (tasks revoked: %d)",
        admin.email,
        len(matched),
        sorted(statuses) or "any",
        len(revoked),
    )
    return LinkedInJobsBulkDeleteResponse(
        dry_run=False,
        matched=len(matched),
        deleted=len(matched),
        revoked_tasks=len(revoked),
        sample=[_sample(job) for job in matched[:20]],
    )


# ── WhatsApp jobs (filter jobs) ──────────────────────────────────────────────
#
# NOTE: there is intentionally NO admin delete endpoint for filter jobs.
# Filters belong to the login account that owns them; only the owner deletes
# them (DELETE /api/v1/whatsapp/filters/jobs/{id}). Admin queue hygiene for
# WhatsApp lives in the stale-task endpoints below, which only ever revoke
# transient Celery/Redis state and never touch a filter row.


@router.get("/jobs/whatsapp", response_model=AdminWhatsAppJobsResponse)
async def admin_whatsapp_jobs(
    limit: int = Query(200, ge=1, le=1000),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminWhatsAppJobsResponse:
    """WhatsApp filter jobs across all users with their message counters."""
    result = await _safe_execute(
        db,
        select(WhatsAppScanFilter).order_by(WhatsAppScanFilter.id.desc()).limit(limit),
    )
    rows = result.scalars().all() if result is not None else []

    jobs: list[AdminWhatsAppJobRow] = []
    for row in rows:
        total = await _scalar(
            db,
            select(func.count()).select_from(WhatsAppRawMessage).where(
                WhatsAppRawMessage.filter_id == row.id
            ),
        )
        matched = await _scalar(
            db,
            select(func.count()).select_from(WhatsAppRawMessage).where(
                WhatsAppRawMessage.filter_id == row.id,
                WhatsAppRawMessage.status == "matched",
            ),
        )
        rejected = await _scalar(
            db,
            select(func.count()).select_from(WhatsAppRawMessage).where(
                WhatsAppRawMessage.filter_id == row.id,
                WhatsAppRawMessage.status == "rejected",
            ),
        )
        forwarded = await _scalar(
            db,
            select(func.count()).select_from(WhatsAppRawMessage).where(
                WhatsAppRawMessage.filter_id == row.id,
                WhatsAppRawMessage.forwarded == True,  # noqa: E712
            ),
        )
        jobs.append(
            AdminWhatsAppJobRow(
                id=row.id,
                name=row.name or "WhatsApp Filter",
                status=row.status or "draft",
                role=row.role,
                job_title=row.job_title,
                keywords=row.keywords or [],
                interval_hours=float(row.interval_hours or 1.0),
                next_scan_at=row.next_scan_at,
                last_scan_at=row.last_scan_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
                total_count=total,
                matched_count=matched,
                rejected_count=rejected,
                forwarded_count=forwarded,
            )
        )

    return AdminWhatsAppJobsResponse(jobs=jobs, count=len(jobs))


# ── Stale Celery tasks (global, all users) ───────────────────────────────────
#
# Reserved/scheduled tasks whose campaign, feed scan, or WhatsApp filter is no
# longer active keep occupying the worker without doing useful work. These
# endpoints let an admin *check* (preview) and *revoke* them. Revoking only
# drops transient queue entries — no job row and no filter row is deleted.


_TASK_SCOPE_LINKEDIN = {
    "tasks.run_account_session",
    "tasks.reconcile_stalled_leads",
    "tasks.execute_campaign_step",
    "tasks.step1_visit_profile",
    "tasks.step1_visit_and_like",
    "tasks.step2_send_connection",
    "tasks.step3_send_message",
    "tasks.step4_followup_if_pending",
    "tasks.step5_thanks_if_accepted",
}
_TASK_SCOPE_WHATSAPP = {"tasks.check_whatsapp_messages", "tasks.connect_whatsapp"}
_TASK_SCOPE_FEED = {"tasks.run_feed_scroll"}
VALID_STALE_SCOPES = {"all", "linkedin", "whatsapp", "feed"}


def _task_scope(task_name: str) -> str:
    if task_name in _TASK_SCOPE_LINKEDIN:
        return "linkedin"
    if task_name in _TASK_SCOPE_WHATSAPP:
        return "whatsapp"
    if task_name in _TASK_SCOPE_FEED:
        return "feed"
    return "other"


async def _global_active_sets(db: AsyncSession) -> tuple[set, set, set, set]:
    """Active automation owners across ALL users.

    Returns ``(active_accounts, active_feed_ids, active_filter_ids, known)``
    where ``known`` names the scopes whose tables could actually be read — a
    missing table must mark its scope unknown (never "nothing is active"),
    otherwise every task of that scope would look stale.
    """
    from models.campaign import CampaignStatus
    from models.feed_scroll_job import FeedScrollJob, FeedScrollJobStatus
    from models.linkedin_account import LinkedInAccount

    active_accounts: set = set()
    active_feed_ids: set = set()
    active_filter_ids: set = set()
    known: set = set()

    accounts_result = await _safe_execute(
        db,
        select(Campaign.account_email)
        .join(LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email)
        .where(Campaign.status == CampaignStatus.ACTIVE),
    )
    if accounts_result is not None:
        active_accounts = {row[0] for row in accounts_result.all()}
        known.add("linkedin")

    feed_result = await _safe_execute(
        db,
        select(FeedScrollJob.id).where(FeedScrollJob.status == FeedScrollJobStatus.ACTIVE),
    )
    if feed_result is not None:
        active_feed_ids = {str(row[0]) for row in feed_result.all()}
        known.add("feed")

    filter_result = await _safe_execute(
        db,
        select(WhatsAppScanFilter.id).where(WhatsAppScanFilter.status == "active"),
    )
    if filter_result is not None:
        active_filter_ids = {int(row[0]) for row in filter_result.all()}
        known.add("whatsapp")

    return active_accounts, active_feed_ids, active_filter_ids, known


def _classify_stale_task(
    request: dict,
    active_accounts: set,
    active_feed_ids: set,
    active_filter_ids: set,
    known: set,
) -> StaleTaskItem | None:
    """Return a StaleTaskItem when the inspected task has no active owner."""
    task_name = request.get("name") or ""
    task_id = request.get("id")
    if not task_id:
        return None
    args = _inspection_args(request)
    scope = _task_scope(task_name)

    reason = ""
    if task_name in _LEGACY_AUTOMATION_TASKS:
        reason = "legacy task name — retired by the current scheduler"
    elif task_name == "tasks.run_account_session":
        if "linkedin" not in known:
            return None
        if not args or args[0] not in active_accounts:
            reason = f"account {args[0] if args else '(missing)'} has no active campaign"
    elif task_name == "tasks.run_feed_scroll":
        if "feed" not in known:
            return None
        if not args or str(args[0]) not in active_feed_ids:
            reason = f"feed job {args[0] if args else '(missing)'} is not active"
    elif task_name == "tasks.check_whatsapp_messages":
        if "whatsapp" not in known:
            return None
        filter_id = args[0] if args else None
        try:
            filter_value = int(filter_id) if filter_id is not None else None
        except (TypeError, ValueError):
            filter_value = None
        if filter_value is None:
            if not active_filter_ids:
                reason = "no active WhatsApp filter owns this scan"
        elif filter_value not in active_filter_ids:
            reason = f"filter {filter_value} is not active"
    else:
        return None

    if not reason:
        return None
    return StaleTaskItem(id=task_id, name=task_name, args=args, scope=scope, reason=reason)


async def _collect_stale_tasks(db: AsyncSession, scope: str) -> tuple[list[StaleTaskItem], int, list[str], str | None]:
    """Inspect reserved/scheduled work and classify stale entries globally."""
    active_accounts, active_feed_ids, active_filter_ids, known = await _global_active_sets(db)
    try:
        snapshot = _celery_inspect()
    except Exception as exc:
        return [], 0, [], f"Celery inspect failed: {exc}"
    raw = snapshot.get("raw") or {}
    workers = list((raw.get("active") or {}).keys())
    error = snapshot.get("error")

    candidates: list = []
    for bucket in ("scheduled", "reserved"):
        by_worker = raw.get(bucket) or {}
        if not isinstance(by_worker, dict):
            continue
        for worker_tasks in by_worker.values():
            if isinstance(worker_tasks, list):
                candidates.extend(worker_tasks)

    stale: list[StaleTaskItem] = []
    for item in candidates:
        classified = _classify_stale_task(
            _inspection_request(item), active_accounts, active_feed_ids, active_filter_ids, known
        )
        if classified is not None and (scope == "all" or classified.scope == scope):
            stale.append(classified)
    return stale, len(candidates), workers, error


@router.get("/queues/stale-preview", response_model=StalePreviewResponse)
async def admin_stale_preview(
    scope: str = Query("all", description="all | linkedin | whatsapp | feed"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> StalePreviewResponse:
    """Preview stale queued/scheduled Celery tasks across all users.

    Read-only: nothing is revoked. Active browser tasks are never listed —
    only reserved and ETA work without an active database owner shows up.
    """
    scope = (scope or "all").strip().lower()
    if scope not in VALID_STALE_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown scope: {scope}. Known: {sorted(VALID_STALE_SCOPES)}",
        )
    stale, inspected, workers, error = await _collect_stale_tasks(db, scope)
    return StalePreviewResponse(
        scope=scope, inspected=inspected, stale_count=len(stale), stale=stale,
        workers=workers, error=error,
    )


@router.post("/queues/cleanup-stale", response_model=StaleCleanupResponse)
async def admin_cleanup_stale_tasks(
    payload: StaleCleanupRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> StaleCleanupResponse:
    """Revoke stale queued/scheduled Celery tasks across all users.

    ``dry_run`` defaults to true — pass ``dry_run=false`` to actually revoke.
    Active browser tasks are never terminated and no database row (job or
    filter) is ever deleted; only transient queue entries and abandoned
    scheduler leases are removed.
    """
    scope = (payload.scope or "all").strip().lower()
    if scope not in VALID_STALE_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown scope: {scope}. Known: {sorted(VALID_STALE_SCOPES)}",
        )
    stale, inspected, workers, error = await _collect_stale_tasks(db, scope)
    targets = stale[: payload.limit]

    if payload.dry_run:
        return StaleCleanupResponse(
            scope=scope, dry_run=True, inspected=inspected,
            revoked_count=0, revoked=targets,
            workers=workers, error=error,
        )

    revoked_ids = set(_best_effort_revoke([task.id for task in targets], terminate=False))
    revoked = [task for task in targets if task.id in revoked_ids]

    # Drop abandoned dispatcher leases for rows that are no longer active.
    deleted_leases: list[str] = []
    try:
        import redis

        from core.config import settings

        client = redis.from_url(settings.REDIS_URL)
        active_accounts, active_feed_ids, active_filter_ids, known = await _global_active_sets(db)
        lease_targets: list[tuple[str, set | None]] = []
        if scope in ("all", "feed") and "feed" in known:
            lease_targets.append(("linkeasy:scheduler:feed:", set(active_feed_ids)))
        if scope in ("all", "whatsapp") and "whatsapp" in known:
            lease_targets.append(("linkeasy:scheduler:whatsapp:", {str(v) for v in active_filter_ids}))
        if scope in ("all", "linkedin") and "linkedin" in known:
            lease_targets.append(("linkeasy:scheduler:account:", set(active_accounts)))
        for prefix, valid in lease_targets:
            assert valid is not None
            for key in client.scan_iter(match=f"{prefix}*", count=200):
                identifier = str(key)[len(prefix):]
                if identifier not in valid and client.delete(key):
                    deleted_leases.append(str(key))
    except Exception as exc:
        logger.warning("Could not sweep scheduler leases during stale cleanup: %s", exc)

    logger.info(
        "🧹 %s revoked %d stale task(s) scope=%s (leases: %d)",
        admin.email, len(revoked), scope, len(deleted_leases),
    )
    return StaleCleanupResponse(
        scope=scope, dry_run=False, inspected=inspected,
        revoked_count=len(revoked), revoked=revoked,
        deleted_lease_count=len(deleted_leases), deleted_leases=deleted_leases,
        workers=workers, error=error,
    )


# ── Users ────────────────────────────────────────────────────────────────────


@router.get("/users", response_model=AdminUsersResponse)
async def list_users(
    q: str | None = Query(None, description="Filter by email or name"),
    limit: int = Query(100, ge=1, le=500),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUsersResponse:
    """Every user with their roles and a little usage context."""
    query = select(User)
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(
            func.lower(User.email).like(pattern)
            | func.lower(User.first_name).like(pattern)
            | func.lower(User.last_name).like(pattern)
        )
    users = (await db.execute(query.limit(limit))).scalars().all()

    # Per-user counts in two grouped queries rather than N+1 per row. Each runs
    # inside a SAVEPOINT so a missing table degrades to zero counts instead of
    # aborting the transaction and taking the whole endpoint down with it.
    accounts_result = await _safe_execute(
        db,
        select(LinkedInAccount.owner_email, func.count(LinkedInAccount.id)).group_by(
            LinkedInAccount.owner_email
        ),
    )
    account_counts = dict(accounts_result.all()) if accounts_result is not None else {}

    campaigns_result = await _safe_execute(
        db,
        select(LinkedInAccount.owner_email, func.count(Campaign.id))
        .join(
            Campaign,
            Campaign.account_email == LinkedInAccount.linkedin_email,
        )
        .group_by(LinkedInAccount.owner_email),
    )
    campaign_counts = (
        dict(campaigns_result.all()) if campaigns_result is not None else {}
    )

    rows: list[AdminUserRow] = []
    for user in users:
        roles = await get_user_roles(db, user.email)
        rows.append(
            AdminUserRow(
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                is_verified=bool(user.is_verified),
                roles=roles,
                primary_role=primary_role(roles),
                linkedin_accounts=int(account_counts.get(user.email, 0) or 0),
                campaigns=int(campaign_counts.get(user.email, 0) or 0),
                created_at=user.created_at,
            )
        )

    return AdminUsersResponse(users=rows, count=len(rows))


@router.put("/users/{email}/roles", response_model=UpdateUserRolesResponse)
async def update_user_roles(
    email: str,
    payload: UpdateUserRolesRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UpdateUserRolesResponse:
    """Assign the complete set of roles for one user."""
    normalized = {role.strip().lower() for role in payload.roles if role.strip()}

    # Guard: never let the last admin drop their own admin role, which would
    # lock everyone out of this API once enforcement is switched on.
    if (
        email.lower() == admin.email.lower()
        and UserRole.ADMIN.value not in normalized
    ):
        other_admins = int(
            (
                await db.execute(
                    select(func.count(User.email)).where(
                        User.role == UserRole.ADMIN.value,
                        func.lower(User.email) != admin.email.lower(),
                    )
                )
            ).scalar()
            or 0
        )
        if other_admins == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "You are the only administrator — promote another user "
                    "before removing your own admin role."
                ),
            )

    try:
        roles = await set_user_roles(db, email, normalized, granted_by=admin.email)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return UpdateUserRolesResponse(
        email=email, roles=roles, primary_role=primary_role(roles)
    )


# ── Settings ─────────────────────────────────────────────────────────────────


@router.get("/settings", response_model=SettingsResponse)
async def read_settings(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    """Campaign parameters, job limits, and rate-limit windows."""
    values = await get_settings_map(db)
    return SettingsResponse(settings=describe_settings(values))


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    payload: UpdateSettingsRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    """Validate and persist setting changes."""
    try:
        await set_settings(db, payload.values, updated_by=admin.email)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    values = await get_settings_map(db)
    return SettingsResponse(settings=describe_settings(values))


# ── Rate limits ──────────────────────────────────────────────────────────────


@router.get("/rate-limits")
async def read_rate_limits(
    limit: int = Query(50, ge=1, le=500),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Current rate-limit counters, busiest first."""
    from services.rate_limiter import DEFAULT_RULES, resolve_rule

    now = datetime.now(timezone.utc)
    result = await _safe_execute(
        db,
        select(RateLimitCounter)
        .where(RateLimitCounter.window_started_at >= now - timedelta(hours=24))
        .order_by(RateLimitCounter.request_count.desc())
        .limit(limit),
    )
    rows = result.scalars().all() if result is not None else []

    rules = {}
    for bucket in DEFAULT_RULES:
        rule = await resolve_rule(db, bucket)
        rules[bucket] = {
            "max_requests": rule.max_requests,
            "window_seconds": rule.window_seconds,
            "description": rule.description,
        }

    return {
        "enabled": app_config.RATE_LIMIT_ENABLED,
        "rules": rules,
        "counters": [
            {
                "identity": row.identity,
                "bucket": row.bucket,
                "request_count": row.request_count,
                "window_started_at": row.window_started_at,
            }
            for row in rows
        ],
    }


@router.post("/rate-limits/reset")
async def reset_rate_limits(
    identity: str | None = Query(None, description="Only clear this identity"),
    bucket: str | None = Query(None, description="Only clear this bucket"),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Clear counters — the Postgres equivalent of flushing ``rate:*``."""
    statement = delete(RateLimitCounter)
    if identity:
        statement = statement.where(RateLimitCounter.identity == identity)
    if bucket:
        statement = statement.where(RateLimitCounter.bucket == bucket)

    result = await db.execute(statement)
    await db.commit()
    deleted = int(getattr(result, "rowcount", 0) or 0)
    logger.info(
        "🧹 %s cleared %d rate-limit counter(s) identity=%s bucket=%s",
        admin.email,
        deleted,
        identity or "*",
        bucket or "*",
    )
    return {"deleted": deleted, "identity": identity, "bucket": bucket}
