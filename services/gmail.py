"""
Gmail integration — OAuth + Gmail API client.
FILE: services/gmail.py

One service class over the Gmail REST API (plus Google's OAuth endpoints).
Deliberately small: the API surface LinkEasy needs is plain JSON over HTTPS —
messages, threads, labels, send — so this speaks to ``gmail.googleapis.com``
directly with httpx instead of pulling in the full google-api client.

Scope policy (see docs/gmail_setup.md for the reasoning):

  * ``gmail.modify`` — read/search messages + threads and manage labels,
    read-state, trash and archive (everything except permanent deletion);
  * ``gmail.send``   — compose and send messages.

``mail.google.com`` (full access, a *restricted* scope) is never requested.

Tokens are exchanged/refreshed at Google's OAuth endpoints and handed back to
the caller as plain strings; the API layer encrypts them (AES-256-GCM) before
they touch the database, following services/social/connections.py.
"""
from __future__ import annotations

import base64
import email.utils
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.message import EmailMessage
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from core.config import settings
from core.security import decrypt_credential, encrypt_credential

logger = logging.getLogger(__name__)

# ── Scopes ────────────────────────────────────────────────────────────────────
# gmail.modify covers read/search plus label/modify/trash operations;
# sending needs gmail.send on top (modify does NOT include send). See
# https://developers.google.com/gmail/api/auth/scopes
GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
)
SCOPES_JOINED = " ".join(GMAIL_SCOPES)

# Google OAuth endpoints.
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

# A token this close to expiry is treated as expired so a slow operation
# cannot outlive it midway through (same skew as services/social/connections).
EXPIRY_SKEW = timedelta(minutes=5)
DEFAULT_TIMEOUT = 30.0
SEND_TIMEOUT = 60.0


class GmailApiError(Exception):
    """A Gmail/Google API failure with a machine-readable category.

    ``category`` is one of:
      * "auth"       — token missing/expired/revoked or scope insufficient;
                      the user must reconnect.
      * "quota"      — Google rate-limited the request; retry later.
      * "not_found"  — message/thread/label/attachment does not exist
                      (or was deleted in another client).
      * "invalid"    — Google rejected the request itself (bad query, ...).
      * "upstream"   — anything else (network, 5xx); try again in a moment.
    """

    def __init__(self, message: str, category: str = "upstream"):
        super().__init__(message)
        self.category = category


@dataclass
class GmailTokens:
    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[datetime]

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return _aware(self.expires_at) <= datetime.now(timezone.utc) + EXPIRY_SKEW


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ── Stored-token helpers (encrypt/decrypt contract in exactly one place) ─────

def apply_tokens(
    connection,
    *,
    access_token: str,
    refresh_token: Optional[str] = None,
    expires_in: Any = None,
    granted_scopes: str = "",
) -> None:
    """Encrypt and store a fresh token set on ``connection`` (not committed)."""
    if not access_token:
        raise ValueError("Google returned no access token")
    connection.encrypted_access_token = encrypt_credential(access_token)
    if refresh_token:
        connection.encrypted_refresh_token = encrypt_credential(refresh_token)
    # A renewal that omits the refresh token keeps the one already stored.
    if expires_in is not None:
        try:
            seconds = int(expires_in)
            if seconds > 0:
                connection.expires_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        except (TypeError, ValueError):
            pass
    if granted_scopes:
        connection.granted_scopes = granted_scopes


def read_tokens(connection) -> GmailTokens:
    """Decrypt stored tokens. Raises ValueError on corrupt/foreign ciphertext."""
    try:
        access_token = decrypt_credential(connection.encrypted_access_token)
        refresh_token = (
            decrypt_credential(connection.encrypted_refresh_token)
            if connection.encrypted_refresh_token
            else None
        )
    except Exception as exc:
        raise ValueError(
            "Stored Gmail credentials cannot be decrypted "
            "(was CREDENTIAL_ENCRYPTION_KEY rotated?). Reconnect Gmail."
        ) from exc
    return GmailTokens(access_token, refresh_token, connection.expires_at)


# ── MIME / payload helpers (pure, unit-testable) ─────────────────────────────

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def decode_base64url(data: str) -> str:
    """Decode Gmail's base64url ``body.data`` to text ('' for anything odd)."""
    if not data:
        return ""
    try:
        if not _B64URL_RE.match(data):
            return ""
        padded = data + "=" * (-len(data) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _decode_header_value(value: str) -> str:
    """Decode RFC 2047 encoded-words (``=?utf-8?Q?..?=``) from a header."""
    if not value:
        return ""
    try:
        parts = decode_header(value)
        return "".join(
            part.decode(charset or "utf-8", errors="replace") if isinstance(part, bytes) else part
            for part, charset in parts
        )
    except Exception:
        return value


def _header_map(message: dict) -> dict[str, str]:
    payload = message.get("payload") or {}
    headers = payload.get("headers") or []
    out: dict[str, str] = {}
    for header in headers:
        name = str(header.get("name", "")).lower()
        value = str(header.get("value", ""))
        if name and name not in out:  # first occurrence wins (RFC 5322)
            out[name] = value
    return out


def _decoded_header(headers: dict[str, str], name: str) -> str:
    return _decode_header_value(headers.get(name, "")).strip()


def _address_list(headers: dict[str, str], name: str) -> list[dict[str, str]]:
    """Parse a From/To/Cc/Bcc header into [{name, email}] preserving order."""
    value = headers.get(name, "")
    if not value:
        return []
    try:
        pairs = email.utils.getaddresses([value])
    except Exception:
        pairs = []
    result = []
    for raw_name, raw_addr in pairs:
        email_addr = raw_addr.strip().strip("<>").strip().lower()
        if not email_addr:
            continue
        result.append(
            {
                "name": _decode_header_value(raw_name.strip()),
                "email": email_addr,
            }
        )
    return result


def _collect_parts(payload: dict | None) -> tuple[list[str], list[str], list[dict]]:
    """Walk a MIME payload: (plain bodies, html bodies, named attachments).

    Bodies are collected across nested ``parts`` (multipart/alternative,
    multipart/related, ...) and concatenated with blank lines.
    """
    plain: list[str] = []
    html_bodies: list[str] = []
    attachments: list[dict] = []

    def walk(part: dict) -> None:
        mime = (part.get("mimeType") or "").lower()
        body = part.get("body") or {}
        data = body.get("data") or ""
        filename = (part.get("filename") or "").strip()
        attachment_id = body.get("attachmentId")

        if attachment_id and filename:
            attachments.append(
                {
                    "attachment_id": attachment_id,
                    "filename": filename,
                    "mime_type": part.get("mimeType") or "application/octet-stream",
                    "size": int(body.get("size") or 0),
                }
            )
            return  # an attachment carries no display body of its own

        if mime == "text/plain" and data:
            plain.append(decode_base64url(data))
            return
        if mime == "text/html" and data:
            html_bodies.append(decode_base64url(data))
            return
        for sub in part.get("parts") or []:
            walk(sub)

    if payload:
        walk(payload)
    return "\n".join(plain), "\n".join(html_bodies), attachments


def summarize_message(message: dict) -> dict:
    """Compact row model for list views (no bodies)."""
    headers = _header_map(message)
    froms = _address_list(headers, "from")
    label_ids = list(message.get("labelIds") or [])
    internal_ms = message.get("internalDate")
    try:
        internal_ms = int(internal_ms or 0)
    except (TypeError, ValueError):
        internal_ms = 0
    return {
        "id": message.get("id", ""),
        "thread_id": message.get("threadId", ""),
        "label_ids": label_ids,
        "snippet": message.get("snippet", "") or "",
        "subject": _decoded_header(headers, "subject") or "(no subject)",
        "from_name": (froms[0]["name"] if froms and froms[0]["name"] else (froms[0]["email"] if froms else "")),
        "from_email": (froms[0]["email"] if froms else ""),
        "date": headers.get("date", ""),
        "internal_date": datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc)
        if internal_ms
        else None,
        "is_read": "UNREAD" not in label_ids,
        "is_starred": "STARRED" in label_ids,
    }


def message_details(message: dict) -> dict:
    """Full message model for the reading pane."""
    headers = _header_map(message)
    plain_text, html_body, attachments = _collect_parts(message.get("payload"))
    summary = summarize_message(message)
    summary.update(
        {
            "to": _address_list(headers, "to"),
            "cc": _address_list(headers, "cc"),
            "bcc": _address_list(headers, "bcc"),
            "reply_to": _address_list(headers, "reply-to"),
            "message_id_header": headers.get("message-id", ""),
            "text_body": plain_text,
            "html_body": html_body,
            "attachments": attachments,
            "size_estimate": int(message.get("sizeEstimate") or 0),
        }
    )
    return summary


def raw_message_json(
    from_email: str,
    to: list[str],
    subject: str,
    body: str,
    cc: Optional[list[str]] = None,
    bcc: Optional[list[str]] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> dict:
    """Build the base64url ``raw`` payload for Gmail's messages.send.

    Produces a multipart/alternative message: the user's plain text plus a
    minimal, safely HTML-escaped rendition (line breaks preserved) so clients
    that prefer HTML still render it — no user content ever lands unescaped.
    """
    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    # Bcc recipients must never appear in the delivered message (RFC 5322);
    # the header is only used to address the envelope, so drop it before the
    # bytes are serialized.
    del msg["Bcc"]

    msg.set_content(body)
    html_body = "<html><body style='font-family:Arial,Helvetica,sans-serif;font-size:14px;'>"
    html_body += "".join(
        "<div>%s</div>" % html.escape(line) if line else "<div><br></div>"
        for line in body.replace("\r\n", "\n").split("\n")
    )
    html_body += "</body></html>"
    msg.add_alternative(html_body, subtype="html")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return {"raw": raw}


# ── OAuth + Gmail API client ─────────────────────────────────────────────────

class GmailService:
    """Stateless per-request client; credentials come from core.config."""

    def __init__(self) -> None:
        self.client_id = settings.GOOGLE_CLIENT_ID
        self.client_secret = settings.GOOGLE_CLIENT_SECRET
        self.redirect_uri = settings.GOOGLE_REDIRECT_URI

    # ── OAuth ────────────────────────────────────────────────────────────────

    def get_auth_url(self, state: str) -> str:
        """Google consent URL. ``state`` is the caller's signed CSRF token."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": SCOPES_JOINED,
            # access_type=offline + prompt=consent guarantee a refresh token on
            # every connect — even for an account that has approved the app
            # before (Google otherwise silently omits it on re-consent).
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict[str, Any]:
        """Swap the authorization code for tokens.

        Returns {"access_token", "refresh_token"?, "expires_in"} — the shape
        used by the social-scheduler callbacks too. A missing refresh token is
        possible only if the user revoked the app mid-flow; the caller stores
        whatever came back and keeps any previous refresh token.
        """
        data = await self._token_post(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
            },
            prefix="Google token exchange failed",
        )
        return {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "expires_in": data.get("expires_in"),
        }

    async def refresh_access_token(
        self,
        refresh_token: Optional[str],
        current_access_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """Refresh an expired access token (uniform service-interface name)."""
        if not refresh_token:
            raise GmailApiError(
                "Google did not issue a refresh token for this mailbox. "
                "Disconnect Gmail and connect it again.",
                category="auth",
            )
        data = await self._token_post(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            prefix="Google token refresh failed",
        )
        return {
            "access_token": data.get("access_token"),
            # Google does not return a new refresh token on refresh.
            "refresh_token": None,
            "expires_in": data.get("expires_in"),
        }

    async def revoke_token(self, access_token: Optional[str]) -> None:
        """Best-effort Google token revocation (disconnect). Never raises."""
        if not access_token:
            return
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                await client.post(
                    GOOGLE_REVOKE_URL,
                    data={"token": access_token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning("Google token revocation failed: %s", exc)

    async def _token_post(self, params: dict, prefix: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(
                    GOOGLE_TOKEN_URL,
                    data=params,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            raise GmailApiError(f"{prefix}: {exc}") from exc
        try:
            data = response.json()
        except json.JSONDecodeError:
            raise GmailApiError(f"{prefix}: HTTP {response.status_code}") from None
        if response.status_code >= 400 or data.get("error"):
            error = data.get("error") or f"HTTP {response.status_code}"
            description = data.get("error_description") or data.get("message") or ""
            raise GmailApiError(
                f"{prefix}: {error}{f' — {description}' if description else ''}",
                category="auth",
            )
        return data

    # ── Gmail API ────────────────────────────────────────────────────────────

    async def get_account_info(self, access_token: str) -> dict:
        """users/me/profile — the connected mailbox's address and totals."""
        return await self._api("GET", "/users/me/profile", access_token)

    async def list_labels(self, access_token: str) -> list[dict]:
        data = await self._api("GET", "/users/me/labels", access_token)
        return data.get("labels") or []

    async def get_label(self, access_token: str, label_id: str) -> dict:
        return await self._api(
            "GET", f"/users/me/labels/{_escape_path(label_id)}", access_token
        )

    async def list_messages(
        self,
        access_token: str,
        *,
        q: str = "",
        label_ids: Optional[list[str]] = None,
        max_results: int = 50,
        page_token: str = "",
    ) -> dict:
        params: dict[str, Any] = {"maxResults": max(1, min(max_results, 100))}
        if q:
            params["q"] = q
        if label_ids:
            params["labelIds"] = ",".join(label_ids)
        if page_token:
            params["pageToken"] = page_token
        return await self._api("GET", "/users/me/messages", access_token, params=params)

    async def get_message(self, access_token: str, message_id: str) -> dict:
        return await self._api(
            "GET", f"/users/me/messages/{_escape_path(message_id)}?format=full", access_token
        )

    async def get_message_metadata(
        self, access_token: str, message_id: str
    ) -> dict:
        return await self._api(
            "GET",
            f"/users/me/messages/{_escape_path(message_id)}"
            "?format=metadata&metadataHeaders=From&metadataHeaders=To"
            "&metadataHeaders=Subject&metadataHeaders=Date",
            access_token,
        )

    async def get_thread(self, access_token: str, thread_id: str) -> dict:
        return await self._api(
            "GET", f"/users/me/threads/{_escape_path(thread_id)}", access_token
        )

    async def modify_message(
        self,
        access_token: str,
        message_id: str,
        add_label_ids: Optional[list[str]] = None,
        remove_label_ids: Optional[list[str]] = None,
    ) -> dict:
        body: dict[str, Any] = {}
        if add_label_ids:
            body["addLabelIds"] = add_label_ids
        if remove_label_ids:
            body["removeLabelIds"] = remove_label_ids
        return await self._api(
            "POST",
            f"/users/me/messages/{_escape_path(message_id)}/modify",
            access_token,
            json_body=body,
        )

    async def trash_message(self, access_token: str, message_id: str) -> dict:
        return await self._api(
            "POST",
            f"/users/me/messages/{_escape_path(message_id)}/trash",
            access_token,
            json_body={},
        )

    async def untrash_message(self, access_token: str, message_id: str) -> dict:
        return await self._api(
            "POST",
            f"/users/me/messages/{_escape_path(message_id)}/untrash",
            access_token,
            json_body={},
        )

    async def send_message(self, access_token: str, raw_payload: dict) -> dict:
        return await self._api(
            "POST",
            "/users/me/messages/send",
            access_token,
            json_body=raw_payload,
            timeout=SEND_TIMEOUT,
        )

    async def get_attachment(
        self, access_token: str, message_id: str, attachment_id: str
    ) -> bytes:
        url = (
            f"/users/me/messages/{_escape_path(message_id)}/attachments/"
            f"{_escape_path(attachment_id)}"
        )
        data = await self._api("GET", url, access_token)
        try:
            return base64.urlsafe_b64decode(
                (data.get("data") or "") + "==="
            )
        except Exception as exc:
            raise GmailApiError("Could not decode the attachment data") from exc

    async def _api(
        self,
        method: str,
        path: str,
        access_token: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        url = f"{GMAIL_API_BASE}{path}"
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=json_body if json_body is not None else None,
                )
        except httpx.HTTPError as exc:
            raise GmailApiError(f"Gmail API request failed: {exc}", category="upstream") from exc

        if response.status_code == 204:
            return {}
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = {}
        if response.status_code >= 400:
            raise self._error_from(response.status_code, data, path)
        if not isinstance(data, dict):
            raise GmailApiError("Gmail API returned an unexpected response", category="upstream")
        return data

    @staticmethod
    def _error_from(status_code: int, data: Any, path: str) -> GmailApiError:
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or "")
            reason = ""
            for err in error.get("errors") or []:
                reason = str(err.get("reason") or "")
                break
            status = str(error.get("status") or "")
        else:
            message = str(error or "") if error else ""
            reason = ""
            status = ""

        _auth_reasons = (
            "authError",
            "insufficientPermissions",
            "scopeOAuth2AccessTokenExpired",
            "accessNotConfigured",
            "tokenExpired",
        )
        _quota_reasons = ("rateLimitExceeded", "userRateLimitExceeded")
        if status_code == 404:
            return GmailApiError("That message no longer exists (deleted or moved).", category="not_found")
        if status_code == 429 or reason in _quota_reasons:
            return GmailApiError(
                "Gmail is rate-limiting requests right now. Wait a moment and try again.",
                category="quota",
            )
        # 401 is always the token; 403 needs its reason checked first because
        # Google also reports quota-style denials as 403 with a reason.
        if status_code == 401 or (status_code == 403 and reason in _auth_reasons) or reason in _auth_reasons:
            return GmailApiError(
                "Gmail access was revoked or has expired. Reconnect Gmail.", category="auth"
            )
        if 400 <= status_code < 500:
            return GmailApiError(
                f"Gmail rejected the request{': ' + message if message else ''} ({status})",
                category="invalid",
            )
        return GmailApiError(
            f"Gmail API error (HTTP {status_code}){': ' + message if message else ''}",
            category="upstream",
        )


def _escape_path(value: str) -> str:
    """Gmail ids are server-generated base64url-ish strings; allow only safe
    characters so a malformed id can never inject path segments."""
    if not value or not re.fullmatch(r"[A-Za-z0-9_\-]+", value):
        raise GmailApiError("Invalid Gmail id", category="invalid")
    return value


__all__ = [
    "EXPIRY_SKEW",
    "GMAIL_API_BASE",
    "GMAIL_SCOPES",
    "GOOGLE_AUTH_URL",
    "GOOGLE_REVOKE_URL",
    "GOOGLE_TOKEN_URL",
    "GmailApiError",
    "GmailService",
    "SCOPES_JOINED",
    "apply_tokens",
    "decode_base64url",
    "message_details",
    "raw_message_json",
    "read_tokens",
    "summarize_message",
]
