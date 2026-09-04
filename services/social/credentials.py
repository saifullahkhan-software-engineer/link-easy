"""
Platform app credentials — a database row overrides the environment default.

The social scheduler's OAuth *app* credentials (the client id/key + secret the
deployment registered with each provider) usually come from the environment
(``YOUTUBE_CLIENT_ID`` … in ``core.config.Settings``). The ``platform_credentials``
table lets an operator enter them from the settings page instead: a stored row
overrides the environment pair for that platform, and deleting the row falls
straight back to the environment values.

Why not just edit the env vars?
    The hosted instance's operator is a logged-in app user and cannot touch
    the deployment's environment; storing app credentials in the database lets
    them run the whole Meta/Google/TikTok setup from the settings UI.

Provider field names
--------------------
Each platform spells its identifier and secret differently and each service
class exposes them under its own attribute names:

    youtube   → client_id   / client_secret   (YouTubeService.client_id …)
    instagram → app_id      / app_secret      (InstagramService.app_id …)
    tiktok    → client_key  / client_secret   (TikTokService.client_key …)
    facebook  → app_id      / app_secret      (FacebookService.app_id …)

The table stores the pair under two generic columns (``client_id`` /
``client_secret``); only this module knows the per-platform names, so the
providers' vocabulary never leaks into the schema.

Sync + async variants
---------------------
The API (``AsyncSession``) and the Celery worker (sync ``Session``) both build
services and both need the same effective credentials, so every resolver here
has a ``*_sync`` sibling.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from models.social_scheduler import PlatformCredential

logger = logging.getLogger(__name__)


# platform -> {"identifier": <attr/JSON name>, "secret": <attr/JSON name>}.
# The attribute names are exactly what the provider services read/write.
SERVICE_ATTRS: dict[str, dict[str, str]] = {
    "youtube": {"identifier": "client_id", "secret": "client_secret"},
    "instagram": {"identifier": "app_id", "secret": "app_secret"},
    "facebook": {"identifier": "app_id", "secret": "app_secret"},
    "tiktok": {"identifier": "client_key", "secret": "client_secret"},
}

# platform -> (env var for the identifier, env var for the secret). The env
# var names match Settings field names exactly (getattr(settings, name)).
ENV_VARS: dict[str, tuple[str, str]] = {
    "youtube": ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"),
    "instagram": ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"),
    "facebook": ("FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET"),
    "tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"),
}


def _strip(value: Optional[str]) -> str:
    return (value or "").strip()


def credential_field_names(platform: str) -> tuple[str, str]:
    """Return the (identifier, secret) JSON/attribute names for a platform.

    Raises KeyError for unknown platforms — callers validate against
    ``PLATFORM_VALUES`` first (``_platform_or_404`` in the API).
    """
    pair = SERVICE_ATTRS[platform]
    return pair["identifier"], pair["secret"]


def env_credentials(platform: str) -> tuple[str, str]:
    """The environment credential pair for ``platform`` (stripped)."""
    id_var, secret_var = ENV_VARS[platform]
    return _strip(getattr(settings, id_var)), _strip(getattr(settings, secret_var))


def platform_env_configured(platform: str) -> bool:
    """True when the environment alone provides a full credential pair."""
    identifier, secret = env_credentials(platform)
    return bool(identifier and secret)


# ── Effective credentials (async) ────────────────────────────────────────────


async def load_credential(
    db: AsyncSession, platform: str
) -> Optional[PlatformCredential]:
    """The stored DB row for ``platform``, or None when none is saved."""
    result = await db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == platform)
    )
    return result.scalars().first()


def effective_credentials(
    platform: str, row: Optional[PlatformCredential]
) -> dict[str, str]:
    """Resolve the credential pair the deployment should actually use.

    A non-empty stored value overrides the environment value field by field,
    so a row that only fills in the secret (or an env that fills in the rest)
    still works. The returned dict is keyed by the platform's provider field
    names plus a ``source`` key ("environment" | "database" | "none").
    """
    identifier_field, secret_field = credential_field_names(platform)
    env_identifier, env_secret = env_credentials(platform)

    stored_identifier = _strip(row.client_id) if row is not None else ""
    stored_secret = _strip(row.client_secret) if row is not None else ""

    identifier = stored_identifier or env_identifier
    secret = stored_secret or env_secret

    if stored_identifier and stored_secret:
        source = "database"
    elif stored_identifier or stored_secret:
        source = "database" if (identifier and secret) else "none"
    elif env_identifier and env_secret:
        source = "environment"
    else:
        source = "none"

    return {
        identifier_field: identifier,
        secret_field: secret,
        "source": source,
    }


def configured_from_credentials(platform: str, creds: dict[str, str]) -> bool:
    """True when an effective pair has both fields filled."""
    identifier_field, secret_field = credential_field_names(platform)
    return bool(creds.get(identifier_field) and creds.get(secret_field))


async def platform_configured(db: AsyncSession, platform: str) -> bool:
    """True when ``platform`` has a usable credential pair (DB row or env)."""
    row = await load_credential(db, platform)
    return configured_from_credentials(platform, effective_credentials(platform, row))


def apply_credentials(service, platform: str, creds: dict[str, str]) -> None:
    """Copy the effective credential pair onto a provider service instance.

    The provider services read their credentials once in ``__init__`` from
    ``settings``; after a DB override exists the caller builds the service and
    calls this so the same instance talks to the provider with the right app.
    """
    identifier_field, secret_field = credential_field_names(platform)
    setattr(service, identifier_field, creds.get(identifier_field, ""))
    setattr(service, secret_field, creds.get(secret_field, ""))


async def upsert_credentials(
    db: AsyncSession, platform: str, identifier: str, secret: str
) -> PlatformCredential:
    """Save (or replace) the stored credential pair for ``platform``."""
    row = await load_credential(db, platform)
    if row is None:
        row = PlatformCredential(platform=platform)
        db.add(row)
    row.client_id = _strip(identifier)
    row.client_secret = _strip(secret)
    await db.commit()
    return row


async def delete_credentials(db: AsyncSession, platform: str) -> bool:
    """Remove the stored row for ``platform``. Returns True when one existed."""
    row = await load_credential(db, platform)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


# ── Sync variants (Celery worker sessions) ───────────────────────────────────


def load_credential_sync(db, platform: str) -> Optional[PlatformCredential]:
    """Sync twin of :func:`load_credential` for the Celery worker."""
    return (
        db.query(PlatformCredential)
        .filter(PlatformCredential.platform == platform)
        .one_or_none()
    )


def platform_configured_sync(db, platform: str) -> bool:
    """Sync twin of :func:`platform_configured` for the Celery worker."""
    row = load_credential_sync(db, platform)
    return configured_from_credentials(platform, effective_credentials(platform, row))


def apply_credentials_sync(db, platform: str, service) -> None:
    """Sync twin of :func:`apply_credentials` (loads the row from ``db``).

    Used by the worker right after ``get_service(platform)`` so token refresh
    and publishing authenticate with operator-set DB credentials too.
    """
    row = load_credential_sync(db, platform)
    apply_credentials(service, platform, effective_credentials(platform, row))
