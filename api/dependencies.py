from collections.abc import Iterable
from datetime import datetime, timezone
import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from core.config import settings
from database import async_session
from models.user import User
from schemas.auth import TokenPayload

from models.roles import UserRole

logger = logging.getLogger(__name__)

# Use HTTPBearer for a simple token input in the Swagger UI "Authorize" dialog.
# This separates the login flow (getting the token) from the authorization flow (using the token).
oauth2_scheme = HTTPBearer(auto_error=False)


async def get_db() -> AsyncSession:
    """Dependency to get an async database session."""
    async with async_session() as session:
        yield session


async def get_current_user_from_token(
    token: str, db: AsyncSession
) -> User:
    """Validate an access-token string and load the matching user.

    Used by the normal ``get_current_user`` dependency and by SSE endpoints,
    where EventSource cannot send an ``Authorization`` header, so the token
    arrives as a ``?token=...`` query parameter instead.
    """
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        token_data = TokenPayload(**payload)
        if token_data.token_type != "access":
            raise JWTError("Invalid token type")

        # ``exp`` is the lifetime of this individual access token.  The
        # session claim is the absolute login deadline and protects against a
        # token being minted with a longer lifetime than the session window.
        if token_data.session_expires_at is not None:
            try:
                session_expires_at = datetime.fromtimestamp(
                    float(token_data.session_expires_at), tz=timezone.utc
                )
            except (TypeError, ValueError, OverflowError, OSError) as exc:
                raise JWTError("Invalid session expiry") from exc
            if session_expires_at <= datetime.now(timezone.utc):
                raise JWTError("Session expired")
    except (JWTError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.email == token_data.sub))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def get_current_user(
    auth: HTTPAuthorizationCredentials | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency to get the current authenticated user."""
    if auth is None or auth.scheme != "Bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await get_current_user_from_token(auth.credentials, db)


def require_roles(allowed_roles: Iterable[UserRole]):
    """Legacy single-column role gate.

    Kept for existing call sites. New admin surfaces should use
    :func:`require_admin`, which reads the ``user_roles`` join table and so
    understands a user holding several roles at once.
    """

    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        logger.debug(
            "Role check for user=%s role=%s allowed_roles=%s",
            current_user.email,
            current_user.role,
            allowed_roles,
        )
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return role_checker


async def get_current_roles(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[str]:
    """Every role the caller holds (multi-role aware)."""
    from services.user_roles import get_user_roles

    return await get_user_roles(db, current_user.email)


async def require_admin(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Gate an endpoint to admins, honouring the bootstrap flag.

    While ``ADMIN_API_ENFORCED`` is false (the default during bootstrap) any
    authenticated user may reach admin endpoints, so a brand-new deployment
    can assign the first admin through the UI without being locked out. The
    attempt is logged either way, and flipping ``ADMIN_API_ENFORCED=true``
    turns this into a hard 403 with no code change.
    """
    from services.user_roles import is_admin

    admin = await is_admin(db, current_user.email)
    if admin:
        return current_user

    if not settings.ADMIN_API_ENFORCED:
        logger.warning(
            "⚠️  Admin endpoint reached by non-admin %s — allowed because "
            "ADMIN_API_ENFORCED is false (bootstrap mode). Set it to true "
            "once roles are assigned.",
            current_user.email,
        )
        return current_user

    logger.info("⛔ Admin endpoint denied for %s", current_user.email)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Administrator access is required for this action",
    )


# ---------------------------------------------------------------------------
# Scheduled-work gate (kill switch; off only when SCHEDULED_JOBS_ENABLED=false)
# ---------------------------------------------------------------------------

SCHEDULED_JOBS_DISABLED_DETAIL = (
    "Scheduled and recurring jobs are temporarily turned off on this "
    "instance, so recurring scans cannot be armed right now. You can still "
    "run scans on demand and start campaigns manually — please try again "
    "shortly or contact support."
)


def scheduled_jobs_enabled() -> bool:
    """Whether timer-driven background work may run on this deployment.

    Defaults to enabled on every environment; an operator can set
    SCHEDULED_JOBS_ENABLED=false to switch the timers off. Read at call time
    (not import time) so tests and operators can flip the setting without
    reimporting the module.
    """
    return bool(settings.scheduled_jobs_enabled)


def require_scheduled_jobs_enabled() -> None:
    """FastAPI dependency: 503 unless recurring/timed jobs are allowed.

    Guards generic scheduled work. WhatsApp's recurring filter endpoint uses
    ``require_whatsapp_scheduled_jobs_enabled`` below because the hosted demo
    intentionally keeps the social-upload Beat entry while pausing WhatsApp.
    Without a gate, an instance with that dispatcher disabled would persist a
    ``next_scan_at`` that never fires and show an active job that does nothing.

    Deliberately does NOT guard one-shot, user-initiated actions (a manual
    scan, connect, live chat, campaign start): those are consumed by the
    Celery worker, which keeps running even when the timers are off.

    503 matches the LinkedIn gate: "temporarily unavailable", and not one of
    the auth codes the frontend treats as a dead session.
    """
    if not scheduled_jobs_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=SCHEDULED_JOBS_DISABLED_DETAIL,
        )


def require_whatsapp_scheduled_jobs_enabled() -> None:
    """Reject recurring WhatsApp scans on the hosted demo only.

    Manual scans and live chat do not use this dependency and remain
    available while recurring scans are paused.
    """
    if not settings.whatsapp_scheduled_jobs_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Recurring WhatsApp scans are temporarily paused on this "
                "hosted instance. You can still run a scan on demand and use "
                "live chat."
            ),
        )
