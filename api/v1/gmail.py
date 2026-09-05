"""
Gmail — API routes.
FILE: api/v1/gmail.py

Gmail support for the app module, alongside LinkedIn and WhatsApp: connect a
personal Gmail (or Google Workspace) mailbox through Google OAuth, then read,
search and check the inbox, read threads, manage labels and read-state, and
send messages. Backed by the Gmail REST API via services/gmail.py.

Design notes (mirroring api/v1/social_scheduler.py — the app's other OAuth
platform):

  * every route authenticates with ``get_current_user`` and scopes its rows
    to ``current_user.email``; another user's connection 404s (no existence
    oracle);
  * OAuth: ``GET /auth-url`` → Google → ``GET /callback``. The callback is a
    bare browser redirect (no Authorization header), so the caller's identity
    travels in a short-lived signed ``state`` JWT minted by the auth-url
    route — that same token is the CSRF check;
  * the callback path is ``/api/v1/gmail/callback`` — the exact route the
    Google Cloud console's Authorized redirect URI must list (Google accepts
    ``http://localhost:8000/...`` for local development);
  * tokens are AES-256-GCM encrypted before they touch the database
    (services/gmail.py apply_tokens/read_tokens) and never appear in a
    response;
  * scopes requested: ``gmail.modify`` + ``gmail.send``. Never
    ``mail.google.com`` (restricted scope). gmail.modify covers reading,
    searching, labels, read/unread, archive and trash; sending needs the
    separate gmail.send scope;
  * Google errors are mapped by category (auth → reconnect prompt, quota →
    429, not_found → 404, upstream → 502) so the UI can act on them.

Route map (prefix /api/v1/gmail):

  GET    /status                      connection summary (no Google calls)
  GET    /auth-url                    start OAuth (returns Google's URL)
  GET    /callback                    Google redirect target (unauthenticated;
                                     identity comes from the signed state)
  DELETE /connection                  disconnect (+ best-effort token revoke)
  GET    /profile                     live mailbox profile (users/me/profile)
  GET    /labels                      label list with per-label totals
  GET    /messages                    search/list summaries (?q &label_ids …)
  GET    /messages/{id}               full message (bodies + attachments meta)
  PATCH  /messages/{id}               add/remove labels (read, star, archive…)
  POST   /messages/{id}/trash         move to trash
  POST   /messages/{id}/untrash       restore from trash
  GET    /messages/{id}/attachments/{attachment_id}   download one attachment
  GET    /threads/{thread_id}         full thread, oldest → newest
  GET    /unread                      inbox unread totals + recent unread
  POST   /send                        compose and send a message
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from typing import Optional
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db
from api.rate_limit_deps import rate_limit
from core.config import settings
from models.gmail import GmailConnection
from models.user import User
from schemas.gmail import (
    GmailAuthUrlResponse,
    GmailLabel,
    GmailMessageDetail,
    GmailMessageListResponse,
    GmailMessageResponse,
    GmailMessageSummary,
    GmailModifyRequest,
    GmailProfileResponse,
    GmailSendRequest,
    GmailSendResponse,
    GmailStatus,
    GmailThreadResponse,
    GmailUnreadResponse,
)
from services.gmail import (
    EXPIRY_SKEW,
    GmailApiError,
    GmailService,
    SCOPES_JOINED,
    apply_tokens,
    message_details,
    raw_message_json,
    read_tokens,
    summarize_message,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/gmail", tags=["gmail"])

# Signed-state TTL for the OAuth dance (same 10 minutes as the social
# scheduler's platforms).
OAUTH_STATE_TTL = timedelta(minutes=10)
OAUTH_STATE_TOKEN_TYPE = "gmail_oauth_state"
# Gmail label/message ids are server-generated alphanumeric strings.
_GM_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_QUERY_MAX = 512


# ── helpers ──────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """Timestamps come back naive from SQLite and aware from Postgres."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _configured_required() -> None:
    if not settings.gmail_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "Gmail is not configured on this instance yet. Ask the operator to "
                "set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (Google Cloud OAuth "
                "web-client credentials — see docs/gmail_setup.md)."
            ),
        )


def _mint_oauth_state(owner_email: str) -> str:
    now = _now()
    payload = {
        "sub": owner_email,
        "platform": "gmail",
        "nonce": uuid.uuid4().hex,
        "iat": now,
        "exp": now + OAUTH_STATE_TTL,
        "token_type": OAUTH_STATE_TOKEN_TYPE,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _read_oauth_state(state: Optional[str]) -> dict:
    """Validate the signed state; raises ValueError when tampered/expired."""
    if not state:
        raise ValueError("missing state")
    try:
        payload = jwt.decode(state, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise ValueError(f"invalid state: {exc}") from exc
    if payload.get("token_type") != OAUTH_STATE_TOKEN_TYPE or payload.get("platform") != "gmail":
        raise ValueError("state does not match this flow")
    owner = payload.get("sub")
    if not owner:
        raise ValueError("state carries no user")
    return payload


def _frontend_redirect(*, connected: bool = False, error: Optional[str] = None) -> RedirectResponse:
    params = {}
    if connected:
        params["connected"] = "1"
    if error:
        params["error"] = error[:300]
    return RedirectResponse(
        url=f"{settings.gmail_oauth_return_url}?{urlencode(params)}", status_code=302
    )


def _redirect_uri(request: Request) -> str:
    """The callback URL registered with Google (must match exactly)."""
    if settings.GOOGLE_REDIRECT_URI:
        return settings.GOOGLE_REDIRECT_URI
    base = (
        settings.PUBLIC_API_URL.rstrip("/")
        if settings.PUBLIC_API_URL
        else str(request.base_url).rstrip("/")
    )
    return f"{base}{router.prefix}/callback"


def _service(request: Request) -> GmailService:
    service = GmailService()
    service.redirect_uri = _redirect_uri(request)
    return service


async def _owned_connection(
    db: AsyncSession, owner_email: str
) -> Optional[GmailConnection]:
    result = await db.execute(
        select(GmailConnection).where(GmailConnection.owner_email == owner_email)
    )
    return result.scalar_one_or_none()


async def _connection_or_409(db: AsyncSession, owner_email: str) -> GmailConnection:
    conn = await _owned_connection(db, owner_email)
    if conn is None:
        raise HTTPException(status_code=409, detail="Gmail is not connected. Connect it first.")
    return conn


async def _access_token(db: AsyncSession, conn: GmailConnection) -> str:
    """Decrypt the stored access token, refreshing it when (nearly) expired.

    Raises HTTP 409 with a reconnect hint when the stored credentials are
    unusable (encryption key rotated) or Google rejects the refresh
    (user changed their password / revoked the app).
    """
    try:
        tokens = read_tokens(conn)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if tokens.is_expired:
        service = GmailService()
        service.redirect_uri = settings.GOOGLE_REDIRECT_URI or ""
        try:
            renewed = await service.refresh_access_token(tokens.refresh_token)
        except GmailApiError as exc:
            logger.info("Gmail refresh failed for %s: %s", conn.owner_email, exc)
            raise HTTPException(
                status_code=409,
                detail=(
                    "The Gmail connection has expired and Google rejected the refresh. "
                    "Reconnect Gmail to keep using it."
                ),
            ) from exc
        try:
            apply_tokens(
                conn,
                access_token=renewed.get("access_token") or "",
                refresh_token=renewed.get("refresh_token") or tokens.refresh_token,
                expires_in=renewed.get("expires_in"),
            )
            await db.commit()
            tokens = read_tokens(conn)
        except ValueError as exc:  # pragma: no cover - renewal of a fresh token
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return tokens.access_token


def _map_gmail_error(exc: GmailApiError) -> HTTPException:
    """Translate a GmailApiError category into the route's HTTP semantics."""
    if exc.category == "auth":
        return HTTPException(
            status_code=409,
            detail="Gmail access was revoked or has expired. Reconnect Gmail to continue.",
        )
    if exc.category == "quota":
        return HTTPException(status_code=429, detail=str(exc))
    if exc.category == "not_found":
        return HTTPException(status_code=404, detail=str(exc))
    if exc.category == "invalid":
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc)[:300])


def _valid_gmail_id(value: str, label: str) -> str:
    value = (value or "").strip()
    if not _GM_ID_RE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")
    return value


def _int_or_none(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _fetch_summaries(
    svc: GmailService, access_token: str, message_ids: list[str]
) -> list[dict]:
    """Fetch light metadata (From/Subject/Date) for many ids, concurrently.

    messages.list only returns id/snippet/labels — Gmail has no batch list
    endpoint with headers, so each row costs one cheap metadata call. Rows
    that vanish mid-fetch (deleted by another client) are skipped.
    """
    if not message_ids:
        return []
    semaphore = asyncio.Semaphore(8)

    async def one(message_id: str) -> Optional[dict]:
        async with semaphore:
            try:
                raw = await svc.get_message_metadata(access_token, message_id)
            except GmailApiError as exc:
                if exc.category in ("not_found", "invalid"):
                    return None  # deleted between list and fetch
                raise
        try:
            return summarize_message(raw)
        except Exception:
            return None

    results = await asyncio.gather(*(one(i) for i in message_ids))
    return [r for r in results if r]


# ── connection / OAuth ───────────────────────────────────────────────────────


@router.get("/status", response_model=GmailStatus)
async def gmail_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Connection summary. Purely local — never calls Google, so the sidebar
    and accounts page can poll it freely."""
    conn = await _owned_connection(db, current_user.email)
    if conn is None:
        return GmailStatus(connected=False, configured=settings.gmail_configured)

    expires_at = _aware(conn.expires_at)
    expired = expires_at is not None and expires_at <= _now() + EXPIRY_SKEW
    return GmailStatus(
        connected=True,
        configured=settings.gmail_configured,
        account_email=conn.account_email,
        scopes=[s for s in (conn.granted_scopes or "").split() if s],
        expires_at=expires_at,
        reconnect_required=bool(expired and not conn.encrypted_refresh_token),
        messages_total=_int_or_none(conn.messages_total),
        last_checked_at=_aware(conn.last_checked_at),
        updated_at=_aware(conn.updated_at),
    )


@router.get("/auth-url", response_model=GmailAuthUrlResponse)
async def gmail_auth_url(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Start Google OAuth. Returns the consent URL; the browser is sent there
    and comes back to /callback."""
    _configured_required()
    state = _mint_oauth_state(current_user.email)
    service = _service(request)
    try:
        auth_url = service.get_auth_url(state)
    except Exception as exc:
        logger.exception("Could not build the Gmail auth URL")
        raise HTTPException(status_code=502, detail=f"Could not start Google sign-in: {exc}")
    return GmailAuthUrlResponse(auth_url=auth_url)


@router.get("/callback", include_in_schema=False)
async def gmail_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Google redirect target. Unauthenticated by necessity — identity comes
    from the signed ``state`` minted by ``gmail_auth_url``. Always ends in a
    redirect to the app's Gmail page."""
    if error:
        return _frontend_redirect(error=error_description or error)

    try:
        state_payload = _read_oauth_state(state)
        owner_email = state_payload["sub"]
    except ValueError as exc:
        logger.warning("Rejected Gmail OAuth callback: %s", exc)
        return _frontend_redirect(error="Sign-in expired or was tampered with. Try again.")

    if not code:
        return _frontend_redirect(error="Google returned no authorization code")

    user = (
        await db.execute(select(User).where(User.email == owner_email))
    ).scalar_one_or_none()
    if user is None:
        return _frontend_redirect(error="Account not found")

    service = _service(request)
    try:
        tokens = await service.exchange_code(code)
        profile = await service.get_account_info(tokens.get("access_token") or "")
    except Exception as exc:
        logger.warning("Gmail OAuth for %s failed: %s", owner_email, exc)
        return _frontend_redirect(error=f"Google sign-in failed: {exc}")
    account_email = str(profile.get("emailAddress") or "").strip().lower()

    if not account_email or not tokens.get("access_token"):
        return _frontend_redirect(error="Google did not return a usable Gmail account")

    # One mailbox must not end up linked to two LinkEasy users — that would
    # give each a live window into the same inbox.
    clash = (
        await db.execute(
            select(GmailConnection).where(
                GmailConnection.account_email == account_email,
                GmailConnection.owner_email != owner_email,
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        return _frontend_redirect(
            error="That Gmail address is already connected to another LinkEasy account."
        )

    conn = await _owned_connection(db, owner_email)
    if conn is None:
        conn = GmailConnection(owner_email=owner_email, encrypted_access_token="")
        db.add(conn)
    try:
        apply_tokens(
            conn,
            access_token=tokens.get("access_token") or "",
            refresh_token=tokens.get("refresh_token"),
            expires_in=tokens.get("expires_in"),
            granted_scopes=SCOPES_JOINED,
        )
    except ValueError as exc:
        return _frontend_redirect(error=str(exc))
    conn.account_email = account_email
    conn.messages_total = str(profile.get("messagesTotal") or "")
    conn.threads_total = str(profile.get("threadsTotal") or "")
    conn.history_id = str(profile.get("historyId") or "")
    conn.last_checked_at = _now()

    try:
        await db.commit()
    except IntegrityError:
        # A parallel callback for the same user won the insert race. If the
        # surviving row is ours, treat the flow as connected; otherwise the
        # loser must retry (their row was rolled back).
        await db.rollback()
        winner = await _owned_connection(db, owner_email)
        if winner is not None and winner.account_email == account_email:
            return _frontend_redirect(connected=True)
        return _frontend_redirect(error="Sign-in raced with another attempt — try again.")

    logger.info("Gmail connected for %s (%s)", owner_email, account_email)
    return _frontend_redirect(connected=True)


@router.delete("/connection", response_model=GmailMessageResponse)
async def gmail_disconnect(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = await _owned_connection(db, current_user.email)
    if conn is None:
        raise HTTPException(status_code=404, detail="Gmail is not connected")

    # Best-effort revoke at Google so the refresh token dies server-side too.
    try:
        tokens = read_tokens(conn)
        service = GmailService()
        await service.revoke_token(tokens.access_token)
    except Exception:
        logger.info("Gmail disconnect without token revoke for %s", current_user.email)

    await db.delete(conn)
    await db.commit()
    logger.info("Gmail disconnected for %s", current_user.email)
    return GmailMessageResponse(message="Gmail disconnected")


# ── mailbox ──────────────────────────────────────────────────────────────────


@router.get("/profile", response_model=GmailProfileResponse)
async def gmail_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Live mailbox profile from users/me/profile (address + totals)."""
    conn = await _connection_or_409(db, current_user.email)
    access_token = await _access_token(db, conn)
    service = GmailService()
    try:
        profile = await service.get_account_info(access_token)
    except GmailApiError as exc:
        raise _map_gmail_error(exc) from exc

    conn.messages_total = str(profile.get("messagesTotal") or "")
    conn.threads_total = str(profile.get("threadsTotal") or "")
    conn.history_id = str(profile.get("historyId") or "")
    conn.account_email = str(profile.get("emailAddress") or conn.account_email).strip().lower()
    conn.last_checked_at = _now()
    await db.commit()

    return GmailProfileResponse(
        email_address=conn.account_email,
        messages_total=_int_or_none(conn.messages_total) or 0,
        threads_total=_int_or_none(conn.threads_total) or 0,
        history_id=conn.history_id,
        fetched_at=_now(),
    )


@router.get("/labels", response_model=list[GmailLabel])
async def gmail_labels(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every label with per-label message/unread totals.

    Counts come from one labels.get per label; a label that vanishes between
    the list and its get is omitted rather than failing the request.
    """
    conn = await _connection_or_409(db, current_user.email)
    access_token = await _access_token(db, conn)
    service = GmailService()
    try:
        labels = await service.list_labels(access_token)
    except GmailApiError as exc:
        raise _map_gmail_error(exc) from exc

    labels = labels[:60]  # bound the fan-out on pathological mailboxes
    semaphore = asyncio.Semaphore(6)

    async def with_counts(label: dict) -> Optional[dict]:
        async with semaphore:
            try:
                detail = await service.get_label(access_token, str(label.get("id") or ""))
            except GmailApiError as exc:
                if exc.category in ("not_found", "invalid"):
                    return None
                raise
        return {
            "id": str(label.get("id") or ""),
            "name": str(label.get("name") or ""),
            "type": str(label.get("type") or "user"),
            "messages_total": _int_or_none(detail.get("messagesTotal")),
            "messages_unread": _int_or_none(detail.get("messagesUnread")),
        }

    try:
        results = await asyncio.gather(*(with_counts(l) for l in labels))
    except GmailApiError as exc:
        raise _map_gmail_error(exc) from exc
    return [GmailLabel(**r) for r in results if r]


@router.get("/messages", response_model=GmailMessageListResponse)
async def gmail_list_messages(
    q: str = Query("", max_length=_QUERY_MAX),
    label_ids: str = Query("", description="Comma-separated label ids"),
    unread_only: bool = Query(False),
    max_results: int = Query(40, ge=1, le=100),
    page_token: str = Query(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Search / list messages (no bodies — click one for the full thread)."""
    conn = await _connection_or_409(db, current_user.email)
    access_token = await _access_token(db, conn)
    service = GmailService()

    # label_ids arrives comma-separated from the client; split here so each
    # piece is validated before being handed to Gmail.
    wanted_labels = []
    for piece in (label_ids.split(",") if label_ids else []):
        piece = piece.strip()
        if piece:
            if not _GM_ID_RE.match(piece):
                raise HTTPException(status_code=400, detail=f"Invalid label id '{piece}'")
            wanted_labels.append(piece)
    query = (q or "").strip()
    if unread_only:
        query = f"{query} is:unread" if query else "is:unread"

    try:
        data = await service.list_messages(
            access_token,
            q=query,
            label_ids=wanted_labels or None,
            max_results=max_results,
            page_token=(page_token or "").strip(),
        )
    except GmailApiError as exc:
        raise _map_gmail_error(exc) from exc

    ids = [str(m.get("id")) for m in (data.get("messages") or []) if m.get("id")]
    rows = await _fetch_summaries(service, access_token, ids)
    rows.sort(
        key=lambda r: (r.get("internal_date") is not None, r.get("internal_date") or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )

    conn.last_checked_at = _now()
    await db.commit()
    return GmailMessageListResponse(
        messages=[GmailMessageSummary(**r) for r in rows],
        next_page_token=data.get("nextPageToken") or "",
        result_size_estimate=int(data.get("resultSizeEstimate") or 0),
        label_id=",".join(wanted_labels),
        q=query,
    )


@router.get("/unread", response_model=GmailUnreadResponse)
async def gmail_unread(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate: None = Depends(rate_limit("gmail:check")),
):
    """Inbox totals + the newest unread messages — the "checking mail" tick
    the UI polls while the inbox is open."""
    conn = await _connection_or_409(db, current_user.email)
    access_token = await _access_token(db, conn)
    service = GmailService()
    try:
        inbox = await service.get_label(access_token, "INBOX")
        data = await service.list_messages(
            access_token, q="in:inbox is:unread", max_results=8
        )
    except GmailApiError as exc:
        raise _map_gmail_error(exc) from exc

    ids = [str(m.get("id")) for m in (data.get("messages") or []) if m.get("id")]
    rows = await _fetch_summaries(service, access_token, ids)
    rows.sort(
        key=lambda r: (r.get("internal_date") is not None, r.get("internal_date") or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )

    conn.last_checked_at = _now()
    await db.commit()
    return GmailUnreadResponse(
        unread_in_inbox=_int_or_none(inbox.get("messagesUnread")) or 0,
        inbox_total=_int_or_none(inbox.get("messagesTotal")) or 0,
        messages=[GmailMessageSummary(**r) for r in rows[:8]],
        checked_at=_now(),
    )


@router.get("/messages/{message_id}", response_model=GmailMessageDetail)
async def gmail_get_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = await _connection_or_409(db, current_user.email)
    access_token = await _access_token(db, conn)
    service = GmailService()
    message_id = _valid_gmail_id(message_id, "message id")
    try:
        raw = await service.get_message(access_token, message_id)
    except GmailApiError as exc:
        raise _map_gmail_error(exc) from exc
    return GmailMessageDetail(**message_details(raw))


@router.patch("/messages/{message_id}", response_model=GmailMessageSummary)
async def gmail_modify_message(
    message_id: str,
    payload: GmailModifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add/remove labels on one message — mark read/unread (UNREAD), star
    (STARRED), archive (remove INBOX), move to a custom label, etc."""
    conn = await _connection_or_409(db, current_user.email)
    access_token = await _access_token(db, conn)
    service = GmailService()
    message_id = _valid_gmail_id(message_id, "message id")
    add = [_valid_gmail_id(i, "label id") for i in payload.add_label_ids]
    remove = [_valid_gmail_id(i, "label id") for i in payload.remove_label_ids]
    if not add and not remove:
        raise HTTPException(status_code=400, detail="Nothing to change — send add_label_ids or remove_label_ids")
    try:
        await service.modify_message(access_token, message_id, add or None, remove or None)
        raw = await service.get_message_metadata(access_token, message_id)
    except GmailApiError as exc:
        raise _map_gmail_error(exc) from exc
    return GmailMessageSummary(**summarize_message(raw))


@router.post("/messages/{message_id}/trash", response_model=GmailMessageSummary)
async def gmail_trash_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = await _connection_or_409(db, current_user.email)
    access_token = await _access_token(db, conn)
    service = GmailService()
    message_id = _valid_gmail_id(message_id, "message id")
    try:
        await service.trash_message(access_token, message_id)
        raw = await service.get_message_metadata(access_token, message_id)
    except GmailApiError as exc:
        raise _map_gmail_error(exc) from exc
    return GmailMessageSummary(**summarize_message(raw))


@router.post("/messages/{message_id}/untrash", response_model=GmailMessageSummary)
async def gmail_untrash_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = await _connection_or_409(db, current_user.email)
    access_token = await _access_token(db, conn)
    service = GmailService()
    message_id = _valid_gmail_id(message_id, "message id")
    try:
        await service.untrash_message(access_token, message_id)
        raw = await service.get_message_metadata(access_token, message_id)
    except GmailApiError as exc:
        raise _map_gmail_error(exc) from exc
    return GmailMessageSummary(**summarize_message(raw))


@router.get("/messages/{message_id}/attachments/{attachment_id}")
async def gmail_download_attachment(
    message_id: str,
    attachment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Proxy an attachment out of Gmail so the browser never needs the OAuth
    token. The filename/mime come from the message's own attachment list."""
    conn = await _connection_or_409(db, current_user.email)
    access_token = await _access_token(db, conn)
    service = GmailService()
    message_id = _valid_gmail_id(message_id, "message id")
    attachment_id = _valid_gmail_id(attachment_id, "attachment id")
    try:
        raw = await service.get_message(access_token, message_id)
        meta = message_details(raw)
        found = next(
            (a for a in meta["attachments"] if a["attachment_id"] == attachment_id), None
        )
        if found is None:
            raise HTTPException(status_code=404, detail="Attachment not found on this message")
        content = await service.get_attachment(access_token, message_id, attachment_id)
    except GmailApiError as exc:
        raise _map_gmail_error(exc) from exc
    except HTTPException:
        raise

    filename = (found["filename"] or "attachment").replace("\\", "_").replace('"', "_")
    # RFC 5987 filename* for the real name + a plain ASCII fallback.
    disposition = f"attachment; filename=\"{filename[:80]}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content,
        media_type=found["mime_type"] or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


@router.get("/threads/{thread_id}", response_model=GmailThreadResponse)
async def gmail_get_thread(
    thread_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Full thread for the reading pane: every message, oldest → newest."""
    conn = await _connection_or_409(db, current_user.email)
    access_token = await _access_token(db, conn)
    service = GmailService()
    thread_id = _valid_gmail_id(thread_id, "thread id")
    try:
        thread = await service.get_thread(access_token, thread_id)
        ids = [str(m.get("id")) for m in (thread.get("messages") or []) if m.get("id")]
        # Bound the payload on monster threads; the newest 50 still tell the
        # whole recent story and keep the reading pane snappy.
        ids = ids[-50:]
    except GmailApiError as exc:
        raise _map_gmail_error(exc) from exc

    semaphore = asyncio.Semaphore(6)

    async def one(message_id: str) -> Optional[dict]:
        async with semaphore:
            try:
                raw = await service.get_message(access_token, message_id)
            except GmailApiError as exc:
                if exc.category in ("not_found", "invalid"):
                    return None
                raise
        return message_details(raw)

    try:
        details = await asyncio.gather(*(one(i) for i in ids))
    except GmailApiError as exc:
        raise _map_gmail_error(exc) from exc
    details = [d for d in details if d]
    details.sort(
        key=lambda d: (d.get("internal_date") is not None, d.get("internal_date") or datetime.min.replace(tzinfo=timezone.utc))
    )
    return GmailThreadResponse(
        id=thread_id,
        messages=[GmailMessageDetail(**d) for d in details],
    )


# ── send ─────────────────────────────────────────────────────────────────────


@router.post(
    "/send",
    response_model=GmailSendResponse,
    dependencies=[Depends(rate_limit("gmail:send"))],
)
async def gmail_send(
    payload: GmailSendRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Compose and send. From is always the connected mailbox — Gmail refuses
    to send from another address, so no From is accepted from the client."""
    conn = await _connection_or_409(db, current_user.email)
    if not conn.account_email:
        raise HTTPException(
            status_code=409,
            detail="The connected mailbox address is unknown — reconnect Gmail.",
        )
    access_token = await _access_token(db, conn)

    def _parse_recipients(header_value: str) -> list[str]:
        addresses: list[str] = []
        for _name, email_addr in getaddresses([header_value or ""]):
            email_addr = email_addr.strip().strip("<>").strip()
            if not email_addr:
                continue
            addresses.append(email_addr)
        return addresses

    to_addresses = _parse_recipients(payload.to)
    if not to_addresses:
        raise HTTPException(status_code=400, detail="Add at least one recipient")
    cc_addresses = _parse_recipients(payload.cc)
    bcc_addresses = _parse_recipients(payload.bcc)

    try:
        from email_validator import EmailNotValidError, validate_email
        for label, addresses in (
            ("recipient", to_addresses),
            ("cc", cc_addresses),
            ("bcc", bcc_addresses),
        ):
            for address in addresses:
                try:
                    validate_email(address, check_deliverability=False)
                except EmailNotValidError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid {label} address '{address}': {exc}",
                    ) from exc
    except ImportError:  # pragma: no cover — email-validator is a hard dep
        pass

    service = GmailService()
    raw_payload = raw_message_json(
        from_email=conn.account_email,
        to=to_addresses,
        subject=payload.subject.strip(),
        body=payload.body,
        cc=cc_addresses or None,
        bcc=bcc_addresses or None,
        in_reply_to=(payload.in_reply_to or "").strip() or None,
        references=(payload.references or "").strip() or None,
    )
    try:
        sent = await service.send_message(access_token, raw_payload)
    except GmailApiError as exc:
        raise _map_gmail_error(exc) from exc

    conn.last_checked_at = _now()
    await db.commit()
    logger.info(
        "Gmail message sent by %s → %s (%s)",
        current_user.email,
        ", ".join(to_addresses),
        sent.get("id"),
    )
    return GmailSendResponse(
        id=sent.get("id") or "",
        thread_id=sent.get("threadId") or "",
        to=", ".join(to_addresses),
        subject=payload.subject.strip(),
    )


__all__ = ["router"]
