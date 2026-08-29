"""Regression tests: the Celery worker/beat sync Postgres driver.

The incident
------------
Both ``celery worker`` and ``celery beat`` crash-looped on every deploy with::

    File "/app/worker/tasks/campaign_tasks.py", line 84, in <module>
      _engine = create_engine(_sync_url, pool_pre_ping=True)
    ...
    File ".../sqlalchemy/dialects/postgresql/psycopg2.py", line 697, in import_dbapi
      import psycopg2
  ModuleNotFoundError: No module named 'psycopg2'

``start.sh`` supervises all three processes and deliberately takes the whole
container down when any child dies, so a missing worker dependency also killed
the healthy API — the deploy log showed the API booting, running migrations,
reporting "application is ready", and then being SIGTERM'd seconds later, on
repeat.

Why it was not caught earlier
-----------------------------
Two independent facts have to line up, and each looks fine on its own:

  * ``worker/tasks/*.py`` rewrite ``DATABASE_URL`` to ``postgresql+psycopg2://``
    and call ``create_engine()`` at **module import time**. SQLAlchemy resolves
    the DBAPI eagerly in ``create_engine`` (not lazily on first connect), so the
    import itself raises. Celery imports every task module during
    ``init_worker()``, i.e. before the worker can even start consuming.
  * ``requirements.txt`` pinned only ``asyncpg`` (async, used by the API and
    Alembic). Nothing installed a **sync** driver, and the API path never
    exercises one, so the API kept booting perfectly.

These tests pin down the contract that connects the two files.
"""
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS = os.path.join(REPO_ROOT, "requirements.txt")

sys.path.insert(0, REPO_ROOT)


def _requirement_names() -> dict:
    """Map of lowercased distribution name -> pinned version from requirements.txt."""
    names = {}
    with open(REQUIREMENTS, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^([A-Za-z0-9._-]+)\s*==\s*([^\s;]+)", line)
            if match:
                names[match.group(1).lower()] = match.group(2)
    return names


class SyncDriverIsDeclaredTests(unittest.TestCase):
    """requirements.txt must ship a sync driver for the psycopg2 dialect."""

    def test_a_sync_psycopg_driver_is_pinned(self):
        pinned = _requirement_names()
        self.assertTrue(
            {"psycopg2-binary", "psycopg2"} & set(pinned),
            "worker/tasks/*.py build 'postgresql+psycopg2://' URLs and call "
            "create_engine() at import time, so psycopg2 must be installed. "
            "Without it `celery worker` and `celery beat` die at startup with "
            "ModuleNotFoundError and start.sh takes the whole container down.",
        )

    def test_async_driver_is_still_pinned_for_the_api(self):
        # The API and Alembic use create_async_engine, which needs asyncpg.
        # Adding the sync driver must not replace it.
        self.assertIn("asyncpg", _requirement_names())

    def test_psycopg2_binary_version_has_python_314_wheels(self):
        """The image is python:3.14-slim, which needs cp314 wheels.

        psycopg2-binary only started publishing cp314 manylinux wheels in
        2.9.12. An older pin builds from source instead and fails the image
        build outright, because the slim base has no gcc/libpq-dev.
        """
        pinned = _requirement_names()
        version = pinned.get("psycopg2-binary")
        if version is None:
            self.skipTest("psycopg2-binary is not the chosen sync driver")

        def as_tuple(text):
            return tuple(int(part) for part in re.findall(r"\d+", text)[:3])

        self.assertGreaterEqual(
            as_tuple(version),
            (2, 9, 12),
            "psycopg2-binary < 2.9.12 has no cp314 wheel; pip would try to "
            "compile it inside python:3.14-slim, which has no compiler.",
        )


class SyncUrlRewriteTests(unittest.TestCase):
    """Every worker module must agree on the driver it asks SQLAlchemy for.

    If one of them ever rewrites to a different driver (e.g. ``psycopg`` v3),
    requirements.txt has to grow that dependency too — this test makes the
    mismatch visible in CI instead of at deploy time.
    """

    def _module_rewriters(self):
        from worker.tasks import campaign_tasks, feed_scroll_tasks, whatsapp_tasks

        return {
            "campaign_tasks": campaign_tasks._make_sync_url,
            "feed_scroll_tasks": feed_scroll_tasks._make_sync_url,
            "whatsapp_tasks": whatsapp_tasks._make_sync_url,
        }

    def setUp(self):
        # Importing the task modules constructs Settings and a real engine, so
        # supply the same placeholder config the other test modules use.
        for key, value in {
            "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
            "JWT_SECRET": "test-secret",
            "CREDENTIAL_ENCRYPTION_KEY": "0123456789abcdef0123456789abcdef"
            "0123456789abcdef0123456789abcdef",
            "PASSWORD_RESET_URL": "http://localhost/reset",
            "BACKEND_CORS_ORIGINS": "http://localhost:5173",
            "RESEND_API_KEY": "test",
            "FROM_EMAIL": "test@example.com",
            "REDIS_URL": "redis://localhost:6379/0",
        }.items():
            os.environ.setdefault(key, value)

    def test_async_urls_are_rewritten_to_the_installed_sync_driver(self):
        cases = [
            "postgresql+asyncpg://u:p@localhost:5432/db",
            "postgres+asyncpg://u:p@localhost:5432/db",
            "postgresql://u:p@localhost:5432/db",
        ]
        for name, rewrite in self._module_rewriters().items():
            for url in cases:
                with self.subTest(module=name, url=url):
                    self.assertTrue(
                        rewrite(url).startswith("postgresql+psycopg2://"),
                        f"{name} must target the psycopg2 driver that "
                        f"requirements.txt installs",
                    )

    def test_rewritten_url_builds_a_real_engine(self):
        """The exact failing line from the deploy log, exercised directly.

        ``create_engine`` imports the DBAPI eagerly, so this raises
        ModuleNotFoundError when the driver is absent — no database needed.
        """
        from sqlalchemy import create_engine

        for name, rewrite in self._module_rewriters().items():
            with self.subTest(module=name):
                url = rewrite("postgresql+asyncpg://u:p@localhost:5432/db")
                engine = create_engine(url, pool_pre_ping=True)
                self.assertEqual(engine.dialect.name, "postgresql")
                self.assertEqual(engine.dialect.driver, "psycopg2")
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
