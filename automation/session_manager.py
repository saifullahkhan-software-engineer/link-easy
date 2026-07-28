"""
Session Manager for Pending LinkedIn Login Sessions
FILE: automation/session_manager.py

Manages in-memory storage of pending Playwright browser sessions
that are waiting for user verification codes.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PendingLoginSession:
    """Represents a pending LinkedIn login session awaiting verification."""
    session_id: str
    linkedin_email: str
    owner_email: str
    label: Optional[str]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Playwright resources (kept alive for verification)
    pw: Any = None       # Playwright instance
    browser: Any = None  # Browser instance
    context: Any = None  # Browser context
    page: Any = None     # Page instance

    # Account data (to be saved after verification)
    encrypted_password: Optional[str] = None
    user_agent: Optional[str] = None  # User-Agent used during login

    # Per-account profile lock held for the lifetime of this pending session.
    # The browser context (and therefore the Chromium user-data-dir) stays
    # open while awaiting the verification code, so the lock must stay held
    # too — it is released in cleanup_session().
    profile_lock: Any = None

    def is_expired(self, timeout_minutes: int = 15) -> bool:
        """Check if session has expired (default 15 minutes)."""
        return datetime.now(timezone.utc) - self.created_at > timedelta(minutes=timeout_minutes)


class SessionManager:
    """
    Singleton manager for pending login sessions.
    Stores sessions in memory with session IDs for retrieval during verification.

    All cleanup methods are async so that Playwright resources are properly
    awaited and browser processes are not leaked.
    """
    _instance = None
    _sessions: Dict[str, PendingLoginSession] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def create_session(
        self,
        linkedin_email: str,
        owner_email: str,
        label: Optional[str],
        pw: Any,
        browser: Any,
        context: Any,
        page: Any,
        encrypted_password: str,
        user_agent: str,
        profile_lock: Any = None,
    ) -> str:
        """
        Create a new pending login session.
        Returns the session ID for later retrieval.

        profile_lock: optional Redis profile lock (worker/profile_lock.py)
        whose ownership transfers to this pending session — it stays held
        while the browser context is open and is released on cleanup.
        """
        session_id = str(uuid.uuid4())

        session = PendingLoginSession(
            session_id=session_id,
            linkedin_email=linkedin_email,
            owner_email=owner_email,
            label=label,
            pw=pw,
            browser=browser,
            context=context,
            page=page,
            encrypted_password=encrypted_password,
            user_agent=user_agent,
            profile_lock=profile_lock,
        )

        self._sessions[session_id] = session
        return session_id

    def get_session(self, session_id: str) -> Optional[PendingLoginSession]:
        """
        Retrieve a pending session by ID.

        NOTE: If the session is expired its cleanup is *not* triggered here
        because this method is synchronous.  Callers that detect expiry should
        call ``await session_manager.cleanup_session(session_id)`` explicitly,
        or rely on the periodic cleanup task.
        """
        session = self._sessions.get(session_id)
        if session and session.is_expired():
            # Remove the stale reference immediately so no new callers can
            # obtain it; the Playwright resources will be released by the
            # next periodic-cleanup cycle.
            del self._sessions[session_id]
            return None
        return session

    async def cleanup_session(self, session_id: str) -> bool:
        """
        Await the close of all Playwright resources for *session_id* and
        remove it from storage.

        Returns True if the session was found and cleaned up, False otherwise.

        Must be called from an async context (e.g. a FastAPI route handler or
        the periodic cleanup task).
        """
        session = self._sessions.pop(session_id, None)
        if not session:
            return False

        # Close resources in the correct order: context → browser → playwright.
        # Each step is attempted independently so that a failure in one does
        # not prevent the others from being released.
        if session.context:
            try:
                await session.context.close()
            except Exception:
                logger.warning(
                    "Failed to close browser context for session %s",
                    session_id,
                    exc_info=True,
                )

        if session.browser:
            try:
                await session.browser.close()
            except Exception:
                logger.warning(
                    "Failed to close browser for session %s",
                    session_id,
                    exc_info=True,
                )

        if session.pw:
            try:
                await session.pw.stop()
            except Exception:
                logger.warning(
                    "Failed to stop Playwright for session %s",
                    session_id,
                    exc_info=True,
                )

        # Release the per-account profile lock now that the browser context
        # (and its grip on the Chromium user-data-dir) is closed.
        if session.profile_lock is not None:
            try:
                from worker.profile_lock import release_profile_lock
                release_profile_lock(session.profile_lock)
            except Exception:
                logger.warning(
                    "Failed to release profile lock for session %s",
                    session_id,
                    exc_info=True,
                )

        logger.debug("Cleaned up pending login session %s", session_id)
        return True

    async def cleanup_expired_sessions(self, timeout_minutes: int = 15) -> int:
        """
        Await the cleanup of all sessions that have exceeded *timeout_minutes*.
        Returns the count of sessions that were cleaned up.
        """
        expired_ids = [
            sid
            for sid, session in list(self._sessions.items())
            if session.is_expired(timeout_minutes)
        ]

        for sid in expired_ids:
            await self.cleanup_session(sid)

        if expired_ids:
            logger.info("Periodic cleanup removed %d expired session(s)", len(expired_ids))

        return len(expired_ids)


async def _periodic_cleanup_loop(interval_seconds: int, timeout_minutes: int) -> None:
    """Infinite loop that cleans up expired sessions on a fixed interval."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await session_manager.cleanup_expired_sessions(timeout_minutes)
        except Exception:
            logger.exception("Unexpected error during periodic session cleanup")


def start_periodic_cleanup(
    interval_seconds: int = 300,
    timeout_minutes: int = 15,
) -> "asyncio.Task[None]":
    """
    Schedule a background asyncio.Task that periodically removes expired
    pending-login sessions and their Playwright resources.

    Call this once inside the application's async lifespan (startup) and
    cancel the returned task on shutdown::

        task = start_periodic_cleanup()
        yield
        task.cancel()

    Args:
        interval_seconds: How often to run the sweep (default: 5 minutes).
        timeout_minutes:  Session age after which it is considered expired
                          (default: 15 minutes, matches PendingLoginSession).
    """
    task = asyncio.create_task(
        _periodic_cleanup_loop(interval_seconds, timeout_minutes),
        name="session_manager_periodic_cleanup",
    )
    logger.info(
        "Started periodic session cleanup task (interval=%ds, timeout=%dmin)",
        interval_seconds,
        timeout_minutes,
    )
    return task


# Global instance
session_manager = SessionManager()

