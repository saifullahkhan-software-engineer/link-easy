"""LinkedIn automation ships ENABLED; the gate is a kill switch.

Every endpoint that opens a Chromium session against linkedin.com carries
``require_linkedin_enabled`` so an operator can set LINKEDIN_ENABLED=false
and get a clean 503 (instead of failures halfway through a campaign run or
login) without a redeploy. Default is ON for every deployment — hosted
users must be able to connect accounts, start campaigns and activate feed-
scan jobs. Read-only and cleanup routes stay open even when the switch is
off, so anything connected or running can still be seen, paused and
removed.
"""
import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test")

from fastapi import HTTPException  # noqa: E402

from api.v1.linkedin import linkedin_enabled, require_linkedin_enabled  # noqa: E402
from core.config import settings  # noqa: E402


class RequireLinkedInEnabledTests(unittest.TestCase):
    def setUp(self):
        self._original = settings.LINKEDIN_ENABLED

    def tearDown(self):
        settings.LINKEDIN_ENABLED = self._original

    def test_enabled_by_default(self):
        """Shipping default must be ON — campaigns/feed scans must start."""
        self.assertTrue(
            type(settings).model_fields["LINKEDIN_ENABLED"].default,
            "LINKEDIN_ENABLED must default to True",
        )

    def test_raises_503_when_kill_switch_off(self):
        settings.LINKEDIN_ENABLED = False
        with self.assertRaises(HTTPException) as ctx:
            require_linkedin_enabled()
        self.assertEqual(ctx.exception.status_code, 503)

    def test_error_is_temporary_and_points_at_whatsapp(self):
        settings.LINKEDIN_ENABLED = False
        with self.assertRaises(HTTPException) as ctx:
            require_linkedin_enabled()
        detail = str(ctx.exception.detail).lower()
        # A temporary-outage message, not a "we never built this" message.
        self.assertIn("temporarily", detail)
        # Must point users at the alternative that does work.
        self.assertIn("whatsapp", detail)

    def test_never_uses_an_auth_status_code(self):
        """401/403 would bounce the user to /login via the axios interceptor."""
        settings.LINKEDIN_ENABLED = False
        with self.assertRaises(HTTPException) as ctx:
            require_linkedin_enabled()
        self.assertNotIn(ctx.exception.status_code, (401, 403))

    def test_passes_when_enabled(self):
        settings.LINKEDIN_ENABLED = True
        self.assertIsNone(require_linkedin_enabled())
        self.assertTrue(linkedin_enabled())

    def test_flag_is_read_at_call_time(self):
        """Toggling the setting must take effect without reimporting."""
        settings.LINKEDIN_ENABLED = False
        self.assertFalse(linkedin_enabled())
        settings.LINKEDIN_ENABLED = True
        self.assertTrue(linkedin_enabled())


class GatedRouteCoverageTests(unittest.TestCase):
    """Every route that opens a LinkedIn browser must carry the gate."""

    @classmethod
    def setUpClass(cls):
        import main

        cls.app = main.app
        cls.gated = set()
        for route in cls.app.routes:
            deps = getattr(route, "dependencies", None) or []
            names = [getattr(d.dependency, "__name__", "") for d in deps]
            if "require_linkedin_enabled" in names:
                for method in route.methods:
                    cls.gated.add((method, route.path))

    def assert_gated(self, method, path):
        self.assertIn(
            (method, path),
            self.gated,
            f"{method} {path} opens a LinkedIn browser and must be gated",
        )

    def test_account_connect_is_gated(self):
        self.assert_gated("POST", "/api/v1/linkedin/account")

    def test_verification_endpoints_are_gated(self):
        self.assert_gated("POST", "/api/v1/linkedin/account/verify")
        self.assert_gated("POST", "/api/v1/linkedin/account/verify-session")

    def test_live_chat_is_gated(self):
        self.assert_gated("POST", "/api/v1/linkedin/live/start")
        self.assert_gated("POST", "/api/v1/linkedin/live/messages/send")

    def test_profile_scan_is_gated(self):
        self.assert_gated("POST", "/api/v1/linkedin/profile/scan")

    def test_campaign_dispatch_is_gated(self):
        self.assert_gated("POST", "/api/v1/campaigns/{campaign_id}/start")
        self.assert_gated("POST", "/api/v1/campaigns/{campaign_id}/restart")

    def test_feed_scroll_dispatch_is_gated(self):
        self.assert_gated("POST", "/api/v1/feed-scroll/jobs/{job_id}/scan")
        self.assert_gated("POST", "/api/v1/feed-scroll/jobs/{job_id}/activate")

    # ── Routes that must stay OPEN ────────────────────────────────────────

    def assert_open(self, method, path):
        self.assertNotIn(
            (method, path),
            self.gated,
            f"{method} {path} must stay reachable while LinkedIn is disabled",
        )

    def test_read_and_delete_account_stay_open(self):
        # Existing users must still see and remove their account.
        self.assert_open("GET", "/api/v1/linkedin/account")
        self.assert_open("DELETE", "/api/v1/linkedin/account")

    def test_live_stop_stays_open(self):
        # Otherwise a browser started before the flag flipped could never be
        # stopped, stranding its profile lock for the full 30-minute TTL.
        self.assert_open("POST", "/api/v1/linkedin/live/stop")

    def test_pause_stays_open(self):
        self.assert_open("POST", "/api/v1/campaigns/{campaign_id}/pause")
        self.assert_open("POST", "/api/v1/feed-scroll/jobs/{job_id}/pause")

    def test_whatsapp_is_completely_unaffected(self):
        for method, path in self.gated:
            self.assertNotIn("whatsapp", path.lower(), f"{method} {path} must not be gated")


class FeatureFlagEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_linkedin_enabled_by_default(self):
        import main

        original = settings.LINKEDIN_ENABLED
        try:
            settings.LINKEDIN_ENABLED = True
            payload = await main.feature_flags()
            self.assertTrue(payload["linkedin"]["enabled"])
            self.assertIsNone(payload["linkedin"]["message"])
            self.assertTrue(payload["whatsapp"]["enabled"])
        finally:
            settings.LINKEDIN_ENABLED = original

    async def test_reports_disabled_with_message_when_kill_switch_off(self):
        import main

        original = settings.LINKEDIN_ENABLED
        try:
            settings.LINKEDIN_ENABLED = False
            payload = await main.feature_flags()
            self.assertFalse(payload["linkedin"]["enabled"])
            self.assertTrue(payload["linkedin"]["message"])
            self.assertIn("temporarily", payload["linkedin"]["message"].lower())
        finally:
            settings.LINKEDIN_ENABLED = original

    async def test_no_message_when_enabled(self):
        import main

        original = settings.LINKEDIN_ENABLED
        try:
            settings.LINKEDIN_ENABLED = True
            payload = await main.feature_flags()
            self.assertTrue(payload["linkedin"]["enabled"])
            self.assertIsNone(payload["linkedin"]["message"])
        finally:
            settings.LINKEDIN_ENABLED = original


if __name__ == "__main__":
    unittest.main()
