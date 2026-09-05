"""Gmail API: OAuth flow, per-user scoping, encryption, endpoints.

These tests run the real ``api/v1/gmail.py`` router against an in-memory
database with the network-calling parts of ``GmailService`` mocked away, so
they pin the app's own contract: who may see what, what is stored encrypted,
which Google calls each route makes, and how Google errors surface.

Highlights:

  * the OAuth callback accepts only the signed state minted for that user and
    stores tokens AES-encrypted (never plaintext);
  * one mailbox cannot be linked to two LinkEasy users;
  * message list / thread / labels fan-outs hit Gmail once per row and skip
    rows that vanish mid-fetch;
  * send validates recipients, builds the MIME message from the connected
    account, and stores nothing but the token state;
  * GmailApiError categories map to HTTP 409 (reconnect) / 429 / 404 / 400 /
    502 so the UI can act on them.
"""
import asyncio
import base64
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost:5173")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test-secret")

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from api.dependencies import get_current_user, get_db  # noqa: E402
from api.v1.gmail import _mint_oauth_state, router  # noqa: E402
from core.config import settings  # noqa: E402
from core.security import decrypt_credential, encrypt_credential  # noqa: E402
from database import Base  # noqa: E402
import models  # noqa: E402,F401
from models.gmail import GmailConnection  # noqa: E402
from models.user import User  # noqa: E402
from services.gmail import (  # noqa: E402
    GmailApiError,
    GmailService,
    SCOPES_JOINED,
    decode_base64url,
)

settings.CREDENTIAL_ENCRYPTION_KEY = "a" * 64
settings.JWT_SECRET = "test-secret"

OWNER = "owner@test.dev"
OTHER = "other@test.dev"
GOOGLE_ACCOUNT = "person@gmail.com"


def _user(email):
    return User(first_name="T", last_name="U", email=email, hashed_password="x", is_verified=True, role="customer")


def _enc(value: str) -> str:
    return encrypt_credential(value)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _redirect_error(location: str) -> str:
    return parse_qs(urlparse(location).query).get("error", [""])[0]


def _meta_message(mid, subject="Hello", frm="Alice <alice@x.com>", snippet="hi",
                  labels=("INBOX",), internal_ms="1725000000000"):
    return {
        "id": mid,
        "threadId": f"thread-{mid}",
        "labelIds": list(labels),
        "snippet": snippet,
        "internalDate": internal_ms,
        "payload": {
            "headers": [
                {"name": "From", "value": frm},
                {"name": "To", "value": "me@gmail.com"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Tue, 3 Sep 2026 10:00:00 +0000"},
            ]
        },
    }


def _full_message(mid, labels=("INBOX", "UNREAD")):
    msg = _meta_message(mid)
    msg["sizeEstimate"] = 900
    msg["payload"] = {
        "mimeType": "multipart/alternative",
        "headers": msg["payload"]["headers"] + [{"name": "Message-ID", "value": f"<{mid}@x>"}],
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64(f"Body of {mid}")}},
            {"mimeType": "text/html", "body": {"data": _b64(f"<p>Body of {mid}</p>")}},
            {
                "mimeType": "application/pdf",
                "filename": "report.pdf",
                "body": {"attachmentId": f"ATT-{mid}", "size": 1200},
            },
        ],
    }
    msg["labelIds"] = list(labels)
    return msg


def _meta_async(side_effect_fn):
    """An AsyncMock whose side_effect is a plain sync function of the
    service-call arguments (the router awaits the mock, so returning a value
    is enough)."""
    return AsyncMock(side_effect=side_effect_fn)


class GmailApiTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self._settings_patch = patch.multiple(
            settings,
            GOOGLE_CLIENT_ID="client-1",
            GOOGLE_CLIENT_SECRET="secret-1",
            GOOGLE_REDIRECT_URI="",
            PUBLIC_API_URL="https://api.example.com",
            GOOGLE_OAUTH_RETURN_URL="http://localhost:5173/app/gmail",
        )
        self._settings_patch.start()

        app = FastAPI()
        app.include_router(router)
        self.current_email = OWNER

        async def override_get_db():
            async with self.Session() as session:
                yield session

        async def override_user():
            async with self.Session() as session:
                return (
                    await session.execute(select(User).where(User.email == self.current_email))
                ).scalar_one()

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_user
        self.app = app
        self.loop.run_until_complete(self._seed())

    def tearDown(self):
        self._settings_patch.stop()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    async def _seed(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.Session() as s:
            s.add_all([_user(OWNER), _user(OTHER)])
            await s.commit()

    def run_async(self, fn):
        async def runner():
            async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
                return await fn(client)

        return self.loop.run_until_complete(runner())

    def run_coro(self, coro):
        return self.loop.run_until_complete(coro)

    async def _connect(self, client, account=GOOGLE_ACCOUNT):
        """Drive the full callback flow for the current user."""
        state = _mint_oauth_state(self.current_email)
        with patch.object(
            GmailService,
            "exchange_code",
            AsyncMock(return_value={"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 3600}),
        ), patch.object(
            GmailService,
            "get_account_info",
            AsyncMock(
                return_value={
                    "emailAddress": account,
                    "messagesTotal": "1234",
                    "threadsTotal": "99",
                    "historyId": "777",
                }
            ),
        ):
            return await client.get(
                "/api/v1/gmail/callback", params={"code": "code-1", "state": state}
            )

    async def _store_connection(self, email=OWNER, account=GOOGLE_ACCOUNT):
        async with self.Session() as s:
            conn = GmailConnection(
                owner_email=email,
                account_email=account,
                encrypted_access_token=_enc("at-1"),
                encrypted_refresh_token=_enc("rt-1"),
                granted_scopes=SCOPES_JOINED,
                messages_total="1234",
                threads_total="99",
                history_id="777",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            s.add(conn)
            await s.commit()


# ── status / config ──────────────────────────────────────────────────────────

class StatusTests(GmailApiTests):
    def test_status_when_unconfigured_and_disconnected(self):
        with patch.object(settings, "GOOGLE_CLIENT_ID", ""), patch.object(settings, "GOOGLE_CLIENT_SECRET", ""):
            res = self.run_async(lambda c: c.get("/api/v1/gmail/status"))
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["connected"])
        self.assertFalse(body["configured"])

    def test_status_connected_reports_account_and_scopes(self):
        async def run(client):
            await self._store_connection()
            return await client.get("/api/v1/gmail/status")

        body = self.run_async(run).json()
        self.assertTrue(body["connected"])
        self.assertTrue(body["configured"])
        self.assertEqual(body["account_email"], GOOGLE_ACCOUNT)
        self.assertIn("https://www.googleapis.com/auth/gmail.modify", body["scopes"])
        self.assertEqual(body["messages_total"], 1234)
        self.assertFalse(body["reconnect_required"])

    def test_status_with_expired_token_and_no_refresh_needs_reconnect(self):
        async def run(client):
            async with self.Session() as s:
                conn = GmailConnection(
                    owner_email=OWNER,
                    account_email=GOOGLE_ACCOUNT,
                    encrypted_access_token=_enc("at"),
                    encrypted_refresh_token=None,
                    granted_scopes=SCOPES_JOINED,
                    expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                )
                s.add(conn)
                await s.commit()
            return await client.get("/api/v1/gmail/status")

        body = self.run_async(run).json()
        self.assertTrue(body["connected"])
        self.assertTrue(body["reconnect_required"])


# ── OAuth ────────────────────────────────────────────────────────────────────

class OAuthTests(GmailApiTests):
    def test_auth_url_requires_credentials(self):
        with patch.object(settings, "GOOGLE_CLIENT_ID", ""):
            res = self.run_async(lambda c: c.get("/api/v1/gmail/auth-url"))
        self.assertEqual(res.status_code, 503)
        self.assertIn("GOOGLE_CLIENT_ID", res.json()["detail"])

    def test_auth_url_builds_google_consent_url(self):
        res = self.run_async(lambda c: c.get("/api/v1/gmail/auth-url"))
        self.assertEqual(res.status_code, 200)
        url = res.json()["auth_url"]
        self.assertTrue(url.startswith("https://accounts.google.com/o/oauth2/v2/auth"))
        self.assertIn("client_id=client-1", url)
        # Derived redirect URI (no env override) — must match the route docs.
        self.assertIn("redirect_uri=https%3A%2F%2Fapi.example.com%2Fapi%2Fv1%2Fgmail%2Fcallback", url)
        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)
        # Never full access; both scopes requested (query values are
        # URL-encoded by urlencode, so decode before comparing).
        self.assertNotIn("mail.google.com", url)
        scopes = parse_qs(urlparse(url).query)["scope"][0]
        self.assertIn("https://www.googleapis.com/auth/gmail.modify", scopes)
        self.assertIn("https://www.googleapis.com/auth/gmail.send", scopes)

    def test_callback_stores_encrypted_tokens_and_profile(self):
        async def run(client):
            res = await self._connect(client)
            assert res.status_code == 302
            assert "connected=1" in res.headers["location"]
            async with self.Session() as s:
                return (await s.execute(select(GmailConnection))).scalars().all()

        rows = self.run_async(run)
        self.assertEqual(len(rows), 1)
        conn = rows[0]
        self.assertEqual(conn.account_email, GOOGLE_ACCOUNT)
        # Ciphertext format "<nonce_hex>:<cipher_hex>" — never plaintext.
        self.assertNotIn("at-new", conn.encrypted_access_token)
        self.assertNotIn("rt-new", conn.encrypted_refresh_token or "")
        self.assertEqual(decrypt_credential(conn.encrypted_access_token), "at-new")
        self.assertEqual(decrypt_credential(conn.encrypted_refresh_token), "rt-new")
        self.assertEqual(conn.granted_scopes, SCOPES_JOINED)
        self.assertEqual(conn.messages_total, "1234")

    def test_callback_rejects_tampered_state(self):
        res = self.run_async(
            lambda c: c.get("/api/v1/gmail/callback", params={"code": "c", "state": "not-a-jwt"})
        )
        self.assertEqual(res.status_code, 302)
        self.assertIn("tampered", _redirect_error(res.headers["location"]))

    def test_callback_rejects_google_error(self):
        res = self.run_async(
            lambda c: c.get(
                "/api/v1/gmail/callback",
                params={"code": "c", "state": _mint_oauth_state(OWNER), "error": "access_denied", "error_description": "Nope"},
            )
        )
        self.assertEqual(res.status_code, 302)
        self.assertIn("Nope", _redirect_error(res.headers["location"]))

    def test_same_mailbox_cannot_link_two_users(self):
        async def run(client):
            await self._connect(client)  # OWNER connects person@gmail.com
            self.current_email = OTHER
            res = await self._connect(client)  # OTHER tries the same mailbox
            self.current_email = OWNER
            return res

        res = self.run_async(run)
        self.assertEqual(res.status_code, 302)
        self.assertIn("already connected", _redirect_error(res.headers["location"]))

        async def count(_client=None):
            async with self.Session() as s:
                return len((await s.execute(select(GmailConnection))).scalars().all())

        self.assertEqual(self.run_async(count), 1)

    def test_disconnect_revokes_and_removes(self):
        async def run(client):
            await self._store_connection()
            revoke = AsyncMock(return_value=None)
            with patch.object(GmailService, "revoke_token", revoke):
                res = await client.delete("/api/v1/gmail/connection")
            assert res.status_code == 200
            async with self.Session() as s:
                rows = (await s.execute(select(GmailConnection))).scalars().all()
            return res, rows, revoke

        res, rows, revoke = self.run_async(run)
        self.assertEqual(res.json()["message"], "Gmail disconnected")
        self.assertEqual(len(rows), 0)
        # Revoked with the *decrypted* token.
        self.assertEqual(revoke.await_args.args[0], "at-1")

    def test_other_user_cannot_disconnect_my_connection(self):
        async def run(client):
            await self._store_connection()
            self.current_email = OTHER
            return await client.delete("/api/v1/gmail/connection")

        res = self.run_async(run)
        self.assertEqual(res.status_code, 404)  # no existence oracle


# ── mailbox routes ───────────────────────────────────────────────────────────

class MailboxTests(GmailApiTests):
    def test_profile_refreshes_totals(self):
        async def run(client):
            await self._store_connection()
            with patch.object(
                GmailService,
                "get_account_info",
                AsyncMock(
                    return_value={
                        "emailAddress": GOOGLE_ACCOUNT,
                        "messagesTotal": "42",
                        "threadsTotal": "7",
                        "historyId": "h1",
                    }
                ),
            ):
                return await client.get("/api/v1/gmail/profile")

        body = self.run_async(run).json()
        self.assertEqual(body["messages_total"], 42)
        self.assertEqual(body["email_address"], GOOGLE_ACCOUNT)

    def test_messages_list_fetches_summaries_sorted_newest_first(self):
        async def run(client):
            await self._store_connection()
            with patch.object(
                GmailService,
                "list_messages",
                AsyncMock(
                    return_value={"messages": [{"id": "m1"}, {"id": "m2"}], "resultSizeEstimate": 2}
                ),
            ), patch.object(
                GmailService,
                "get_message_metadata",
                _meta_async(
                    lambda _t, mid: _meta_message(
                        mid, internal_ms="1725000000000" if mid == "m1" else "1725000001000"
                    )
                ),
            ):
                return await client.get(
                    "/api/v1/gmail/messages", params={"label_ids": "INBOX", "max_results": 2}
                )

        body = self.run_async(run).json()
        self.assertEqual(body["result_size_estimate"], 2)
        # Newest first (m2 has the later internalDate).
        self.assertEqual([m["id"] for m in body["messages"]], ["m2", "m1"])
        first = body["messages"][0]
        self.assertEqual(first["subject"], "Hello")
        self.assertEqual(first["from_email"], "alice@x.com")

    def test_messages_list_requires_connection(self):
        res = self.run_async(lambda c: c.get("/api/v1/gmail/messages"))
        self.assertEqual(res.status_code, 409)
        self.assertIn("not connected", res.json()["detail"])

    def test_messages_list_propagates_unread_query_and_skips_vanished_rows(self):
        async def run(client):
            await self._store_connection()
            return await client.get("/api/v1/gmail/messages", params={"q": "from:alice", "unread_only": True})

        calls = {}

        def fake_list(access_token, q="", label_ids=None, max_results=40, page_token=""):
            calls["q"] = q
            return {"messages": [{"id": "gone"}]}

        def fake_meta(_token, mid):
            raise GmailApiError("gone", category="not_found")

        with patch.object(GmailService, "list_messages", _meta_async(fake_list)), patch.object(
            GmailService, "get_message_metadata", _meta_async(fake_meta)
        ):
            res = self.run_async(run)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(calls["q"], "from:alice is:unread")
        self.assertEqual(res.json()["messages"], [])

    def test_message_detail_parses_parts_and_attachments(self):
        async def run(client):
            await self._store_connection()
            with patch.object(GmailService, "get_message", AsyncMock(return_value=_full_message("m1"))):
                return await client.get("/api/v1/gmail/messages/m1")

        body = self.run_async(run).json()
        self.assertEqual(body["subject"], "Hello")
        self.assertEqual(body["text_body"], "Body of m1")
        self.assertEqual(body["html_body"], "<p>Body of m1</p>")
        self.assertEqual(body["attachments"][0]["filename"], "report.pdf")
        self.assertEqual(body["attachments"][0]["attachment_id"], "ATT-m1")
        self.assertFalse(body["is_read"])  # UNREAD present
        self.assertEqual(body["to"][0]["email"], "me@gmail.com")

    def test_label_modify_routes_to_gmail(self):
        async def run(client):
            await self._store_connection()
            return await client.patch(
                "/api/v1/gmail/messages/m1",
                json={"add_label_ids": ["STARRED"], "remove_label_ids": ["UNREAD"]},
            )

        calls = {}

        def fake_modify(_token, mid, add_label_ids=None, remove_label_ids=None):
            calls["add"] = add_label_ids
            calls["remove"] = remove_label_ids
            return {"id": mid}

        def fake_meta(_token, mid):
            return _meta_message(mid, labels=("INBOX", "STARRED"))

        with patch.object(GmailService, "modify_message", _meta_async(fake_modify)), patch.object(
            GmailService, "get_message_metadata", _meta_async(fake_meta)
        ):
            res = self.run_async(run)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(calls["add"], ["STARRED"])
        self.assertEqual(calls["remove"], ["UNREAD"])
        self.assertIn("STARRED", res.json()["label_ids"])
        self.assertTrue(res.json()["is_read"])

    def test_label_modify_validates_ids(self):
        async def run(client):
            await self._store_connection()
            return await client.patch("/api/v1/gmail/messages/m1", json={"add_label_ids": ["../etc"]})

        res = self.run_async(run)
        self.assertEqual(res.status_code, 400)

    def test_label_modify_requires_something_to_change(self):
        async def run(client):
            await self._store_connection()
            return await client.patch("/api/v1/gmail/messages/m1", json={})

        res = self.run_async(run)
        self.assertEqual(res.status_code, 400)

    def test_trash_untrash(self):
        async def run(client):
            await self._store_connection()
            trash = await client.post("/api/v1/gmail/messages/m1/trash")
            untrash = await client.post("/api/v1/gmail/messages/m1/untrash")
            return trash, untrash

        with patch.object(GmailService, "trash_message", AsyncMock()) as trashed, patch.object(
            GmailService, "untrash_message", AsyncMock()
        ) as untrashed, patch.object(
            GmailService, "get_message_metadata", _meta_async(lambda _token, mid: _meta_message(mid))
        ):
            trash_res, untrash_res = self.run_async(run)
        self.assertEqual(trash_res.status_code, 200)
        self.assertEqual(untrash_res.status_code, 200)
        trashed.assert_awaited_once()
        untrashed.assert_awaited_once()

    def test_thread_returns_ordered_full_messages(self):
        async def run(client):
            await self._store_connection()
            return await client.get("/api/v1/gmail/threads/thread-1")

        with patch.object(
            GmailService, "get_thread", AsyncMock(return_value={"messages": [{"id": "m1"}, {"id": "m2"}]})
        ), patch.object(GmailService, "get_message", _meta_async(lambda _token, mid: _full_message(mid))):
            body = self.run_async(run).json()
        self.assertEqual(len(body["messages"]), 2)
        self.assertEqual(body["messages"][0]["text_body"], "Body of m1")

    def test_unread_tick(self):
        async def run(client):
            await self._store_connection()
            return await client.get("/api/v1/gmail/unread")

        with patch.object(
            GmailService, "get_label", AsyncMock(return_value={"messagesTotal": "50", "messagesUnread": "3"})
        ), patch.object(
            GmailService, "list_messages", AsyncMock(return_value={"messages": [{"id": "m1"}]})
        ), patch.object(
            GmailService,
            "get_message_metadata",
            _meta_async(lambda _token, mid: _meta_message(mid, labels=("INBOX", "UNREAD"))),
        ):
            body = self.run_async(run).json()
        self.assertEqual(body["unread_in_inbox"], 3)
        self.assertEqual(body["inbox_total"], 50)
        self.assertEqual(len(body["messages"]), 1)

    def test_attachment_download_proxies_bytes(self):
        async def run(client):
            await self._store_connection()
            return await client.get("/api/v1/gmail/messages/m1/attachments/ATT-m1")

        with patch.object(GmailService, "get_message", AsyncMock(return_value=_full_message("m1"))), patch.object(
            GmailService, "get_attachment", AsyncMock(return_value=b"%PDF-1.4 fake")
        ):
            res = self.run_async(run)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b"%PDF-1.4 fake")
        self.assertIn("attachment", res.headers["content-disposition"])
        self.assertIn("report.pdf", res.headers["content-disposition"])
        self.assertEqual(res.headers["content-type"], "application/pdf")

    def test_google_error_categories_map_to_http(self):
        async def run(client):
            await self._store_connection()
            statuses = {}
            for category, expected in [
                ("auth", 409),
                ("quota", 429),
                ("not_found", 404),
                ("invalid", 400),
                ("upstream", 502),
            ]:
                with patch.object(
                    GmailService, "list_messages", AsyncMock(side_effect=GmailApiError("x", category=category))
                ):
                    res = await client.get("/api/v1/gmail/messages")
                statuses[category] = res.status_code
            return statuses

        statuses = self.run_async(run)
        self.assertEqual(
            statuses, {"auth": 409, "quota": 429, "not_found": 404, "invalid": 400, "upstream": 502}
        )

    def test_expired_token_is_refreshed_before_the_call(self):
        async def run(client):
            async with self.Session() as s:
                conn = GmailConnection(
                    owner_email=OWNER,
                    account_email=GOOGLE_ACCOUNT,
                    encrypted_access_token=_enc("at-old"),
                    encrypted_refresh_token=_enc("rt-1"),
                    granted_scopes=SCOPES_JOINED,
                    expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                )
                s.add(conn)
                await s.commit()
            return await client.get("/api/v1/gmail/profile")

        with patch.object(
            GmailService,
            "refresh_access_token",
            AsyncMock(return_value={"access_token": "at-fresh", "expires_in": 3600}),
        ) as refreshed, patch.object(
            GmailService,
            "get_account_info",
            AsyncMock(
                return_value={"emailAddress": GOOGLE_ACCOUNT, "messagesTotal": "1", "threadsTotal": "1", "historyId": "h"}
            ),
        ):
            res = self.run_async(run)
        self.assertEqual(res.status_code, 200)
        refreshed.assert_awaited_once()
        self.assertEqual(refreshed.await_args.args[0], "rt-1")

        async def inspect(_client=None):
            async with self.Session() as s:
                conn = (await s.execute(select(GmailConnection))).scalar_one()
                return conn.expires_at, conn.encrypted_access_token

        expires_at, encrypted = self.run_async(inspect)
        self.assertIsNotNone(expires_at)
        self.assertEqual(decrypt_credential(encrypted), "at-fresh")

    def test_auth_error_from_google_tells_user_to_reconnect(self):
        async def run(client):
            await self._store_connection()
            with patch.object(
                GmailService,
                "get_message",
                AsyncMock(
                    side_effect=GmailApiError(
                        "Request had invalid authentication credentials", category="auth"
                    )
                ),
            ):
                return await client.get("/api/v1/gmail/messages/m1")

        res = self.run_async(run)
        self.assertEqual(res.status_code, 409)
        self.assertIn("Reconnect", res.json()["detail"])


# ── send ─────────────────────────────────────────────────────────────────────

class SendTests(GmailApiTests):
    def _decode_mime(self, raw):
        padded = raw + "=" * (-len(raw) % 4)
        return message_from_bytes(base64.urlsafe_b64decode(padded.encode()))

    def _plain_text(self, mime):
        for part in mime.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode()
        return ""

    def test_send_builds_mime_and_returns_ids(self):
        sent = {"id": "out-1", "threadId": "thread-out"}

        def fake_send(_token, payload):
            mime = self._decode_mime(payload["raw"])
            self.assertEqual(mime["From"], GOOGLE_ACCOUNT)
            self.assertEqual(mime["To"], "alice@x.com")
            self.assertEqual(mime["Cc"], "bob@x.com")
            self.assertEqual(mime["Subject"], "Hello Alice")
            self.assertIn("The body", self._plain_text(mime))
            self.assertIsNone(mime["Bcc"])  # Bcc must not leak into headers
            return sent

        async def run(client):
            await self._store_connection()
            with patch.object(GmailService, "send_message", _meta_async(fake_send)):
                return await client.post(
                    "/api/v1/gmail/send",
                    json={
                        "to": "alice@x.com",
                        "cc": "bob@x.com",
                        "bcc": "hidden@x.com",
                        "subject": "Hello Alice",
                        "body": "The body",
                    },
                )

        res = self.run_async(run)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["id"], "out-1")
        self.assertEqual(body["thread_id"], "thread-out")

    def test_send_validates_recipients(self):
        async def run(client):
            await self._store_connection()
            with patch.object(GmailService, "send_message", AsyncMock()) as send:
                bad = await client.post(
                    "/api/v1/gmail/send",
                    json={"to": "not-an-email", "subject": "x", "body": "y"},
                )
                empty = await client.post(
                    "/api/v1/gmail/send", json={"to": "", "subject": "x", "body": "y"}
                )
            return bad, empty, send

        bad, empty, send = self.run_async(run)
        self.assertEqual(bad.status_code, 400)
        self.assertIn("not-an-email", bad.json()["detail"])
        self.assertEqual(empty.status_code, 400)
        send.assert_not_awaited()

    def test_send_requires_connection(self):
        res = self.run_async(
            lambda c: c.post("/api/v1/gmail/send", json={"to": "a@b.co", "subject": "x", "body": "y"})
        )
        self.assertEqual(res.status_code, 409)

    def test_send_supports_reply_threading_headers(self):
        sent = {"id": "out-2", "threadId": "thread-9"}

        def fake_send(_token, payload):
            mime = self._decode_mime(payload["raw"])
            self.assertEqual(mime["In-Reply-To"], "<orig@x>")
            self.assertEqual(mime["References"], "<orig@x> <mid@x>")
            return sent

        async def run(client):
            await self._store_connection()
            with patch.object(GmailService, "send_message", _meta_async(fake_send)):
                return await client.post(
                    "/api/v1/gmail/send",
                    json={
                        "to": "alice@x.com",
                        "subject": "Re: Hello",
                        "body": "Sure",
                        "in_reply_to": "<orig@x>",
                        "references": "<orig@x> <mid@x>",
                    },
                )

        res = self.run_async(run)
        self.assertEqual(res.status_code, 200)


class ErrorMappingUnitTests(unittest.TestCase):
    """Error mapping on the service level (no DB/HTTP)."""

    def test_reason_classification(self):
        cases = [
            (401, {"error": {"message": "invalid auth"}}, "auth"),
            (403, {"error": {"errors": [{"reason": "insufficientPermissions"}]}}, "auth"),
            (403, {"error": {"errors": [{"reason": "rateLimitExceeded"}]}}, "quota"),
            (403, {"error": {"errors": [{"reason": "userRateLimitExceeded"}]}}, "quota"),
            (429, {"error": {"message": "slow down"}}, "quota"),
            (404, {"error": {"message": "not found"}}, "not_found"),
            (400, {"error": {"message": "bad request"}}, "invalid"),
            (403, {"error": {"message": "forbidden for another reason"}}, "invalid"),
            (500, {"error": {"message": "oops"}}, "upstream"),
        ]
        for code, body, expected in cases:
            exc = GmailService._error_from(code, body, "/users/me/messages")
            self.assertEqual(exc.category, expected, (code, body))


class MimeHelperTests(unittest.TestCase):
    """Pure parsing/building helpers (no network, no DB)."""

    def test_decode_base64url_padding(self):
        self.assertEqual(decode_base64url(_b64("hello world")), "hello world")
        self.assertEqual(decode_base64url(""), "")
        self.assertEqual(decode_base64url("!!not-base64!!"), "")
        self.assertEqual(decode_base64url("aGVsbG8"), "hello")

    def test_headers_decode_encoded_words(self):
        from services.gmail import summarize_message

        msg = {
            "id": "m",
            "labelIds": ["UNREAD"],
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "=?utf-8?Q?caf=C3=A9?="},
                    {"name": "From", "value": "=?utf-8?B?SsO8cmdlbg==?= <j@x.com>"},
                ]
            },
        }
        summary = summarize_message(msg)
        self.assertEqual(summary["subject"], "café")
        self.assertEqual(summary["from_name"], "Jürgen")
        self.assertFalse(summary["is_read"])

    def test_raw_message_is_multipart_alternative_and_escaped(self):
        from services.gmail import raw_message_json

        payload = raw_message_json(
            from_email="me@gmail.com",
            to=["a@x.com"],
            subject="Hi <b>",
            body="Line one\n<script>alert(1)</script>",
            cc=["c@x.com"],
        )
        padded = payload["raw"] + "=" * (-len(payload["raw"]) % 4)
        mime = message_from_bytes(base64.urlsafe_b64decode(padded.encode()))
        self.assertEqual(mime.get_content_type(), "multipart/alternative")
        self.assertEqual(mime["From"], "me@gmail.com")
        self.assertEqual(mime["To"], "a@x.com")
        html = ""
        plain = ""
        for part in mime.walk():
            if part.get_content_type() == "text/html":
                html = part.get_payload(decode=True).decode()
            if part.get_content_type() == "text/plain":
                plain = part.get_payload(decode=True).decode()
        self.assertEqual(plain.rstrip("\n"), "Line one\n<script>alert(1)</script>")
        # HTML rendition is escaped — a pasted script tag must never render.
        self.assertNotIn("<script>", html)
        self.assertIn("alert(1)", html)


if __name__ == "__main__":
    unittest.main()
