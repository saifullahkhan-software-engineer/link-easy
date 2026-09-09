"""Tests for live chat SSE message streaming and WhatsApp chat list pagination."""
import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.dependencies import get_current_user, get_db
from api.v1.linkedin_live import router as linkedin_live_router
from api.v1.whatsapp_live import router as whatsapp_live_router
from database import Base
from models.user import User


class LiveChatSseAndPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.user_email = "test@user.dev"
        async with self.Session() as db:
            db.add(
                User(
                    email=self.user_email,
                    first_name="Test",
                    last_name="User",
                    hashed_password="x",
                    is_verified=True,
                )
            )
            await db.commit()

        self.app = FastAPI()
        self.app.include_router(whatsapp_live_router)
        self.app.include_router(linkedin_live_router)

        async def db_override():
            async with self.Session() as db:
                yield db

        async def user_override():
            return User(
                email=self.user_email,
                first_name="Test",
                last_name="User",
                hashed_password="x",
                is_verified=True,
            )

        from api.dependencies import get_current_user, get_db
        from api.v1.live import sse_user

        self.app.dependency_overrides[get_db] = db_override
        self.app.dependency_overrides[get_current_user] = user_override
        self.app.dependency_overrides[sse_user] = user_override
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    # ── WhatsApp chat list pagination ────────────────────────────────────────

    async def test_whatsapp_chats_scroll_param_propagates(self):
        fake_manager = SimpleNamespace(
            status="running",
            list_chats=AsyncMock(
                return_value=[
                    {"chat_id": "c1", "name": "Chat 1", "preview": "hi", "unread_count": 0}
                ]
            ),
        )
        fake_session = SimpleNamespace(id=1, owner_email=self.user_email)

        with (
            patch("api.v1.whatsapp_live.get_owned_session", AsyncMock(return_value=fake_session)),
            patch("api.v1.whatsapp_live._manager_for", return_value=fake_manager),
        ):
            resp = await self.client.get("/api/v1/whatsapp/live/chats?limit=10&scroll=true")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertTrue(body["has_more"])
        fake_manager.list_chats.assert_called_once_with(filter_text=None, limit=10, scroll=True)

    # ── WhatsApp SSE message stream ──────────────────────────────────────────

    async def test_whatsapp_stream_requires_running_status(self):
        fake_manager = SimpleNamespace(status="idle", active_chat_id=None)
        fake_session = SimpleNamespace(id=1, owner_email=self.user_email)
        mock_user = User(email=self.user_email, first_name="Test", last_name="User", hashed_password="x")

        with (
            patch("api.v1.whatsapp_live.sse_user", return_value=mock_user),
            patch("api.v1.whatsapp_live.get_owned_session", AsyncMock(return_value=fake_session)),
            patch("api.v1.whatsapp_live._manager_for", return_value=fake_manager),
        ):
            resp = await self.client.get("/api/v1/whatsapp/live/messages/stream")

        self.assertEqual(resp.status_code, 409)
        self.assertIn("not running", resp.json()["detail"])

    async def test_whatsapp_stream_requires_open_chat(self):
        fake_manager = SimpleNamespace(status="running", active_chat_id=None)
        fake_session = SimpleNamespace(id=1, owner_email=self.user_email)
        mock_user = User(email=self.user_email, first_name="Test", last_name="User", hashed_password="x")

        with (
            patch("api.v1.whatsapp_live.sse_user", return_value=mock_user),
            patch("api.v1.whatsapp_live.get_owned_session", AsyncMock(return_value=fake_session)),
            patch("api.v1.whatsapp_live._manager_for", return_value=fake_manager),
        ):
            resp = await self.client.get("/api/v1/whatsapp/live/messages/stream")

        self.assertEqual(resp.status_code, 409)
        self.assertIn("No chat is currently open", resp.json()["detail"])

    # ── LinkedIn SSE message stream ──────────────────────────────────────────

    async def test_linkedin_stream_requires_open_chat(self):
        mock_user = User(email=self.user_email, first_name="Test", last_name="User", hashed_password="x")
        with (
            patch("api.v1.linkedin_live.sse_user", return_value=mock_user),
            patch("api.v1.linkedin_live._ensure_running", return_value=None),
            patch("api.v1.linkedin_live.linkedin_live_browser.active_chat_id", None),
        ):
            resp = await self.client.get("/api/v1/linkedin/live/messages/stream")

        self.assertEqual(resp.status_code, 409)
        self.assertIn("No chat is currently open", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
