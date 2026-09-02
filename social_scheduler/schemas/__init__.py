from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class PostCreate(BaseModel):
    title: str
    caption: str
    hashtags: str = ""
    video_path: str
    video_url: str
    thumbnail: str = ""
    platforms: List[str]  # ["youtube", "instagram", "tiktok"]
    scheduled_at: datetime
    youtube_title: str = ""
    instagram_caption: str = ""
    tiktok_caption: str = ""


class PostUpdate(BaseModel):
    title: Optional[str] = None
    caption: Optional[str] = None
    hashtags: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    status: Optional[str] = None
    youtube_title: Optional[str] = None
    instagram_caption: Optional[str] = None
    tiktok_caption: Optional[str] = None


class PostResult(BaseModel):
    id: str
    platform: str
    status: str
    platform_id: str
    platform_url: str
    error: str
    posted_at: Optional[datetime]

    class Config:
        from_attributes = True


class PostResponse(BaseModel):
    id: str
    title: str
    caption: str
    hashtags: str
    video_path: str
    video_url: str
    thumbnail: str
    platforms: List[str]
    scheduled_at: datetime
    status: str
    youtube_title: str
    instagram_caption: str
    tiktok_caption: str
    created_at: datetime
    updated_at: datetime
    results: List[PostResult]

    class Config:
        from_attributes = True


class PlatformConnectionCreate(BaseModel):
    platform: str  # youtube | instagram | tiktok
    access_token: str
    refresh_token: str = ""
    expires_at: Optional[datetime] = None
    account_name: str = ""
    account_id: str = ""
    extra_data: dict = {}


class PlatformConnectionResponse(BaseModel):
    id: str
    platform: str
    account_name: str
    account_id: str
    expires_at: Optional[datetime]
    updated_at: datetime

    class Config:
        from_attributes = True


class PlatformAuthUrlResponse(BaseModel):
    auth_url: str


class PlatformTokenExchange(BaseModel):
    code: str


class StatsResponse(BaseModel):
    scheduled_this_week: int
    total_published: int
    total_failed: int
    next_post_in: Optional[str]
    connected_platforms: List[str]
