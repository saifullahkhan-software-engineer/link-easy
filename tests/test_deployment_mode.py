"""ENVIRONMENT=deployment labels the hosted instance but no longer reduces it.

Hosted users must be able to start the campaigns and feed-scan jobs they
create, so scheduling (Celery Beat's dispatchers) and LinkedIn automation
are ON by default on every environment. The switches remain as explicit
kill switches: SCHEDULED_JOBS_ENABLED=false clears the Beat schedule and
makes the recurring-job endpoints return 503, while LINKEDIN_ENABLED=false
takes the LinkedIn surfaces down — on-demand worker work is unaffected.

Three properties matter most and are asserted here:

1. Deployment mode keeps the schedulers running by default; the explicit
   override still wins in both directions on every environment.
2. Turning scheduling off must NOT disable on-demand work. The Celery
   worker keeps running, so a manual WhatsApp scan stays reachable.
3. The hosted-instance banner payload still keys off is_demo/notice.
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

    def test_deployment_keeps_scheduling_on(self):
        s = build_settings(ENVIRONMENT="deployment")
        self.assertTrue(s.is_deployment)
        self.assertTrue(
            s.scheduled_jobs_enabled,
            "hosted users must be able to start campaigns and recurring jobs",
        )

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

    def test_message_reads_as_temporary_and_mentions_on_demand(self):
        settings.SCHEDULED_JOBS_ENABLED_OVERRIDE = False
        with self.assertRaises(HTTPException) as ctx:
            require_scheduled_jobs_enabled()
        detail = str(ctx.exception.detail).lower()
        self.assertIn("on demand", detail)
        self.assertIn("temporarily", detail)


class BeatScheduleTests(unittest.TestCase):
    """Beat must publish every dispatcher everywhere by default; the
    explicit SCHEDULED_JOBS_ENABLED override is the only off switch."""

    EXPECTED = {
        "dispatch-due-account-sessions",
        "dispatch-due-feed-scans",
        "dispatch-due-whatsapp-scans",
    }

    def _beat_schedule_for(self, environment, scheduled_override=None):
        """Rebuild celery_app under a given ENVIRONMENT/override.

        Mutates the shared settings singleton and reloads only
        worker.celery_app, so no other module ends up pointing at a stale
        Settings object.
        """
        saved_env = settings.ENVIRONMENT
        saved_override = settings.SCHEDULED_JOBS_ENABLED_OVERRIDE
        try:
            settings.ENVIRONMENT = environment
            settings.SCHEDULED_JOBS_ENABLED_OVERRIDE = scheduled_override
            import worker.celery_app as celery_module

            importlib.reload(celery_module)
            return set(celery_module.celery_app.conf.beat_schedule)
        finally:
            settings.ENVIRONMENT = saved_env
            settings.SCHEDULED_JOBS_ENABLED_OVERRIDE = saved_override
            import worker.celery_app as celery_module

            importlib.reload(celery_module)

    def test_deployment_keeps_every_dispatcher(self):
        self.assertEqual(self._beat_schedule_for("deployment"), self.EXPECTED)

    def test_production_keeps_every_dispatcher(self):
        self.assertEqual(self._beat_schedule_for("production"), self.EXPECTED)

    def test_development_keeps_every_dispatcher(self):
        self.assertEqual(self._beat_schedule_for("development"), self.EXPECTED)

    def test_explicit_override_clears_the_schedule(self):
        self.assertEqual(
            self._beat_schedule_for("deployment", scheduled_override=False),
            set(),
        )
        self.assertEqual(
            self._beat_schedule_for("production", scheduled_override=False),
            set(),
        )


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

    def test_deployment_payload_flags_the_instance(self):
        body = self._payload("deployment")
        self.assertTrue(body["deployment"]["is_demo"])
        self.assertTrue(body["deployment"]["notice"])
        # Scheduling is ON even on the hosted instance.
        self.assertTrue(body["scheduled_jobs"]["enabled"])
        self.assertIsNone(body["scheduled_jobs"]["message"])
        # LinkedIn automation is ON by default too.
        self.assertTrue(body["linkedin"]["enabled"])

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
