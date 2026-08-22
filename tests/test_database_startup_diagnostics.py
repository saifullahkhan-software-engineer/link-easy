"""
Unit tests for the database startup hardening added after the Railway
crash-loop incident (socket.gaierror: [Errno -2] Name or service not known).

Covers:

  * ``normalize_database_url`` rewrites plain postgresql:// / postgres:// URLs
    (as handed out by Railway's ${{Postgres.DATABASE_URL}} reference) to the
    postgresql+asyncpg:// scheme the async engine requires, and leaves
    already-async / non-Postgres URLs untouched
  * ``Settings`` applies that normalization on load
  * ``diagnose_connection_error`` turns the raw DNS/connection failure into a
    short actionable message that names the Railway fix
  * ``_is_connectivity_error`` classifies which failures are worth retrying
  * ``database_target`` masks credentials before logging
"""
import os
import socket
import sys
import unittest

# Importing database -> models -> core.config builds pydantic Settings from the
# environment.  Provide minimal placeholders so these unit tests run in a
# lightweight source checkout without a .env file.
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import normalize_database_url  # noqa: E402


class TestNormalizeDatabaseUrl(unittest.TestCase):
    def test_plain_postgresql_gets_asyncpg_driver(self):
        self.assertEqual(
            normalize_database_url("postgresql://postgres:pw@containers-us-west-1.railway.app:6543/railway"),
            "postgresql+asyncpg://postgres:pw@containers-us-west-1.railway.app:6543/railway",
        )

    def test_legacy_postgres_scheme_gets_asyncpg_driver(self):
        self.assertEqual(
            normalize_database_url("postgres://u:p@db.example.com:5432/mydb"),
            "postgresql+asyncpg://u:p@db.example.com:5432/mydb",
        )

    def test_query_string_is_preserved(self):
        self.assertEqual(
            normalize_database_url("postgresql://u:p@host:5432/db?sslmode=require"),
            "postgresql+asyncpg://u:p@host:5432/db?sslmode=require",
        )

    def test_already_async_url_untouched(self):
        url = "postgresql+asyncpg://linkeflow:pw@postgres:5432/linkeflow"
        self.assertEqual(normalize_database_url(url), url)

    def test_sqlite_url_untouched(self):
        url = "sqlite+aiosqlite:///:memory:"
        self.assertEqual(normalize_database_url(url), url)


class TestSettingsNormalization(unittest.TestCase):
    def test_settings_rewrites_plain_postgres_url(self):
        from core.config import Settings

        settings = Settings(
            DATABASE_URL="postgresql://u:p@containers-us-west-1.railway.app:7432/railway",
            PASSWORD_RESET_URL="http://localhost/reset",
            BACKEND_CORS_ORIGINS="http://localhost:5173",
            RESEND_API_KEY="test",
            FROM_EMAIL="test@example.com",
            REDIS_URL="redis://localhost:6379/0",
        )
        self.assertTrue(settings.DATABASE_URL.startswith("postgresql+asyncpg://"))


class TestConnectionDiagnostics(unittest.TestCase):
    def test_dns_failure_mentions_railway_reference(self):
        from database import diagnose_connection_error

        # Reproduce the exact production failure: asyncpg raises gaierror deep
        # inside SQLAlchemy's wrapped connect call.
        gaierror = socket.gaierror(-2, "Name or service not known")
        wrapped = OSError("wrapped", gaierror)
        wrapped.__cause__ = gaierror
        message = diagnose_connection_error(wrapped)
        self.assertIn("DNS lookup failed", message)
        self.assertIn("DATABASE_URL", message)
        self.assertIn("${{Postgres.DATABASE_URL}}", message)
        # Never leak the password in diagnostics.
        self.assertNotIn("secret", message)

    def test_connection_refused_hint(self):
        from database import diagnose_connection_error

        message = diagnose_connection_error(
            ConnectionRefusedError(111, "Connection refused")
        )
        self.assertIn("Connection refused", message)
        self.assertIn("nothing is listening", message)

    def test_unknown_error_falls_back_to_exception_text(self):
        from database import diagnose_connection_error

        message = diagnose_connection_error(ValueError("boom"))
        self.assertIn("ValueError", message)
        self.assertIn("boom", message)

    def test_connectivity_errors_are_retryable(self):
        from database import _is_connectivity_error
        from sqlalchemy.exc import OperationalError

        gaierror = socket.gaierror(-2, "Name or service not known")
        op = OperationalError("statement", {}, gaierror)
        self.assertTrue(_is_connectivity_error(op))
        self.assertFalse(_is_connectivity_error(ValueError("boom")))

    def test_database_target_masks_password(self):
        from database import database_target

        target = database_target()
        self.assertIn("host=localhost", target)
        self.assertIn("port=5432", target)
        self.assertNotIn("secret", target)


if __name__ == "__main__":
    unittest.main()
