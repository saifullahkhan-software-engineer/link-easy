"""Token handling shared by the social-scheduler API and the Celery worker.

OAuth tokens are persisted on ``SocialPlatformConnection`` as AES-256-GCM
ciphertext (``core.security.encrypt_credential``) and only ever decrypted
into a short-lived ``PlatformTokens`` value right before a platform call.
Both the async API (``AsyncSession``) and the sync worker (``Session``) use
these helpers so the encrypt/decrypt contract lives in exactly one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.security import decrypt_credential, encrypt_credential
from models.social_scheduler import SocialPlatformConnection

# A token this close to expiry is treated as expired so a long upload cannot
# outlive it midway through.
EXPIRY_SKEW = timedelta(minutes=5)


@dataclass
class PlatformTokens:
    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[datetime]

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return _aware(self.expires_at) <= datetime.now(timezone.utc) + EXPIRY_SKEW


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def expires_at_from(expires_in: Any) -> Optional[datetime]:
    """Absolute expiry from a platform's ``expires_in`` seconds (None if unknown)."""
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def apply_tokens(
    connection: SocialPlatformConnection,
    *,
    access_token: str,
    refresh_token: Optional[str],
    expires_in: Any = None,
    expires_at: Optional[datetime] = None,
) -> None:
    """Encrypt and store a fresh token set on ``connection`` (not committed)."""
    if not access_token:
        raise ValueError("The platform returned no access token")
    connection.encrypted_access_token = encrypt_credential(access_token)
    if refresh_token:
        connection.encrypted_refresh_token = encrypt_credential(refresh_token)
    # A renewal that omits the refresh token keeps the one already stored.
    connection.expires_at = expires_at if expires_at is not None else expires_at_from(expires_in)


def read_tokens(connection: SocialPlatformConnection) -> PlatformTokens:
    """Decrypt the stored tokens. Raises ValueError on a corrupt/foreign ciphertext."""
    try:
        access_token = decrypt_credential(connection.encrypted_access_token)
        refresh_token = (
            decrypt_credential(connection.encrypted_refresh_token)
            if connection.encrypted_refresh_token
            else None
        )
    except Exception as exc:  # wrong CREDENTIAL_ENCRYPTION_KEY, truncated value…
        raise ValueError(
            f"Stored {connection.platform} credentials cannot be decrypted "
            "(was CREDENTIAL_ENCRYPTION_KEY rotated?). Reconnect the account."
        ) from exc
    return PlatformTokens(access_token, refresh_token, connection.expires_at)


def reconnect_required(connection: SocialPlatformConnection) -> bool:
    """True when the token is (nearly) expired and cannot be renewed unattended.

    Instagram's long-lived token *can* be renewed without a refresh token
    while it is still valid, so only a past-expiry Instagram token counts.
    """
    if connection.expires_at is None:
        return False
    expired = _aware(connection.expires_at) <= datetime.now(timezone.utc) + EXPIRY_SKEW
    if not expired:
        return False
    return not connection.encrypted_refresh_token
