from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:saif1234@localhost:5432/nexusflow_dev"
    
    # Redis
    REDIS_URL: str = "redis://default:vyp4A4zIJ9kdMuYEGOLaxR93WbUcKytw@pastel-heartfelt-sack-16346.db.redis.io:19935"
    
    # App
    APP_NAME: str = "Social Scheduler"
    APP_URL: str = "http://localhost:3000"
    
    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:3001"]
    
    # YouTube
    YOUTUBE_CLIENT_ID: str = ""
    YOUTUBE_CLIENT_SECRET: str = ""
    YOUTUBE_REDIRECT_URI: str = "http://localhost:8000/api/platforms/youtube/callback"
    
    # Instagram
    INSTAGRAM_APP_ID: str = ""
    INSTAGRAM_APP_SECRET: str = ""
    INSTAGRAM_REDIRECT_URI: str = "http://localhost:8000/api/platforms/instagram/callback"
    
    # TikTok
    TIKTOK_CLIENT_KEY: str = ""
    TIKTOK_CLIENT_SECRET: str = ""
    TIKTOK_REDIRECT_URI: str = "http://localhost:8000/api/platforms/tiktok/callback"
    
    # File Upload
    UPLOAD_DIR: str = "public/uploads"
    MAX_UPLOAD_SIZE: int = 500 * 1024 * 1024  # 500MB
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
