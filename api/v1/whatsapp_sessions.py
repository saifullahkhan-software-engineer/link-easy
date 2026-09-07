"""Per-user WhatsApp session resolution.

FILE: api/v1/whatsapp_sessions.py

WhatsApp connections are owner-scoped (same multi-account model as the
LinkedIn accounts rollout). Both the scanner API and the live-chat API need
to resolve *the caller's* session row, so the lookup — including the
one-time adoption of the pre-migration legacy singleton row — lives here.

Adoption rule: if the authenticated caller has no session yet and a legacy
row with ``owner_email IS NULL`` exists, that row is claimed on first visit
(the same pattern the WhatsApp filter endpoints already use for legacy
filter rows). The adopted row keeps ``profile_dir = NULL``, which resolves
to the legacy shared flat profile directory, so a pre-migration install
keeps its working connection without moving files on disk.
"""
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from models.whatsapp import WhatsAppSession

NOT_CONNECTED_DETAIL = "WhatsApp is not connected. Please connect first."


async def get_owned_session(
    db: AsyncSession,
    current_user: User,
    *,
    require_connected: bool = False,
    session_id: Optional[int] = None,
) -> Optional[WhatsAppSession]:
    """Return one of the caller's WhatsApp session rows.

    ``session_id`` selects a specific device/session (a user may be logged
    into more than one — e.g. personal and work). When omitted, the newest of
    the caller's sessions is returned (legacy installs adopt the unowned row),
    so single-session users and older clients are unchanged.

    When ``require_connected`` is set, raises 400 instead of returning a
    missing/disconnected session so endpoints can share the same guard.
    """
    # FastAPI resolves the ``Query`` default to None when a request omits the
    # param, but unit tests call these handlers directly and then the literal
    # ``Query(...)`` object lands here. Treat anything that is not an int as
    # "no pick" so both paths behave identically.
    if not isinstance(session_id, int):
        session_id = None

    if session_id is not None:
        # Resolve the pick inside the caller's own rows: a foreign or deleted
        # session id is simply "not found", never someone else's session.
        result = await db.execute(
            select(WhatsAppSession).where(
                WhatsAppSession.id == session_id,
                WhatsAppSession.owner_email == current_user.email,
            )
        )
        session = result.scalars().first()
    else:
        result = await db.execute(
            select(WhatsAppSession)
            .where(WhatsAppSession.owner_email == current_user.email)
            .order_by(WhatsAppSession.id.desc())
        )
        session = result.scalars().first()

        if session is None:
            legacy_result = await db.execute(
                select(WhatsAppSession)
                .where(WhatsAppSession.owner_email.is_(None))
                .order_by(WhatsAppSession.id.desc())
            )
            legacy = legacy_result.scalars().first()
            if legacy is not None:
                legacy.owner_email = current_user.email
                await db.commit()
                await db.refresh(legacy)
                session = legacy

    if require_connected and (
        session is None or session.status != "connected" or not session.is_active
    ):
        if session_id is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That WhatsApp session is no longer connected — pick another one.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=NOT_CONNECTED_DETAIL,
        )
    return session


async def list_owned_sessions(
    db: AsyncSession,
    current_user: User,
) -> list:
    """Every WhatsApp session the caller owns, newest first (for listing)."""
    result = await db.execute(
        select(WhatsAppSession)
        .where(WhatsAppSession.owner_email == current_user.email)
        .order_by(WhatsAppSession.id.desc())
    )
    return list(result.scalars().all())


def session_profile_dir(session) -> str:
    """Durable Chromium profile dir for a session row.

    The explicit ``profile_dir`` column wins; NULL (legacy rows) resolves to
    the shared flat directory ``{PROFILE_STORAGE_DIR}/whatsapp``.
    """
    from services.whatsapp_browser import whatsapp_profile_dir

    return getattr(session, "profile_dir", None) or whatsapp_profile_dir()
