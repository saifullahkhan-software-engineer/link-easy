"""Regression tests for the Railway "Network › Healthcheck failure" incident.

A deploy that fails the platform healthcheck produces no useful error: Railway
waits out ``healthcheckTimeout`` (300s) and reports a generic failure, while the
real cause is that the container exited before uvicorn ever bound ``$PORT``.
Three separate boot paths did exactly that, and each is pinned down here:

  * a Railway volume mounted over ``PROFILE_STORAGE_DIR`` is owned by root, so
    the non-root ``appuser`` in the image could not write it and ``start.sh``
    aborted with ``exit 1`` — now it falls back to a writable directory and
    boots, and says which variable restores persistence;
  * ``core/config.py`` built ``Settings`` at import time with several
    single-feature variables marked required, so one unset Railway variable
    raised a pydantic ``ValidationError`` while importing ``main`` — they now
    default to ``""`` and are reported instead;
  * the healthcheck pointed at ``/``; it now points at a dedicated ``/health``
    probe that touches no database, cache, queue or browser.

The ``start.sh`` cases run the real script as a subprocess so they cover the
shipped preflight, not a re-implementation of it.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START_SH = os.path.join(REPO_ROOT, "start.sh")

# Importing core.config / main builds pydantic Settings from the environment.
# Provide placeholders so these unit tests run in a bare source checkout.
_required_env = {
    "DATABASE_URL": "postgresql+asyncpg://user:secret@localhost:5432/db",
    "JWT_SECRET": "test-secret",
    "CREDENTIAL_ENCRYPTION_KEY": "0123456789abcdef0123456789abcdef",
    "PASSWORD_RESET_URL": "http://localhost/reset",
    "BACKEND_CORS_ORIGINS": "http://localhost:5173",
    "RESEND_API_KEY": "test",
    "FROM_EMAIL": "test@example.com",
    "REDIS_URL": "redis://localhost:6379/0",
}
for _key, _value in _required_env.items():
    os.environ.setdefault(_key, _value)

sys.path.insert(0, REPO_ROOT)

from core.config import Settings  # noqa: E402


def _settings(**overrides):
    """Build a Settings instance from explicit values (ignores .env leakage)."""
    values = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "CREDENTIAL_ENCRYPTION_KEY": "0123456789abcdef0123456789abcdef",
        "JWT_SECRET": "test-secret",
        "BACKEND_CORS_ORIGINS": "http://localhost:5173",
        "PASSWORD_RESET_URL": "http://localhost/reset",
        "DATA_DELETION_URL": "http://localhost/delete-confirm",
        "RESEND_API_KEY": "test",
        "FROM_EMAIL": "test@example.com",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class MissingOptionalSettingsTests(unittest.TestCase):
    """A half-configured deploy must boot and name the gap, not crash."""

    def test_fully_configured_reports_nothing(self):
        self.assertEqual(_settings().missing_optional_settings(), {})

    def test_unset_single_feature_vars_are_reported_not_fatal(self):
        # This exact combination used to raise ValidationError at import time.
        missing = _settings(
            JWT_SECRET="",
            BACKEND_CORS_ORIGINS="",
            PASSWORD_RESET_URL="",
            DATA_DELETION_URL="",
            RESEND_API_KEY="",
            FROM_EMAIL="",
        ).missing_optional_settings()
        self.assertEqual(
            sorted(missing),
            [
                "BACKEND_CORS_ORIGINS",
                "DATA_DELETION_URL",
                "FROM_EMAIL",
                "JWT_SECRET",
                "PASSWORD_RESET_URL",
                "RESEND_API_KEY",
            ],
        )
        # Every reported name must come with the consequence, so the log line
        # is actionable rather than just a list of names.
        for name, effect in missing.items():
            self.assertTrue(effect, f"{name} has no explanation attached")

    def test_legacy_jwt_alias_satisfies_the_check(self):
        # docker-compose sets JWT_SECRET_KEY; that must not be flagged.
        settings = _settings(JWT_SECRET="", JWT_SECRET_KEY="legacy-secret")
        self.assertEqual(settings.JWT_SECRET, "legacy-secret")
        self.assertNotIn("JWT_SECRET", settings.missing_optional_settings())

    def test_hard_required_vars_are_still_required(self):
        # DATABASE_URL / REDIS_URL genuinely cannot be defaulted — the engine
        # and the Celery broker are built from them.
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            _settings(DATABASE_URL=None)


class HealthEndpointTests(unittest.TestCase):
    """GET /health is what the platform probes, so it must never need the DB."""

    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient

            import main
        except Exception as exc:  # pragma: no cover - optional heavy imports
            raise unittest.SkipTest(f"cannot import the API app: {exc}")
        # No `with TestClient(...)` on purpose: entering the context manager
        # runs the lifespan (init_db + Alembic migrations). The probe has to
        # answer without any of that, which is the whole point.
        cls.client = TestClient(main.app)

    def test_returns_200_without_touching_the_database(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["service"], "link-easy")
        self.assertIn("uptime_seconds", body)
        self.assertIn("writable", body["profile_storage"])
        self.assertIsInstance(body["missing_configuration"], list)

    def test_reports_the_profile_directory_it_resolved(self):
        from core.config import settings

        body = self.client.get("/health").json()
        self.assertEqual(body["profile_storage"]["path"], settings.PROFILE_STORAGE_DIR)

    def test_railway_healthcheck_path_points_at_a_real_route(self):
        # Guards against the config and the app drifting apart again — a
        # healthcheckPath that 404s fails every deploy.
        with open(os.path.join(REPO_ROOT, "railway.json"), encoding="utf-8") as fh:
            config = json.load(fh)
        path = config["deploy"]["healthcheckPath"]
        response = self.client.get(path)
        self.assertEqual(
            response.status_code,
            200,
            f"railway.json healthcheckPath {path!r} did not return 200",
        )


class StartShPreflightTests(unittest.TestCase):
    """Run the shipped entrypoint and assert on its preflight decisions."""

    def _run(self, env, timeout=60):
        base = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            # Nothing to supervise: the cases below are decided before any
            # child process is started, and this keeps the test free of a
            # database, Redis and a browser.
            "RUN_WEB": "0",
            "RUN_WORKER": "0",
            "RUN_BEAT": "0",
        }
        base.update(env)
        # cwd is a throwaway directory, not the checkout: start.sh also honours
        # a local .env (that is how config is supplied when it is run outside
        # the image), and a developer's .env must not decide these assertions.
        with tempfile.TemporaryDirectory() as cwd:
            return subprocess.run(
                ["bash", START_SH],
                cwd=cwd,
                env=base,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

    def test_missing_required_variable_is_named_and_fails_fast(self):
        # Previously this died inside uvicorn as a pydantic ValidationError and
        # the platform reported only "Healthcheck failure" five minutes later.
        result = self._run(
            {
                "REDIS_URL": "redis://localhost:6379/0",
                "CREDENTIAL_ENCRYPTION_KEY": "0123456789abcdef0123456789abcdef",
                "PROFILE_STORAGE_DIR": tempfile.mkdtemp(),
            }
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("required configuration is missing", result.stdout)
        self.assertIn("DATABASE_URL", result.stdout)

    def test_every_required_variable_present_passes_the_config_gate(self):
        result = self._run(
            {
                "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
                "REDIS_URL": "redis://localhost:6379/0",
                "CREDENTIAL_ENCRYPTION_KEY": "0123456789abcdef0123456789abcdef",
                "JWT_SECRET": "test-secret",
                "BACKEND_CORS_ORIGINS": "http://localhost:5173",
                "PASSWORD_RESET_URL": "http://localhost/reset",
                "RESEND_API_KEY": "test",
                "FROM_EMAIL": "test@example.com",
                "PROFILE_STORAGE_DIR": tempfile.mkdtemp(),
            }
        )
        self.assertNotIn("required configuration is missing", result.stdout)
        self.assertNotIn("optional configuration missing", result.stdout)

    def test_optional_variable_warns_without_aborting(self):
        result = self._run(
            {
                "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
                "REDIS_URL": "redis://localhost:6379/0",
                "CREDENTIAL_ENCRYPTION_KEY": "0123456789abcdef0123456789abcdef",
                "PROFILE_STORAGE_DIR": tempfile.mkdtemp(),
            }
        )
        self.assertNotIn("required configuration is missing", result.stdout)
        self.assertIn("optional configuration missing", result.stdout)
        self.assertIn("BACKEND_CORS_ORIGINS", result.stdout)

    def test_unwritable_profile_dir_falls_back_instead_of_aborting(self):
        """The Railway volume case: root-owned mount, non-root container."""
        if os.name == "nt" or os.geteuid() == 0:
            self.skipTest("chmod-based read-only dirs are ineffective for root/Windows")
        with tempfile.TemporaryDirectory() as tmp:
            blocked = os.path.join(tmp, "profiles")
            fallback = os.path.join(tmp, "fallback")
            os.mkdir(blocked)
            os.chmod(blocked, 0o500)  # r-x: mkdir/touch fail, exactly like root:root 755
            try:
                result = self._run(
                    {
                        "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
                        "REDIS_URL": "redis://localhost:6379/0",
                        "CREDENTIAL_ENCRYPTION_KEY": "0123456789abcdef0123456789abcdef",
                        "PROFILE_STORAGE_DIR": blocked,
                        "PROFILE_FALLBACK_DIR": fallback,
                    }
                )
            finally:
                os.chmod(blocked, 0o700)

            # The old behaviour was `exit 1` on the unwritable directory, which
            # is what failed the healthcheck. The only FATAL we expect now is
            # the deliberate "nothing to run" from the RUN_* setup above.
            self.assertNotIn("FATAL: neither", result.stdout)
            self.assertNotIn("FATAL: cannot create", result.stdout)
            self.assertIn("is not writable by uid", result.stdout)
            self.assertIn("Falling back to", result.stdout)
            self.assertIn(fallback, result.stdout)
            self.assertIn("RAILWAY_RUN_UID=0", result.stdout)
            self.assertIn("nothing to run", result.stdout)
            self.assertTrue(
                os.path.isdir(fallback),
                "the fallback profile directory was not created",
            )

    def test_writable_profile_dir_is_used_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles = os.path.join(tmp, "profiles")
            result = self._run(
                {
                    "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
                    "REDIS_URL": "redis://localhost:6379/0",
                    "CREDENTIAL_ENCRYPTION_KEY": "0123456789abcdef0123456789abcdef",
                    "PROFILE_STORAGE_DIR": profiles,
                }
            )
            self.assertNotIn("Falling back to", result.stdout)
            self.assertIn(f"profiles     : {profiles}", result.stdout)


if __name__ == "__main__":
    unittest.main()
