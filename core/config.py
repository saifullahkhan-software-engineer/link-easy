
import os
from typing import ClassVar

from pydantic_settings import BaseSettings
from pydantic import Field, model_validator


def normalize_database_url(url: str) -> str:
    """Ensure Postgres URLs use the asyncpg driver SQLAlchemy's async engine needs.

    Hosted platforms hand out plain ``postgresql://`` (or legacy Heroku-style
    ``postgres://``) connection strings — e.g. Railway's raw
    ``${{Postgres.DATABASE_URL}}`` reference.  ``create_async_engine`` refuses
    those ("The asyncio extension requires an async driver"), so they are
    rewritten to ``postgresql+asyncpg://``.  URLs that already carry a driver
    (``postgresql+asyncpg://``) and non-Postgres URLs (``sqlite+aiosqlite://``)
    pass through untouched.
    """
    for plain in ("postgresql://", "postgres://"):
        if url.startswith(plain):
            return "postgresql+asyncpg://" + url[len(plain):]
    return url


class Settings(BaseSettings):
    DATABASE_URL: str
    DEBUG: bool = False
    # "development" | "production" | "deployment".
    #
    # "deployment" labels the public hosted instance. It no longer reduces
    # functionality: campaigns, feed scans and the schedulers run on every
    # environment by default (a hosted user must be able to start the
    # campaigns and feed jobs they create). ENVIRONMENT is now used only to
    # surface the hosted-instance banner; feature switches are controlled
    # individually below (LINKEDIN_ENABLED, SCHEDULED_JOBS_ENABLED) so any
    # deployment — hosted or self-hosted — can opt a capability in or out.
    ENVIRONMENT: str = "production"
    # It's crucial to set a strong, secret key in your environment.
    # You can generate one with: openssl rand -hex 32
    # Accepts both JWT_SECRET (canonical) and JWT_SECRET_KEY (docker-compose legacy alias)
    JWT_SECRET: str = ""  # type: ignore
    JWT_SECRET_KEY: str | None = None  # legacy alias used in docker-compose.yml
    # 32-byte hex key for AES-256-GCM LinkedIn credential encryption.
    # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    # Accepts both CREDENTIAL_ENCRYPTION_KEY and ENCRYPTION_KEY
    CREDENTIAL_ENCRYPTION_KEY: str = ""  # type: ignore
    ENCRYPTION_KEY: str | None = None  # legacy alias
    JWT_ALGORITHM: str = "HS256"
    # A login is one fixed session.  Access tokens can still be shorter-lived
    # and refreshed, but neither token may keep the session alive past this
    # absolute deadline.  Two hours is the product default.
    SESSION_EXPIRE_MINUTES: int = 120
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    # Kept as an upper bound for backwards-compatible deployments that tune
    # refresh-token lifetime.  ``SESSION_EXPIRE_MINUTES`` always wins when it
    # is shorter (which it is by default).
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30
    # The four settings below are used by exactly one feature each (password
    # reset email, browser CORS, transactional email). They used to be
    # *required*, and because `settings` is constructed at import time that
    # meant one unset Railway variable raised a pydantic ValidationError while
    # importing `main` — uvicorn never bound $PORT, so the platform reported a
    # generic "Network › Healthcheck" failure minutes later with the real
    # cause scrolled off in the deploy log. They now default to "" and are
    # surfaced by `missing_optional_settings()` instead, so the service boots
    # and tells you what is unset.
    PASSWORD_RESET_URL: str = ""
    BACKEND_CORS_ORIGINS: str = ""

    # Email settings
    RESEND_API_KEY: str = ""
    FROM_EMAIL: str = ""

    # Redis settings
    REDIS_URL: str

    @model_validator(mode="after")
    def _resolve_legacy_aliases(self):
        # Accept both postgresql:// and postgres+asyncpg:// style URLs so a raw
        # Railway/Heroku DATABASE_URL reference works without manual editing.
        self.DATABASE_URL = normalize_database_url(self.DATABASE_URL)
        # JWT_SECRET <- JWT_SECRET_KEY fallback (docker-compose uses JWT_SECRET_KEY)
        if not self.JWT_SECRET and self.JWT_SECRET_KEY:
            self.JWT_SECRET = self.JWT_SECRET_KEY
        # CREDENTIAL_ENCRYPTION_KEY <- ENCRYPTION_KEY fallback
        if not self.CREDENTIAL_ENCRYPTION_KEY and self.ENCRYPTION_KEY:
            self.CREDENTIAL_ENCRYPTION_KEY = self.ENCRYPTION_KEY
        return self

    # Root directory for durable per-account Chromium profile directories.
    # Each LinkedInAccount gets <PROFILE_STORAGE_DIR>/<account.id> as its
    # user-data-dir. Mount this path on a persistent volume in production.
    # The Docker image overrides this source-tree default with /app/profiles;
    # local runs continue to use ./profiles unless explicitly configured.
    PROFILE_STORAGE_DIR: str = "./profiles"

    # WhatsApp automation pacing (seconds) between forwarding each matched
    # message. Sending several messages at the same time trips WhatsApp's
    # spam/blocking filter, so the scanner waits this long between forwards.
    # Override with the WHATSAPP_FORWARD_DELAY_SECONDS env var.
    WHATSAPP_FORWARD_DELAY_SECONDS: float = 10.0

    # Hard-enforce admin-only APIs. Left false during bootstrap so the first
    # admin can be assigned through the UI without being locked out; the
    # frontend already hides admin surfaces from non-admins. Flip to true
    # (ADMIN_API_ENFORCED=true) once roles are assigned to make every admin
    # endpoint return 403 for non-admins.
    ADMIN_API_ENFORCED: bool = False

    # Postgres-backed API rate limiting. Redis on this deployment is busy with
    # Celery job traffic, so limits live in the database instead.
    RATE_LIMIT_ENABLED: bool = True

    # ── LinkedIn availability ────────────────────────────────────────────────
    # LinkedIn automation is ENABLED by default on every environment, including
    # the hosted deployment: users connect their LinkedIn account, create
    # campaigns, add leads and create feed-scan jobs, and they must be able to
    # START those campaigns and jobs from the same instance. The gate
    # (require_linkedin_enabled) stays in place purely as a kill switch — an
    # operator can set LINKEDIN_ENABLED=false to take the LinkedIn surfaces
    # down instantly (they return 503 with LINKEDIN_DISABLED_MESSAGE) without
    # a code change, for example if LinkedIn starts broadly challenging the
    # host's IP range. Residential per-account proxies
    # (LinkedInAccount.proxy_*, read by automation/browser.py) remain the
    # recommended setup for hosted IPs.
    LINKEDIN_ENABLED: bool = True

    # Shown to users wherever LinkedIn is gated (only when LINKEDIN_ENABLED is
    # explicitly turned off). Overridable so the message can be retuned
    # without a redeploy.
    LINKEDIN_DISABLED_MESSAGE: str = (
        "LinkedIn automation is temporarily unavailable on this instance. "
        "Please try again shortly or contact support — WhatsApp automation is "
        "fully available in the meantime."
    )

    # ── Hosted instance (ENVIRONMENT=deployment) ─────────────────────────────
    # Contact address shown in the hosted-instance banner. Users who would
    # rather run LinkEasy on their own machine/IP are told to email for help
    # with the setup. Env-overridable so it can change without a redeploy.
    SUPPORT_EMAIL: str = "saifullahkhanofficial1@gmail.com"

    # Master switch for unattended, timer-driven work: Celery Beat's three
    # dispatchers (due campaign steps, recurring feed scans, recurring
    # WhatsApp scans) and the API's interval scheduling.
    #
    # Default None = ON for every environment. Started campaigns must keep
    # processing their later steps and activated feed-scan jobs must keep
    # recurring on the hosted instance, and that work is dispatched from the
    # database-backed schedule by Beat. Set SCHEDULED_JOBS_ENABLED=false
    # explicitly to switch the timers off on a given deployment (on-demand
    # actions the user clicks are unaffected — the Celery worker still runs,
    # so manual scans, live chat and connects all work; only the *timers*
    # are removed). Beat itself is a lightweight publisher; it only wakes a
    # browser when real due work exists.
    SCHEDULED_JOBS_ENABLED_OVERRIDE: bool | None = Field(
        default=None, alias="SCHEDULED_JOBS_ENABLED"
    )

    # ── Social post scheduler (YouTube Shorts / Instagram Reels / TikTok) ────
    # OAuth app credentials for each platform. A platform whose client id /
    # key is empty is reported as "not configured" by
    # GET /api/v1/social-scheduler/platforms and its connect button is
    # disabled in the UI; nothing else breaks. Redirect URIs must match the
    # ones registered in each developer console exactly and point at THIS
    # API's callback routes (the UI is redirected onwards from there).
    YOUTUBE_CLIENT_ID: str = ""
    YOUTUBE_CLIENT_SECRET: str = ""
    YOUTUBE_REDIRECT_URI: str = ""
    INSTAGRAM_APP_ID: str = ""
    INSTAGRAM_APP_SECRET: str = ""
    INSTAGRAM_REDIRECT_URI: str = ""
    TIKTOK_CLIENT_KEY: str = ""
    TIKTOK_CLIENT_SECRET: str = ""
    TIKTOK_REDIRECT_URI: str = ""
    FACEBOOK_APP_ID: str = ""
    FACEBOOK_APP_SECRET: str = ""
    FACEBOOK_REDIRECT_URI: str = ""

    # Where uploaded videos are stored. Files are served back under
    # /uploads/social/<generated-name> (a mount in main.py) because Instagram
    # downloads the video from a public URL rather than accepting an upload —
    # so in production this must be a durable volume AND the API must be
    # reachable at PUBLIC_API_URL. Defaults next to the profile storage so a
    # single volume mount covers both.
    UPLOAD_DIR: str = "./uploads/social"
    # Per-file upload cap in bytes (default 500 MB — Instagram's own ceiling is
    # ~650 MB and TikTok's 287 MB, so anything larger can't be published anyway).
    MAX_UPLOAD_SIZE: int = 500 * 1024 * 1024
    # Public base URL of this API (no trailing slash), used to build the
    # absolute video URL handed to Instagram and, when a platform's
    # *_REDIRECT_URI is unset, the default OAuth callback URL. Empty → the
    # request's own origin is used for callbacks and Instagram publishing
    # fails with an actionable error.
    PUBLIC_API_URL: str = ""
    # Where the browser is sent after an OAuth callback completes (the
    # frontend's settings page). Empty → derived from the first CORS origin.
    SOCIAL_OAUTH_RETURN_URL: str = ""

    @property
    def is_deployment(self) -> bool:
        """True on the public hosted instance (ENVIRONMENT=deployment)."""
        return self.ENVIRONMENT.strip().lower() == "deployment"

    @property
    def scheduled_jobs_enabled(self) -> bool:
        """Whether the Celery scheduler is enabled at all.

        This remains enabled in development and production by default. The
        hosted demo uses the same Beat process, but its Beat schedule is
        deliberately narrowed to social uploads (see ``worker.celery_app``).
        """
        if self.SCHEDULED_JOBS_ENABLED_OVERRIDE is not None:
            return self.SCHEDULED_JOBS_ENABLED_OVERRIDE
        return True

    @property
    def whatsapp_scheduled_jobs_enabled(self) -> bool:
        """Whether recurring WhatsApp filters may be armed.

        WhatsApp connection, chat and an explicitly requested scan are
        on-demand operations and remain available on the hosted demo. Only
        the recurring scanner is paused there to avoid putting browser load on
        Celery. Local/development behavior is unchanged.
        """
        return self.scheduled_jobs_enabled and not self.is_deployment

    @property
    def deployment_notice(self) -> str | None:
        """Banner copy for the hosted instance, or None when not in that mode."""
        if not self.is_deployment:
            return None
        return (
            "You're on the hosted LinkEasy instance. Campaigns, feed scans "
            "and WhatsApp automation all run here, including scheduled and "
            "recurring jobs. Prefer to run it on your own machine, where the "
            "browser uses your own IP? LinkEasy is self-hostable — email "
            f"{self.SUPPORT_EMAIL} and we'll help you set it up."
        )

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.BACKEND_CORS_ORIGINS.split(",")
            if origin.strip()
        ]

    @property
    def social_oauth_return_url(self) -> str:
        """Frontend page the browser lands on after a platform OAuth callback.

        Explicit SOCIAL_OAUTH_RETURN_URL wins; otherwise the first CORS origin
        (the frontend) + the settings page path. Falls back to a relative path
        so a same-origin dev proxy setup still works.
        """
        if self.SOCIAL_OAUTH_RETURN_URL:
            return self.SOCIAL_OAUTH_RETURN_URL.rstrip("/")
        origins = self.cors_origins
        base = origins[0].rstrip("/") if origins else ""
        return f"{base}/app/social-scheduler/settings"

    def social_platform_configured(self, platform: str) -> bool:
        """True when the OAuth app credentials for ``platform`` are set."""
        return {
            "youtube": bool(self.YOUTUBE_CLIENT_ID and self.YOUTUBE_CLIENT_SECRET),
            "instagram": bool(self.INSTAGRAM_APP_ID and self.INSTAGRAM_APP_SECRET),
            "tiktok": bool(self.TIKTOK_CLIENT_KEY and self.TIKTOK_CLIENT_SECRET),
            "facebook": bool(self.FACEBOOK_APP_ID and self.FACEBOOK_APP_SECRET),
        }.get(platform, False)

    #: name -> what breaks when it is left unset. Used to warn at startup and
    #: to report the gap over GET /health, so a half-configured deployment is
    #: diagnosable without reading the deploy log. ClassVar, not a field:
    #: pydantic must not treat it as an environment-backed setting.
    OPTIONAL_SETTING_EFFECTS: ClassVar[dict[str, str]] = {
        "JWT_SECRET": "login tokens cannot be signed or verified — authentication fails",
        "BACKEND_CORS_ORIGINS": "browsers block every API call from the frontend (CORS)",
        "PASSWORD_RESET_URL": "password-reset emails carry no link back to the app",
        "RESEND_API_KEY": "no transactional email is sent (Resend key missing)",
        "FROM_EMAIL": "no transactional email is sent (sender address missing)",
    }

    def missing_optional_settings(self) -> dict[str, str]:
        """Unset-but-important settings, mapped to what they break.

        JWT_SECRET is reported through its legacy alias too, so a deployment
        that sets JWT_SECRET_KEY (docker-compose) is not flagged.
        """
        missing: dict[str, str] = {}
        for name, effect in self.OPTIONAL_SETTING_EFFECTS.items():
            if name == "JWT_SECRET":
                if self.JWT_SECRET:
                    continue
            elif getattr(self, name, ""):
                continue
            missing[name] = effect
        return missing

    class Config:
        env_file = ".env"


settings = Settings()
