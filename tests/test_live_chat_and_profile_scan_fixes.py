"""Regressions for the WhatsApp live-chat and LinkedIn profile-scan fixes.

Three defects are covered:

1. ``POST /api/v1/whatsapp/live/start`` raised ``HTTPException(detail=<pydantic
   model>)``. Starlette cannot JSON-encode a model, so every failed start
   collapsed into an opaque 500 "Internal Server Error" and the real reason
   (not connected / profile busy / session expired) never reached the UI.

2. The profile scraper injected a NON-raw Python string as JavaScript, so the
   ``\\b`` word boundary in the "Show all"/"See more" matcher became a literal
   backspace (0x08). The expander regex therefore matched nothing, LinkedIn's
   collapsed Experience/Education/Skills cards were never opened, and scans
   came back with basics/About only.

3. ``LiveBrowserManager.start`` called the *synchronous* Redis
   ``acquire_profile_lock`` directly on the event loop with a 30s blocking
   timeout, freezing every other API request (status/chat polling) while a
   live-chat session was starting.

All tests are pure Python — no Playwright, no Redis, no network.
"""
import ast
import asyncio
import os
import re
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test")

SCRAPER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "services",
    "linkedin_profile_scraper.py",
)

# Characters that must never appear in JavaScript source shipped to a browser.
# Each is produced by a Python escape that was meant to stay literal
# (\b -> backspace, \f -> form feed, \v -> vertical tab, \a -> bell).
_CONTROL_CHARS = {
    "\x08": r"\b (word boundary became a backspace)",
    "\x0c": r"\f (became a form feed)",
    "\x0b": r"\v (became a vertical tab)",
    "\x07": r"\a (became a bell)",
}


def _injected_js_literals(path):
    """Yield (lineno, source) for every JS string handed to the browser."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", "") not in {
            "evaluate",
            "evaluate_handle",
            "add_init_script",
        }:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                yield node.lineno, arg.value


class ProfileScraperInjectedJavaScriptTests(unittest.TestCase):
    """The scraper's injected JS must reach the browser byte-for-byte."""

    def test_no_python_escape_corrupted_the_javascript(self):
        literals = list(_injected_js_literals(SCRAPER_PATH))
        self.assertTrue(literals, "no injected JavaScript found to verify")

        for lineno, source in literals:
            for char, description in _CONTROL_CHARS.items():
                self.assertNotIn(
                    char,
                    source,
                    f"{SCRAPER_PATH}:{lineno} ships a control character — "
                    f"{description}. Mark the string raw (r\"\"\"...\"\"\") so the "
                    f"escape survives as JavaScript.",
                )

    def test_show_all_expander_regex_is_a_real_word_boundary(self):
        """The control matcher must keep its ``\\b`` as two JS characters."""
        expander = next(
            (
                source
                for _, source in _injected_js_literals(SCRAPER_PATH)
                if "const wanted" in source
            ),
            None,
        )
        self.assertIsNotNone(expander, "expander JavaScript not found")

        wanted = next(
            line for line in expander.splitlines() if "const wanted" in line
        )
        self.assertIn(
            r"\b",
            wanted,
            "the word-boundary escape was consumed by Python; LinkedIn's "
            "'Show all'/'See more' controls will never match",
        )

        # Behavioural check against the same pattern the browser compiles.
        pattern = re.search(r"/\^\((.+?)\)\\b/i", wanted)
        self.assertIsNotNone(pattern, f"unexpected regex shape: {wanted!r}")
        compiled = re.compile(rf"^({pattern.group(1)})\b", re.I)

        for label in ("Show all 12 experiences", "See more", "Show all skills"):
            self.assertTrue(
                compiled.match(label), f"expander would skip {label!r}"
            )
        # Must stay precise — a word boundary, not a prefix match.
        for label in ("Show alligator", "Showcase", "Seemore"):
            self.assertIsNone(
                compiled.match(label), f"expander wrongly matched {label!r}"
            )


class WhatsAppLiveStartErrorContractTests(unittest.IsolatedAsyncioTestCase):
    """A failed start must return a readable 503, never a 500."""

    async def _post_start(self, snapshot):
        from starlette.testclient import TestClient

        import main
        from api.dependencies import get_current_user, get_db
        from api.v1 import whatsapp_live

        app = main.app
        app.dependency_overrides[get_current_user] = lambda: object()
        app.dependency_overrides[get_db] = lambda: None
        try:
            with (
                patch.object(
                    whatsapp_live, "_require_connection", AsyncMock(return_value=None)
                ),
                patch.object(
                    whatsapp_live.live_browser,
                    "start",
                    AsyncMock(return_value=snapshot),
                ),
            ):
                # raise_server_exceptions=False so an unserializable detail
                # surfaces as the 500 the user actually saw.
                client = TestClient(app, raise_server_exceptions=False)
                return client.post("/api/v1/whatsapp/live/start")
        finally:
            app.dependency_overrides.clear()

    async def test_browser_failure_returns_503_with_the_real_reason(self):
        message = "WhatsApp did not reach the chat list after 45 seconds"
        response = await self._post_start(
            {
                "status": "error",
                "message": message,
                "error": message,
                "active_chat_id": None,
                "active_chat_name": None,
            }
        )

        self.assertEqual(response.status_code, 503)
        detail = response.json()["detail"]
        self.assertIsInstance(detail, str)
        self.assertEqual(detail, message)

    async def test_busy_profile_reason_is_not_swallowed_by_a_500(self):
        message = (
            "The WhatsApp browser is busy with another operation. "
            "Try again in a few seconds."
        )
        response = await self._post_start(
            {
                "status": "error",
                "message": message,
                "error": None,
                "active_chat_id": None,
                "active_chat_name": None,
            }
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], message)

    async def test_successful_start_still_returns_the_snapshot(self):
        response = await self._post_start(
            {
                "status": "running",
                "message": "Live chat is open. The scanner is paused.",
                "error": None,
                "active_chat_id": None,
                "active_chat_name": None,
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "running")


class LiveChatStartDoesNotBlockTheEventLoopTests(unittest.IsolatedAsyncioTestCase):
    """The blocking Redis lock must not stall concurrent API requests."""

    async def test_profile_lock_is_acquired_off_the_event_loop(self):
        import services.whatsapp_live_browser as live_module
        from services.whatsapp_live_browser import LiveBrowserManager

        manager = LiveBrowserManager()
        acquiring_thread = {}
        main_thread = __import__("threading").get_ident()

        def slow_blocking_acquire(*_args, **_kwargs):
            """Stand-in for redis lock.acquire(): synchronous and slow."""
            acquiring_thread["id"] = __import__("threading").get_ident()
            __import__("time").sleep(0.6)
            return object()

        class _FailingFactory:
            async def start(self):
                raise RuntimeError("stop after the lock is taken")

        ticks = 0

        async def heartbeat():
            """Simulates the UI's status/chat pollers hitting the API."""
            nonlocal ticks
            for _ in range(12):
                await asyncio.sleep(0.05)
                ticks += 1

        with (
            patch(
                "worker.profile_lock.acquire_profile_lock",
                side_effect=slow_blocking_acquire,
            ),
            patch("worker.profile_lock.release_profile_lock"),
            patch.object(live_module, "async_playwright", return_value=_FailingFactory()),
        ):
            await asyncio.gather(manager.start(), heartbeat())

        self.assertNotEqual(
            acquiring_thread.get("id"),
            main_thread,
            "acquire_profile_lock ran on the event loop thread — a 30s "
            "blocking wait would freeze every other API request",
        )
        self.assertGreaterEqual(
            ticks,
            10,
            f"event loop was starved during start (only {ticks}/12 polls ran)",
        )


if __name__ == "__main__":
    unittest.main()
