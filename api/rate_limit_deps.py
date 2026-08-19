"""
FastAPI dependencies for Postgres-backed rate limiting.

FILE: api/rate_limit_deps.py

Usage on an endpoint::

    @router.post("/login", dependencies=[Depends(rate_limit("auth:login"))])

Identity resolution prefers the authenticated user, falling back to the
client IP for anonymous endpoints such as login and signup (where there is
no user yet — that is exactly the surface being brute-forced).

Exceeding a limit returns **429** with a ``Retry-After`` header so clients
and browsers can back off correctly.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from core.config import settings
from core.logging_config import get_logger
from services.rate_limiter import DEFAULT_RULES, check_rate_limit, resolve_rule

logger = get_logger(__name__)


def _client_ip(request: Request) -> str:
    """Best-effort client IP, honouring a reverse proxy's forwarded header."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # First entry is the original client; the rest are proxies.
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _identity_from_token(request: Request) -> Optional[str]:
    """Email from a valid bearer token, else None. Never raises."""
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(
            auth[7:].strip(),
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        subject = payload.get("sub")
        return str(subject) if subject else None
    except Exception:
        # An invalid token just means "anonymous" here; auth itself is
        # enforced by get_current_user on the endpoint.
        return None


def resolve_identity(request: Request, body_email: Optional[str] = None) -> str:
    """Who to count this request against."""
    email = _identity_from_token(request)
    if email:
        return f"user:{email.lower()}"
    if body_email:
        return f"email:{body_email.strip().lower()}"
    return f"ip:{_client_ip(request)}"


def rate_limit(bucket: str):
    """Build a dependency enforcing ``bucket``'s limit."""
    if bucket not in DEFAULT_RULES:
        raise KeyError(f"Unknown rate-limit bucket: {bucket}")

    async def dependency(
        request: Request,
        db: AsyncSession = Depends(get_db),
    ) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return

        rule = await resolve_rule(db, bucket)
        identity = resolve_identity(request)
        result = await check_rate_limit(db, identity, rule)

        if not result.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Too many requests. Try again in "
                    f"{result.retry_after_seconds} second(s)."
                ),
                headers={
                    "Retry-After": str(result.retry_after_seconds),
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

    return dependency
