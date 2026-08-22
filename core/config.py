
import os

from pydantic_settings import BaseSettings
from pydantic import model_validator


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
    ENVIRONMENT: str = "production"  # "development" or "production"
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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
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
