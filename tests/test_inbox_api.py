"""Ultimate Inbox: owner isolation, bounded replies, Meta transport and callback moves."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test-secret")

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.dependencies import get_current_user, get_db
from api.v1.inbox import router
from core.config import Settings, settings
from core.security import encrypt_credential
from database import Base
from models.social_scheduler import SocialPlatformConnection
from models.user import User
from services.social.facebook import FacebookService
from services.social.instagram import InstagramService
from services.social.inbox import InboxError, MetaInboxService


class InboxApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings_patch = patch.multiple(settings, RATE_LIMIT_ENABLED=False, CREDENTIAL_ENCRYPTION_KEY="a" * 64)
        self.settings_patch.start()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.owner = "owner@inbox.test"
        self.other = "other@inbox.test"
        self.current = self.owner
        self.connection = SocialPlatformConnection(
            owner_email=self.owner, platform="facebook", account_id="page-owner", account_name="Owner Page",
            encrypted_access_token=encrypt_credential("owner-token"),
        )
        async with self.Session() as db:
            db.add_all([User(email=email, first_name="Test", last_name="User", hashed_password="x", is_verified=True)
                        for email in (self.owner, self.other)])
            await db.flush()
            db.add(self.connection)
            db.add(SocialPlatformConnection(
                owner_email=self.other, platform="instagram", account_id="other-ig",
                encrypted_access_token=encrypt_credential("other-token"), extra_data={"page_id": "other-page"},
            ))
            await db.commit()

        self.app = FastAPI()
        self.app.include_router(router)

        async def db_override():
            async with self.Session() as db:
                yield db

        async def user_override():
            return User(email=self.current, first_name="Test", last_name="User", hashed_password="x", is_verified=True)

        self.app.dependency_overrides[get_db] = db_override
        self.app.dependency_overrides[get_current_user] = user_override
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        self.settings_patch.stop()

    async def test_own_connection_is_used_and_tokens_stay_server_side(self):
        async def list_for(service, after):
            self.assertEqual(service.account_id, "page-owner")
            self.assertEqual(service.access_token, "owner-token")
            self.assertEqual(service.channel, "messenger")
            return {"conversations": [{"id": "c1", "name": "Alice"}], "next_cursor": "opaque"}

        with patch.object(MetaInboxService, "list_conversations", list_for):
            response = await self.client.get("/api/v1/inbox/messenger/conversations")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("token", response.text)
        self.assertEqual(response.json()["conversations"][0]["id"], "c1")

    async def test_another_users_connection_is_never_used(self):
        with patch.object(MetaInboxService, "list_conversations", new_callable=AsyncMock) as upstream:
            response = await self.client.get("/api/v1/inbox/instagram/conversations")
            self.assertEqual(response.status_code, 409)
            self.assertIn("Accounts", response.json()["detail"])
            self.current = self.other
            response = await self.client.get("/api/v1/inbox/messenger/conversations")
            self.assertEqual(response.status_code, 409)
            upstream.assert_not_called()

    async def test_account_picker_lists_and_selects_among_the_callers_accounts(self):
        async with self.Session() as db:
            db.add(SocialPlatformConnection(
                owner_email=self.owner, platform="facebook", account_id="page-second",
                account_name="Second Page", encrypted_access_token=encrypt_credential("second-token"),
            ))
            # Pin distinct created_at values — SQLite's CURRENT_TIMESTAMP is
            # second-precision, so without this the "first-connected" default
            # would be picked by uuid tie-break and the default-selection
            # assertion below would be flaky. page-owner is the older one.
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            first_row = (
                await db.execute(select(SocialPlatformConnection).where(
                    SocialPlatformConnection.owner_email == self.owner,
                    SocialPlatformConnection.account_id == "page-owner",
                ))
            ).scalar_one()
            second_row = (
                await db.execute(select(SocialPlatformConnection).where(
                    SocialPlatformConnection.owner_email == self.owner,
                    SocialPlatformConnection.account_id == "page-second",
                ))
            ).scalar_one()
            first_row.created_at = now - timedelta(hours=2)
            second_row.created_at = now - timedelta(hours=1)
            await db.commit()
            first_id = first_row.id
            second_id = second_row.id

        # The dropdown lists exactly the caller's accounts for that channel,
        # with display details and no token material.
        response = await self.client.get("/api/v1/inbox/accounts", params={"channel": "messenger"})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["platform"], "facebook")
        self.assertEqual({a["id"] for a in payload["accounts"]}, {first_id, second_id})
        self.assertEqual({a["account_name"] for a in payload["accounts"]}, {"Owner Page", "Second Page"})
        self.assertNotIn("token", response.text)
        # The instagram channel lists no owner accounts (the one ig row is
        # another user's).
        response = await self.client.get("/api/v1/inbox/accounts", params={"channel": "instagram"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["accounts"], [])
        # Unknown channels 404.
        self.assertEqual(
            (await self.client.get("/api/v1/inbox/accounts", params={"channel": "whatsapp"})).status_code,
            404,
        )

        # Selecting a specific account routes through that account's token.
        async def list_for(service, after):
            self.assertEqual(service.account_id, "page-second")
            self.assertEqual(service.access_token, "second-token")
            return {"conversations": [], "next_cursor": None}

        with patch.object(MetaInboxService, "list_conversations", list_for):
            response = await self.client.get(
                "/api/v1/inbox/messenger/conversations", params={"account_id": second_id}
            )
        self.assertEqual(response.status_code, 200, response.text)

        # Omitting the id keeps the legacy default (first-connected account).
        async def default_for(service, after):
            self.assertEqual(service.account_id, "page-owner")
            return {"conversations": [], "next_cursor": None}

        with patch.object(MetaInboxService, "list_conversations", default_for):
            response = await self.client.get("/api/v1/inbox/messenger/conversations")
        self.assertEqual(response.status_code, 200, response.text)

        # A stale pick is a 404, never a fallback to someone else's account.
        async def never_used(service, after):
            self.fail("the inbox must not fall back when the pick is stale")
            return {"conversations": [], "next_cursor": None}

        with patch.object(MetaInboxService, "list_conversations", never_used):
            response = await self.client.get(
                "/api/v1/inbox/messenger/conversations", params={"account_id": "gone"}
            )
        self.assertEqual(response.status_code, 404)
        # Another user's account id cannot be used either.
        with patch.object(MetaInboxService, "list_conversations", never_used):
            response = await self.client.get(
                "/api/v1/inbox/messenger/conversations", params={"account_id": "other-ig"}
            )
        self.assertEqual(response.status_code, 404)

    async def test_expired_and_corrupt_connections_require_reconnect(self):
        for changes in (
            {"expires_at": datetime.now(timezone.utc) - timedelta(hours=1)},
            {"expires_at": None, "encrypted_access_token": "corrupt-token"},
        ):
            async with self.Session() as db:
                connection = await db.get(SocialPlatformConnection, self.connection.id)
                for key, value in changes.items():
                    setattr(connection, key, value)
                await db.commit()
            with patch.object(MetaInboxService, "list_conversations", new_callable=AsyncMock) as upstream:
                response = await self.client.get("/api/v1/inbox/messenger/conversations")
            self.assertEqual(response.status_code, 409)
            upstream.assert_not_called()

    async def test_unsupported_channels_are_not_silently_enabled(self):
        for channel in ("whatsapp-business", "gmail", "youtube"):
            response = await self.client.get(f"/api/v1/inbox/{channel}/conversations")
            self.assertEqual(response.status_code, 404)

    async def test_replies_are_validated_and_cannot_supply_a_token_or_recipient(self):
        with patch.object(MetaInboxService, "send_reply", new_callable=AsyncMock) as upstream:
            for payload in (
                {"conversation_id": "c1", "text": "   "},
                {"conversation_id": "c1", "text": "x" * 1001},
                {"conversation_id": "c1", "text": "😀" * 251},
                {"conversation_id": "c1", "text": "Hi", "recipient_id": "other-user"},
                {"conversation_id": "c1", "text": "Hi", "access_token": "forged"},
            ):
                response = await self.client.post("/api/v1/inbox/messenger/messages", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
            upstream.assert_not_called()
            upstream.return_value = {"message_id": "sent-1"}
            response = await self.client.post("/api/v1/inbox/messenger/messages", json={"conversation_id": "c1", "text": " Hello "})
            self.assertEqual(response.status_code, 200, response.text)
            upstream.assert_awaited_once_with("c1", "Hello")

    async def test_provider_error_is_reported_not_a_fake_empty_inbox(self):
        with patch.object(MetaInboxService, "list_messages", new_callable=AsyncMock) as upstream:
            upstream.side_effect = InboxError("Messaging permission required", 403)
            response = await self.client.get("/api/v1/inbox/messenger/messages", params={"conversation_id": "c1"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("permission", response.json()["detail"])

    async def test_routes_require_authentication(self):
        self.app.dependency_overrides.pop(get_current_user)
        response = await self.client.get("/api/v1/inbox/messenger/conversations")
        self.assertEqual(response.status_code, 401)


PARTICIPANTS = {"data": [{"id": "page-1", "name": "Our Page"}, {"id": "person-1", "name": "Alice"}]}
IG_PARTICIPANTS = {"data": [{"id": "ig-1", "username": "our.brand"}, {"id": "ig-person", "username": "alice"}]}


class MetaInboxServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_instagram_resolves_the_linked_page_token_not_the_publishing_user_token(self):
        service = MetaInboxService("instagram", "ig-1", "ig-user-token", "page-1")
        service._request = AsyncMock(side_effect=[
            {"id": "page-1", "access_token": "ig-page-token", "instagram_business_account": {"id": "ig-1"}},
            {"data": [{"id": "ic1", "participants": IG_PARTICIPANTS, "messages": {"data": [{"message": "Hello"}]}}],
             "paging": {"next": "https://graph.facebook.com/?access_token=secret", "cursors": {"after": "cursor"}}},
        ])
        result = await service.list_conversations("older")
        self.assertEqual(result["conversations"][0]["name"], "alice")
        self.assertEqual(result["next_cursor"], "cursor")
        self.assertNotIn("token", str(result))
        call = service._request.call_args_list[1]
        self.assertEqual(call.kwargs["token"], "ig-page-token")
        self.assertEqual(call.kwargs["params"]["platform"], "instagram")
        self.assertEqual(call.kwargs["params"]["after"], "older")

    async def test_instagram_cannot_fall_back_to_a_different_linked_account(self):
        service = MetaInboxService("instagram", "ig-1", "user-token", "page-1")
        service._request = AsyncMock(return_value={"access_token": "page-token", "instagram_business_account": {"id": "other-ig"}})
        with self.assertRaises(InboxError) as error:
            await service.list_conversations()
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(service._request.await_count, 1)

    async def test_messages_are_normalized_oldest_first_and_check_page_membership(self):
        service = MetaInboxService("messenger", "page-1", "page-token")
        service._request = AsyncMock(side_effect=[
            {"id": "c1", "participants": PARTICIPANTS, "messages": {"data": [
                {"id": "m2", "message": "Hello Alice", "from": {"id": "page-1", "name": "Our Page"}},
                {"id": "m1", "message": "Hi!", "from": {"id": "person-1", "name": "Alice"}},
            ]}},
            {"data": [{"id": "c1"}]},
        ])
        result = await service.list_messages("c1")
        self.assertEqual([m["id"] for m in result["messages"]], ["m1", "m2"])
        self.assertEqual([m["outgoing"] for m in result["messages"]], [False, True])
        self.assertIn("limit(20)", service._request.call_args_list[0].kwargs["params"]["fields"])

    async def test_foreign_conversations_cannot_be_read_or_replied_to(self):
        for action in ("read", "reply"):
            service = MetaInboxService("messenger", "page-1", "page-token")
            service._request = AsyncMock(return_value={"id": "other-c", "participants": {"data": [{"id": "other-page"}, {"id": "person-1"}]}})
            with self.assertRaises(InboxError) as error:
                await (service.list_messages("other-c") if action == "read" else service.send_reply("other-c", "Hello"))
            self.assertEqual(error.exception.status_code, 404)
            self.assertEqual(service._request.await_count, 1)

    async def test_cross_channel_conversations_cannot_be_read_or_replied_to(self):
        for action in ("read", "reply"):
            service = MetaInboxService("messenger", "page-1", "page-token")
            service._request = AsyncMock(side_effect=[{"id": "ig-c", "participants": PARTICIPANTS}, {"data": []}])
            with self.assertRaises(InboxError) as error:
                await (service.list_messages("ig-c") if action == "read" else service.send_reply("ig-c", "Hello"))
            self.assertEqual(error.exception.status_code, 404)
            self.assertEqual(service._request.await_count, 2)

    async def test_send_uses_validated_recipient_and_response_type(self):
        service = MetaInboxService("messenger", "page-1", "page-token")
        service._request = AsyncMock(side_effect=[
            {"id": "c1", "participants": PARTICIPANTS}, {"data": [{"id": "c1"}]}, {"message_id": "sent-1", "recipient_id": "person-1"},
        ])
        result = await service.send_reply("c1", "Hello")
        self.assertEqual(result, {"message_id": "sent-1"})
        call = service._request.call_args_list[-1]
        self.assertEqual(call.args[1], "page-1")
        self.assertEqual(call.kwargs["method"], "POST")
        self.assertEqual(call.kwargs["payload"], {"recipient": {"id": "person-1"}, "message": {"text": "Hello"}, "messaging_type": "RESPONSE"})

    async def test_transport_never_exposes_provider_error_tokens_and_never_retries_a_send(self):
        service = MetaInboxService("messenger", "page-1", "secret-token")
        response = AsyncMock()
        response.status = 403
        response.json.return_value = {"error": {"code": 200, "message": "secret-token rejected"}}
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=None)
        session = MagicMock()
        session.request.return_value = context
        with self.assertRaises(InboxError) as error:
            await service._request(session, "page-1", edge="messages", token="secret-token", method="POST", payload={"message": {"text": "Hello"}})
        self.assertNotIn("secret-token", str(error.exception))
        self.assertEqual(error.exception.status_code, 403)
        session.request.assert_called_once()
        self.assertNotIn("secret-token", session.request.call_args.args[1])
        self.assertFalse(session.request.call_args.kwargs["allow_redirects"])
        self.assertEqual(session.request.call_args.kwargs["headers"], {"Authorization": "Bearer secret-token"})


class InboxConfigurationTests(unittest.TestCase):
    def test_oauth_returns_to_accounts_and_explicit_legacy_overrides_still_work(self):
        config = Settings(DATABASE_URL="sqlite+aiosqlite:///:memory:", REDIS_URL="redis://localhost", BACKEND_CORS_ORIGINS="https://app.example.com", SOCIAL_OAUTH_RETURN_URL="", GOOGLE_OAUTH_RETURN_URL="", _env_file=None)
        self.assertEqual(config.social_oauth_return_url, "https://app.example.com/app/account")
        self.assertEqual(config.gmail_oauth_return_url, "https://app.example.com/app/account/gmail")
        config.SOCIAL_OAUTH_RETURN_URL = "https://app.example.com/app/social-scheduler/settings"
        self.assertEqual(config.social_oauth_return_url, config.SOCIAL_OAUTH_RETURN_URL)
        config.GOOGLE_OAUTH_RETURN_URL = "https://app.example.com/app/gmail"
        self.assertEqual(config.gmail_oauth_return_url, config.GOOGLE_OAUTH_RETURN_URL)

    def test_meta_oauth_requests_messaging_and_page_metadata_permissions(self):
        for service in (FacebookService, InstagramService):
            scopes = set(service.SCOPES.split(","))
            self.assertTrue({"pages_messaging", "pages_manage_metadata", "pages_read_engagement"}.issubset(scopes))
        self.assertIn("instagram_manage_messages", InstagramService.SCOPES)
