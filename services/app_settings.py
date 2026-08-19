"""
Admin-editable application settings.

FILE: services/app_settings.py

Campaign parameters, job limits, and rate-limit windows live in the
``app_settings`` table so the developer/admin can retune them from the
dashboard without a redeploy.

Values are stored as text plus a ``value_type`` discriminator, so one table
holds ints, floats, booleans, and strings without a migration per knob.
Every read is coerced back to its declared Python type.

Guard rails: each key declares an allowed range. A limit that exceeds the
hard automation caps in ``worker/rate_limit.py`` would get an account blocked
by LinkedIn, so writes are clamped/rejected rather than trusted blindly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from models.rbac import AppSetting

logger = get_logger(__name__)


@dataclass(frozen=True)
class SettingSpec:
    """Definition of one admin-editable knob."""

    key: str
    default: Any
    value_type: str  # int | float | bool | str
    category: str
    description: str
    minimum: Optional[float] = None
    maximum: Optional[float] = None


# ── Registry ─────────────────────────────────────────────────────────────────
# Campaign ceilings mirror worker/rate_limit.py HARD_CAPS. Those caps are the
# absolute ceiling LinkedIn tolerates; admin settings may lower them but must
# never raise them above the cap, so a misconfiguration cannot get an account
# suspended.
SETTING_SPECS: tuple[SettingSpec, ...] = (
    # Campaign parameters
    SettingSpec(
        "campaign.daily_connection_limit", 15, "int", "campaign",
        "Connection requests per account per day", 0, 15,
    ),
    SettingSpec(
        "campaign.daily_message_limit", 20, "int", "campaign",
        "Direct messages per account per day", 0, 20,
    ),
    SettingSpec(
        "campaign.daily_visit_limit", 80, "int", "campaign",
        "Profile visits per account per day", 0, 80,
    ),
    SettingSpec(
        "campaign.daily_like_limit", 30, "int", "campaign",
        "Post likes per account per day", 0, 30,
    ),
    SettingSpec(
        "campaign.min_delay_seconds", 45.0, "float", "campaign",
        "Minimum human-like pause between actions", 5, 600,
    ),
    SettingSpec(
        "campaign.max_delay_seconds", 180.0, "float", "campaign",
        "Maximum human-like pause between actions", 5, 3600,
    ),
    # Job limits
    SettingSpec(
        "jobs.max_actions_per_session", 20, "int", "jobs",
        "Actions allowed in a single browser session", 1, 100,
    ),
    SettingSpec(
        "jobs.max_concurrent_browsers", 2, "int", "jobs",
        "Chromium processes allowed at once", 1, 10,
    ),
    SettingSpec(
        "jobs.whatsapp_forward_delay_seconds", 10.0, "float", "jobs",
        "Pause between WhatsApp forwards (anti-block)", 1, 300,
    ),
    SettingSpec(
        "jobs.feed_scroll_max_posts", 40, "int", "jobs",
        "Posts inspected per feed scan", 1, 200,
    ),
    # Rate limiting — overrides services/rate_limiter.py defaults.
    SettingSpec(
        "rate_limit.auth:login.max_requests", 10, "int", "rate_limit",
        "Login attempts allowed per window", 1, 1000,
    ),
    SettingSpec(
        "rate_limit.auth:login.window_seconds", 300, "int", "rate_limit",
        "Login rate-limit window (seconds)", 10, 86400,
    ),
    SettingSpec(
        "rate_limit.profile:scan.max_requests", 20, "int", "rate_limit",
        "Profile scans allowed per window", 1, 1000,
    ),
    SettingSpec(
        "rate_limit.profile:scan.window_seconds", 3600, "int", "rate_limit",
        "Profile scan rate-limit window (seconds)", 10, 86400,
    ),
)

SPECS_BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in SETTING_SPECS}


def coerce(value: Any, value_type: str) -> Any:
    """Convert a stored text value back to its declared Python type."""
    if value is None:
        return None
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    if value_type == "bool":
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return str(value)


def serialize(value: Any, value_type: str) -> str:
    if value_type == "bool":
        return "true" if value else "false"
    return str(value)


def validate(spec: SettingSpec, value: Any) -> Any:
    """Coerce and range-check a value, raising ValueError when out of bounds."""
    try:
        coerced = coerce(value, spec.value_type)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{spec.key} must be a {spec.value_type}") from exc

    if spec.value_type in {"int", "float"}:
        if spec.minimum is not None and coerced < spec.minimum:
            raise ValueError(f"{spec.key} must be at least {spec.minimum:g}")
        if spec.maximum is not None and coerced > spec.maximum:
            # The ceiling exists so a typo cannot push an account past the
            # platform limits that get accounts suspended.
            raise ValueError(
                f"{spec.key} must be at most {spec.maximum:g} — higher values "
                "risk the account being flagged or suspended"
            )
    return coerced


async def get_settings_map(
    db: AsyncSession, category: Optional[str] = None
) -> dict[str, Any]:
    """Return ``{key: typed value}`` merging defaults with stored overrides."""
    specs = [
        spec
        for spec in SETTING_SPECS
        if category is None or spec.category == category
    ]
    values: dict[str, Any] = {spec.key: spec.default for spec in specs}

    query = select(AppSetting)
    if category is not None:
        query = query.where(AppSetting.category == category)

    try:
        rows = (await db.execute(query)).scalars().all()
    except Exception as exc:
        # Before the migration runs the table may not exist yet — defaults
        # keep the app fully functional.
        logger.debug("app_settings unavailable, using defaults: %s", exc)
        return values

    for row in rows:
        spec = SPECS_BY_KEY.get(row.key)
        if spec is None:
            continue
        try:
            values[spec.key] = coerce(row.value, spec.value_type)
        except (TypeError, ValueError):
            logger.warning("Ignoring malformed app_setting %s=%r", row.key, row.value)
    return values


async def get_setting(db: AsyncSession, key: str) -> Any:
    """Read one setting, falling back to its default."""
    spec = SPECS_BY_KEY.get(key)
    if spec is None:
        raise KeyError(f"Unknown setting: {key}")
    row = (
        await db.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalars().first()
    if row is None:
        return spec.default
    try:
        return coerce(row.value, spec.value_type)
    except (TypeError, ValueError):
        return spec.default


async def set_settings(
    db: AsyncSession, updates: dict[str, Any], updated_by: Optional[str] = None
) -> dict[str, Any]:
    """Validate and persist several settings, returning the new values."""
    unknown = sorted(set(updates) - set(SPECS_BY_KEY))
    if unknown:
        raise ValueError(f"Unknown setting(s): {', '.join(unknown)}")

    validated: dict[str, Any] = {
        key: validate(SPECS_BY_KEY[key], value) for key, value in updates.items()
    }

    # Cross-field rule: an inverted delay range would make pacing nonsensical.
    merged = {**await get_settings_map(db), **validated}
    if merged["campaign.min_delay_seconds"] > merged["campaign.max_delay_seconds"]:
        raise ValueError(
            "campaign.min_delay_seconds cannot be greater than "
            "campaign.max_delay_seconds"
        )

    for key, value in validated.items():
        spec = SPECS_BY_KEY[key]
        row = (
            await db.execute(select(AppSetting).where(AppSetting.key == key))
        ).scalars().first()
        if row is None:
            db.add(
                AppSetting(
                    key=key,
                    value=serialize(value, spec.value_type),
                    value_type=spec.value_type,
                    category=spec.category,
                    description=spec.description,
                    updated_by=updated_by,
                )
            )
        else:
            row.value = serialize(value, spec.value_type)
            row.value_type = spec.value_type
            row.category = spec.category
            row.description = spec.description
            row.updated_by = updated_by

    await db.commit()
    logger.info(
        "⚙️  %s updated %d setting(s): %s",
        updated_by or "system",
        len(validated),
        ", ".join(sorted(validated)),
    )
    return validated


def describe_settings(values: dict[str, Any]) -> list[dict[str, Any]]:
    """Shape the registry + current values for the admin UI."""
    return [
        {
            "key": spec.key,
            "value": values.get(spec.key, spec.default),
            "default": spec.default,
            "value_type": spec.value_type,
            "category": spec.category,
            "description": spec.description,
            "minimum": spec.minimum,
            "maximum": spec.maximum,
        }
        for spec in SETTING_SPECS
    ]
