"""Hosted-demo (ENVIRONMENT=deployment) must be a reduced, honest mode.

The public demo runs on one small container with no residential proxies, so
unattended timer work is switched off: Celery Beat publishes nothing, and the
endpoints that arm a *repeating* schedule refuse with 503 rather than writing
a ``next_scan_at`` that will never be dispatched.

Two properties matter most and are asserted here:

1. Turning scheduling off must NOT disable on-demand work. The Celery worker
   still runs on the demo, so a manual WhatsApp scan must stay reachable.
2. Every other ENVIRONMENT — including the default "production" — must behave
   exactly as it did before, so local and self-hosted installs are untouched.
"""
import asyncio
import importlib
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

import core.config as config_module  # noqa: E402
from api.dependencies import (  # noqa: E402
    require_scheduled_jobs_enabled,
    scheduled_jobs_enabled,
)
from core.config import settings  # noqa: E402


def build_settings(**env):
    """A fresh Settings() built from an explicit environment.

    Deliberately does NOT reload core.config: reloading rebinds the module's
    ``settings`` singleton, and every other module holds a reference to the
    OLD object. Tests elsewhere monkeypatch that shared singleton, so a reload
    here would silently break them. Constructing Settings() reads os.environ
    directly and leaves the singleton alone.
    """
    saved = {k: os.environ.get(k) for k in ("ENVIRONMENT", "SCHEDULED_JOBS_ENABLED")}
    try:
        for key in saved:
            os.environ.pop(key, None)
        os.environ.update(env)
        return config_module.Settings()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class EnvironmentModeTests(unittest.TestCase):
    """ENVIRONMENT decides the mode; SCHEDULED_JOBS_ENABLED can override it."""

    def test_deployment_disables_scheduling(self):
        s = build_settings(ENVIRONMENT="deployment")
        self.assertTrue(s.is_deployment)
        self.assertFalse(s.scheduled_jobs_enabled)

    def test_deployment_match_is_case_insensitive(self):
        self.assertTrue(build_settings(ENVIRONMENT="Deployment").is_deployment)
        self.assertTrue(build_settings(ENVIRONMENT=" DEPLOYMENT ").is_deployment)

    def test_production_is_unchanged(self):
        """The pre-existing default must keep every scheduler running."""
        s = build_settings(ENVIRONMENT="production")
        self.assertFalse(s.is_deployment)
        self.assertTrue(s.scheduled_jobs_enabled)
        self.assertIsNone(s.deployment_notice)

    def test_development_is_unchanged(self):
        s = build_settings(ENVIRONMENT="development")
        self.assertFalse(s.is_deployment)
        self.assertTrue(s.scheduled_jobs_enabled)
        self.assertIsNone(s.deployment_notice)

    def test_default_environment_keeps_scheduling_on(self):
        """No ENVIRONMENT set at all must not silently disable schedules."""
        s = build_settings()
        self.assertFalse(s.is_deployment)
        self.assertTrue(s.scheduled_jobs_enabled)

    def test_explicit_override_wins_in_both_directions(self):
        on = build_settings(ENVIRONMENT="deployment", SCHEDULED_JOBS_ENABLED="true")
        self.assertTrue(on.is_deployment)
        self.assertTrue(on.scheduled_jobs_enabled, "explicit true must re-enable")

        off = build_settings(ENVIRONMENT="production", SCHEDULED_JOBS_ENABLED="false")
        self.assertFalse(off.is_deployment)
        self.assertFalse(off.scheduled_jobs_enabled, "explicit false must disable")

    def test_notice_mentions_the_support_address(self):
        s = build_settings(ENVIRONMENT="deployment")
        self.assertIsNotNone(s.deployment_notice)
        self.assertIn(s.SUPPORT_EMAIL, s.deployment_notice)
        self.assertIn("@", s.SUPPORT_EMAIL)


class RequireScheduledJobsEnabledTests(unittest.TestCase):
    """The dependency reads the setting at call time so it can be toggled."""

    def setUp(self):
        self._original = settings.SCHEDULED_JOBS_ENABLED_OVERRIDE

    def tearDown(self):
        settings.SCHEDULED_JOBS_ENABLED_OVERRIDE = self._original

    def test_allows_when_enabled(self):
        settings.SCHEDULED_JOBS_ENABLED_OVERRIDE = True
        self.assertTrue(scheduled_jobs_enabled())
        self.assertIsNone(require_scheduled_jobs_enabled())

    def test_raises_503_when_disabled(self):
        settings.SCHEDULED_JOBS_ENABLED_OVERRIDE = False
        self.assertFalse(scheduled_jobs_enabled())
        with self.assertRaises(HTTPException) as ctx:
            require_scheduled_jobs_enabled()
        self.assertEqual(ctx.exception.status_code, 503)

    def test_message_points_users_at_running_locally(self):
        settings.SCHEDULED_JOBS_ENABLED_OVERRIDE = False
        with self.assertRaises(HTTPException) as ctx:
            require_scheduled_jobs_enabled()
        self.assertIn("locally", str(ctx.exception.detail).lower())


class BeatScheduleTests(unittest.TestCase):
    """Beat must publish nothing on the demo, and everything elsewhere."""

    EXPECTED = {
        "dispatch-due-account-sessions",
        "dispatch-due-feed-scans",
        "dispatch-due-whatsapp-scans",
    }

    def _beat_schedule_for(self, environment):
        """Rebuild celery_app under a given ENVIRONMENT.

        Mutates the shared settings singleton and reloads only
        worker.celery_app, so no other module ends up pointing at a stale
        Settings object.
        """
        saved = settings.ENVIRONMENT
        try:
            settings.ENVIRONMENT = environment
            import worker.celery_app as celery_module

            importlib.reload(celery_module)
            return set(celery_module.celery_app.conf.beat_schedule)
        finally:
            settings.ENVIRONMENT = saved
            import worker.celery_app as celery_module

            importlib.reload(celery_module)

    def test_deployment_has_no_periodic_tasks(self):
        self.assertEqual(self._beat_schedule_for("deployment"), set())

    def test_production_keeps_every_dispatcher(self):
        self.assertEqual(self._beat_schedule_for("production"), self.EXPECTED)

    def test_development_keeps_every_dispatcher(self):
        self.assertEqual(self._beat_schedule_for("development"), self.EXPECTED)


class FeaturesPayloadTests(unittest.TestCase):
    """/api/v1/features drives the banner, so its shape is a contract."""

    def _payload(self, environment):
        """Call the real /features handler under a given ENVIRONMENT.

        The handler reads settings at call time, so flipping the singleton is
        enough — no module reload, and therefore no stale-settings fallout in
        other test modules.
        """
        import main

        saved = settings.ENVIRONMENT
        try:
            settings.ENVIRONMENT = environment
            return asyncio.run(main.feature_flags())
        finally:
            settings.ENVIRONMENT = saved

    def test_deployment_payload_flags_the_demo(self):
        body = self._payload("deployment")
        self.assertTrue(body["deployment"]["is_demo"])
        self.assertTrue(body["deployment"]["notice"])
        self.assertFalse(body["scheduled_jobs"]["enabled"])
        self.assertTrue(body["scheduled_jobs"]["message"])

    def test_production_payload_shows_no_banner(self):
        body = self._payload("production")
        self.assertFalse(body["deployment"]["is_demo"])
        self.assertIsNone(
            body["deployment"]["notice"],
            "notice must be null outside deployment mode — the banner keys off it",
        )
        self.assertTrue(body["scheduled_jobs"]["enabled"])
        self.assertIsNone(body["scheduled_jobs"]["message"])

    def test_whatsapp_stays_enabled_on_the_demo(self):
        """Disabling schedules must never disable WhatsApp itself."""
        body = self._payload("deployment")
        self.assertTrue(body["whatsapp"]["enabled"])


class GatedRouteTests(unittest.TestCase):
    """Only *recurring* scheduling is gated — on-demand work stays reachable."""

    @classmethod
    def setUpClass(cls):
        import main

        cls.routes = {}
        for route in main.app.routes:
            path = getattr(route, "path", None)
            if path is None:
                continue
            for method in getattr(route, "methods", set()) or set():
                cls.routes[(method, path)] = route

    def _dependency_names(self, method, path):
        route = self.routes.get((method, path))
        self.assertIsNotNone(route, f"{method} {path} is not registered")
        return {
            getattr(dep.dependency, "__name__", "")
            for dep in getattr(route, "dependencies", [])
        }

    def test_whatsapp_filter_activate_is_gated(self):
        """Arming a repeating scan must respect the scheduling switch."""
        for path in (
            "/api/v1/whatsapp/filters/{filter_id}/activate",
            "/api/v1/whatsapp/filters/jobs/{filter_id}/activate",
        ):
            with self.subTest(path=path):
                self.assertIn(
                    "require_scheduled_jobs_enabled",
                    self._dependency_names("POST", path),
                )

    def test_manual_whatsapp_scan_is_not_gated(self):
        """The whole point of keeping the worker: on-demand scans still run."""
        self.assertNotIn(
            "require_scheduled_jobs_enabled",
            self._dependency_names("POST", "/api/v1/whatsapp/scan/trigger"),
        )

    def test_pause_is_not_gated(self):
        """Pausing is a way out of a running job, never a way to start one."""
        for path in (
            "/api/v1/whatsapp/filters/{filter_id}/pause",
            "/api/v1/whatsapp/filters/jobs/{filter_id}/pause",
        ):
            with self.subTest(path=path):
                self.assertNotIn(
                    "require_scheduled_jobs_enabled",
                    self._dependency_names("POST", path),
                )

    def test_whatsapp_connect_is_not_gated(self):
        """WhatsApp must stay fully usable on the demo."""
        self.assertNotIn(
            "require_scheduled_jobs_enabled",
            self._dependency_names("POST", "/api/v1/whatsapp/connect"),
        )


if __name__ == "__main__":
    unittest.main()
