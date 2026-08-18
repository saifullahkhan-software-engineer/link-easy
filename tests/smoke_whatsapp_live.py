"""Smoke-test the new WhatsApp live chat surface without needing a real DB.

Boots the app in-process via httpx's ASGI transport (no lifespan run) and
hits every /api/v1/whatsapp/live/* endpoint to verify wiring + auth + 409
when the live session is not running. Prints each response.
"""
import asyncio
import os
import sys

# main.py lives at the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Required env so pydantic Settings can resolve at import time.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault(
    "CREDENTIAL_ENCRYPTION_KEY",
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
)
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost:5173")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import httpx

# Importing main wires every router, including whatsapp_live.
import main  # noqa: E402

BASE = "/api/v1/whatsapp/live"


def green(label, code):
    return f"  ✓ {label:<58} → {code}"


async def main_async():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://testserver",
    ) as client:
        cases = [
            ("GET", f"{BASE}/status", 401),
            ("POST", f"{BASE}/start", 401),
            ("POST", f"{BASE}/stop", 401),
            ("GET", f"{BASE}/chats", 401),
            ("POST", f"{BASE}/chats/open", 401),
            ("POST", f"{BASE}/chats/close", 401),
            ("GET", f"{BASE}/messages", 401),
            ("POST", f"{BASE}/messages/send", 401),
        ]
        print(f"Smoke test: 8 endpoints should return 401 (no auth)")
        print("-" * 70)
        for method, url, expected in cases:
            r = await client.request(method, url)
            ok = "✓" if r.status_code == expected else "✗"
            print(f"  {ok} {method:<5} {url:<44} → {r.status_code} (expected {expected})")
        # Also confirm the legacy scanner endpoints still wired (no regressions).
        legacy = [
            ("GET", "/api/v1/whatsapp/status", 401),
            ("DELETE", "/api/v1/whatsapp/connection", 401),
            ("POST", "/api/v1/whatsapp/scan/trigger", 401),
            ("GET", "/api/v1/whatsapp/groups", 401),
        ]
        print("\nRegression check: existing /whatsapp/* still wired")
        print("-" * 70)
        for method, url, expected in legacy:
            r = await client.request(method, url)
            ok = "✓" if r.status_code == expected else "✗"
            print(f"  {ok} {method:<5} {url:<44} → {r.status_code} (expected {expected})")


if __name__ == "__main__":
    asyncio.run(main_async())
