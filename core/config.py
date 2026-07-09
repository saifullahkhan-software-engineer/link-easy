
import os

from pydantic_settings import BaseSettings
from pydantic import model_validator


class Settings(BaseSettings):
    DATABASE_URL: str
    DEBUG: bool = False
    ENVIRONMENT: str = "production"  # "development" or "production"
    # It's crucial to set a strong, secret key in your environment.
    # You can generate one with: openssl rand -hex 32
    JWT_SECRET: str
    # 32-byte hex key for AES-256-GCM LinkedIn credential encryption.
    # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    CREDENTIAL_ENCRYPTION_KEY: str
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
