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

``/me`` is deliberately available to any authenticated user — the frontend
calls it to decide whether to render the Admin Dashboard button. Every other
route goes through :func:`require_admin`, which hard-blocks non-admins once
``ADMIN_API_ENFORCED=true`` and logs the attempt meanwhile.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db, require_admin
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
    MyRolesResponse,
    SettingsResponse,
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


@router.delete("/accounts/whatsapp/{session_id}", status_code=204)
async def admin_delete_whatsapp_session(
    session_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a WhatsApp session and its persisted credentials."""
    session = await db.scalar(select(WhatsAppSession).where(WhatsAppSession.id == session_id))
    if session is None:
        raise HTTPException(status_code=404, detail="WhatsApp session not found")
    session.cookies_json = None
    session.storage_state_json = None
    session.is_active = False
    session.status = "disconnected"
    await db.delete(session)
    await db.commit()


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

    linkedin = [
        AdminLinkedInAccountRow(
            id=row.id,
            owner_email=row.owner_email,
            linkedin_email=row.linkedin_email,
            label=row.label,
            status=row.status.value if hasattr(row.status, "value") else row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
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


# ── WhatsApp jobs (filter jobs) ──────────────────────────────────────────────


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
