"""Regression tests for the fixed two-hour authentication session.

Access tokens may be refreshed during a session, but refresh-token rotation
must never move the absolute session deadline forward.  The same deadline is
also checked by the API dependency so the server cannot be kept alive by a
client that bypasses the frontend timer.
"""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test")

import unittest

from fastapi import HTTPException
from jose import jwt

from api.dependencies import get_current_user_from_token
from core.config import settings
from core.security import create_access_token, create_refresh_token


class AuthSessionTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.subject = {
            "sub": "user@example.com",
            "role": "customer",
            "roles": ["customer"],
        }

    def _decode_without_expiry_check(self, token):
        return jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_exp": False},
        )

    def test_default_session_and_access_lifetimes_are_two_hours(self):
        self.assertEqual(settings.SESSION_EXPIRE_MINUTES, 120)
        self.assertEqual(settings.ACCESS_TOKEN_EXPIRE_MINUTES, 120)

    def test_access_and_refresh_tokens_share_the_absolute_deadline(self):
        access = self._decode_without_expiry_check(create_access_token(self.subject))
        refresh = self._decode_without_expiry_check(create_refresh_token(self.subject))

        self.assertEqual(access["session_expires_at"], refresh["session_expires_at"])
        self.assertEqual(access["exp"] - access["iat"], 2 * 60 * 60)
        self.assertEqual(refresh["exp"] - refresh["iat"], 2 * 60 * 60)

    def test_refresh_rotation_preserves_the_original_deadline(self):
        first = self._decode_without_expiry_check(create_refresh_token(self.subject))
        original_deadline = datetime.fromtimestamp(
            first["session_expires_at"], tz=timezone.utc
        )

        rotated = self._decode_without_expiry_check(
            create_refresh_token(self.subject, original_deadline)
        )

        self.assertEqual(rotated["session_expires_at"], first["session_expires_at"])
        self.assertLessEqual(rotated["exp"], first["session_expires_at"])

    def test_server_rejects_an_expired_session_claim(self):
        token = jwt.encode(
            {
                **self.subject,
                "token_type": "access",
                "iat": datetime.now(timezone.utc),
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
                "session_expires_at": int(
                    (datetime.now(timezone.utc) - timedelta(seconds=1)).timestamp()
                ),
            },
            settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )

        with self.assertRaises(HTTPException) as context:
            import asyncio

            asyncio.run(get_current_user_from_token(token, object()))

        self.assertEqual(context.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
