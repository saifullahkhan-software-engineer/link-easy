
import os

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
    # "deployment" is the public hosted demo. It is deliberately a REDUCED
    # mode, not just a label: the free tier has one small container and no
    # residential proxies, so anything that runs unattended on a timer is
    # switched off (see SCHEDULED_JOBS_ENABLED below) and the UI tells people
    # to run the app locally for the full feature set.
    #
    # Anything that is NOT "deployment" — including the default "production" —
    # behaves exactly as before, so self-hosting and local development are
    # unaffected.
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
    PASSWORD_RESET_URL: str
    BACKEND_CORS_ORIGINS: str

    # Email settings
    RESEND_API_KEY: str
    FROM_EMAIL: str

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
    # LinkedIn automation is DISABLED by default because it is not viable from
    # a datacenter IP. Driving the sign-in form (or reusing a session) from a
    # hosted platform such as Railway makes LinkedIn serve a CAPTCHA or a
    # /checkpoint/challenge on a large share of attempts, so accounts either
    # fail to connect or get challenged mid-campaign. The fix is one sticky
    # residential proxy per account (the LinkedInAccount.proxy_* columns,
    # already read by automation/browser.py) — that costs money, so it is
    # deferred until the product is monetised or has enough users to justify
    # it.
    #
    # Until then the connect/verify endpoints return 503 with a clear
    # explanation and the UI shows a "coming soon" notice instead of a form
    # that cannot succeed. WhatsApp is unaffected and stays fully available.
    #
    # Flip to true (LINKEDIN_ENABLED=true) once proxies are provisioned — no
    # code change required.
    LINKEDIN_ENABLED: bool = False

    # Shown to users wherever LinkedIn is gated. Overridable so the message can
    # be retuned (e.g. with an ETA) without a redeploy.
    LINKEDIN_DISABLED_MESSAGE: str = (
        "LinkedIn automation is temporarily unavailable. LinkedIn blocks "
        "sign-ins from datacenter IP addresses, so each account needs its own "
        "residential proxy to run reliably. We're adding proxy support as the "
        "product grows — WhatsApp automation is fully available in the "
        "meantime."
    )

    # ── Hosted-demo (deployment) mode ────────────────────────────────────────
    # Contact address shown in the hosted-demo banner. Users who want the full
    # feature set are told to run LinkEasy locally and to email for help with
    # the setup. Env-overridable so it can change without a redeploy.
    SUPPORT_EMAIL: str = "saifullahkhanofficial1@gmail.com"

    # Master switch for unattended, timer-driven work: Celery Beat's three
    # dispatchers (due campaign steps, recurring feed scans, recurring
    # WhatsApp scans) and the API's interval scheduling.
    #
    # Default None = "decide from ENVIRONMENT": off in deployment mode, on
    # everywhere else. Set it explicitly (true/false) to override that.
    #
    # Why it is off on the hosted demo: every timed job wakes a ~500 MB
    # Chromium in a container that also serves the API, and it does so with
    # nobody watching. On the free tier that means OOM-kills and half-finished
    # sessions. On-demand actions the user clicks are unaffected — the Celery
    # worker still runs, so manual WhatsApp scans, live chat and connects all
    # work; only the *timers* are removed.
    SCHEDULED_JOBS_ENABLED_OVERRIDE: bool | None = Field(
        default=None, alias="SCHEDULED_JOBS_ENABLED"
    )

    @property
    def is_deployment(self) -> bool:
        """True on the public hosted demo (ENVIRONMENT=deployment)."""
        return self.ENVIRONMENT.strip().lower() == "deployment"

    @property
    def scheduled_jobs_enabled(self) -> bool:
        """Whether timer-driven background work may run.

        Explicit SCHEDULED_JOBS_ENABLED wins; otherwise it is off in
        deployment mode and on everywhere else (development, production,
        self-hosted), so existing installs keep their schedulers.
        """
        if self.SCHEDULED_JOBS_ENABLED_OVERRIDE is not None:
            return self.SCHEDULED_JOBS_ENABLED_OVERRIDE
        return not self.is_deployment

    @property
    def deployment_notice(self) -> str | None:
        """Banner copy for the hosted demo, or None when not in that mode."""
        if not self.is_deployment:
            return None
        return (
            "You're on the hosted demo. Scheduled campaigns and recurring "
            "scans are turned off here, and LinkedIn automation needs a "
            "residential proxy we don't run yet. LinkEasy works best on your "
            "own machine, where everything is enabled and the browser runs on "
            f"your own IP. Email {self.SUPPORT_EMAIL} and we'll help you set "
            "it up."
        )

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.BACKEND_CORS_ORIGINS.split(",")
            if origin.strip()
        ]

    class Config:
        env_file = ".env"


settings = Settings()
