"""
Multi-role helpers backed by the ``user_roles`` join table.

FILE: services/user_roles.py

A user may hold several roles at once — that is what lets a single account
see both the Admin Dashboard and the App Dashboard buttons.

``users.role`` (the original single-value column) is kept as a denormalised
cache of the highest-privilege role so existing JWTs, ``require_roles``, and
older queries keep working unchanged. The join table is the source of truth.

Every user implicitly has ``customer`` access: the app dashboard is available
to everyone who can log in, so an admin who was never explicitly granted
``customer`` still sees the App button.
"""
from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from models.rbac import Role, UserRoleLink
from models.roles import UserRole
from models.user import User

logger = get_logger(__name__)

# Most- to least-privileged. Drives which role is cached in ``users.role``.
ROLE_PRIORITY: tuple[str, ...] = (UserRole.ADMIN.value, UserRole.CUSTOMER.value)


async def get_user_roles(db: AsyncSession, email: str) -> list[str]:
    """Return every role held by ``email``, most-privileged first.

    Falls back to the legacy ``users.role`` column when the join table has no
    rows yet (e.g. a user created before the migration, or before the
    developer has assigned anything).
    """
    try:
        rows = (
            await db.execute(
                select(Role.name)
                .join(UserRoleLink, UserRoleLink.role_id == Role.id)
                .where(UserRoleLink.user_email == email)
            )
        ).scalars().all()
        names = {str(name) for name in rows}
    except Exception as exc:
        # Table may not exist before the migration runs — never break login.
        logger.debug("user_roles unavailable for %s: %s", email, exc)
        names = set()

    if not names:
        user = (
            await db.execute(select(User).where(User.email == email))
        ).scalars().first()
        legacy = getattr(user, "role", None) if user else None
        if legacy:
            names = {legacy.value if hasattr(legacy, "value") else str(legacy)}

    # Everyone who can log in can use the app itself.
    names.add(UserRole.CUSTOMER.value)

    ordered = [role for role in ROLE_PRIORITY if role in names]
    ordered.extend(sorted(names - set(ROLE_PRIORITY)))
    return ordered


def primary_role(roles: Iterable[str]) -> str:
    """The highest-privilege role in ``roles`` (cached on ``users.role``)."""
    role_set = set(roles)
    for role in ROLE_PRIORITY:
        if role in role_set:
            return role
    return UserRole.CUSTOMER.value


async def is_admin(db: AsyncSession, email: str) -> bool:
    return UserRole.ADMIN.value in await get_user_roles(db, email)


async def set_user_roles(
    db: AsyncSession,
    email: str,
    roles: Iterable[str],
    granted_by: Optional[str] = None,
) -> list[str]:
    """Replace ``email``'s roles with ``roles`` and resync the cache column."""
    wanted = {str(role).strip().lower() for role in roles if str(role).strip()}
    valid = {role.value for role in UserRole}
    unknown = sorted(wanted - valid)
    if unknown:
        raise ValueError(f"Unknown role(s): {', '.join(unknown)}")
    if not wanted:
        raise ValueError("A user must keep at least one role")

    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalars().first()
    if user is None:
        raise LookupError(f"User not found: {email}")

    role_rows = (
        await db.execute(select(Role).where(Role.name.in_(wanted)))
    ).scalars().all()
    by_name = {row.name: row for row in role_rows}

    # Seed any role row the migration has not created yet.
    for name in wanted - set(by_name):
        row = Role(name=name, description=f"{name} role")
        db.add(row)
        await db.flush()
        by_name[name] = row

    existing = (
        await db.execute(
            select(UserRoleLink).where(UserRoleLink.user_email == email)
        )
    ).scalars().all()
    existing_by_role_id = {link.role_id: link for link in existing}
    wanted_ids = {by_name[name].id for name in wanted}

    for role_id, link in existing_by_role_id.items():
        if role_id not in wanted_ids:
            await db.delete(link)

    for name in wanted:
        role_row = by_name[name]
        if role_row.id not in existing_by_role_id:
            db.add(
                UserRoleLink(
                    user_email=email,
                    role_id=role_row.id,
                    granted_by=granted_by,
                )
            )

    # Keep the legacy column in sync so existing role checks agree.
    user.role = primary_role(wanted)
    await db.commit()

    logger.info(
        "👤 %s set roles for %s -> %s",
        granted_by or "system",
        email,
        ", ".join(sorted(wanted)),
    )
    return sorted(wanted)
