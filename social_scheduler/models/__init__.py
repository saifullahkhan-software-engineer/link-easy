from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import relationship
from database import Base
import uuid


def generate_uuid():
    return str(uuid.uuid4())


class Post(Base):
    __tablename__ = "social_posts"

    id = Column(String, primary_key=True, default=generate_uuid)
    title = Column(String, nullable=False)
    caption = Column(Text, nullable=False)
    hashtags = Column(Text, default="")
    video_path = Column(String, nullable=False)
    video_url = Column(String, nullable=False)
    thumbnail = Column(String, default="")
    platforms = Column(JSON)  # ["youtube", "instagram", "tiktok"]
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String, default="pending")  # pending | posting | posted | failed | cancelled
    youtube_title = Column(String, default="")
    instagram_caption = Column(String, default="")
    tiktok_caption = Column(String, default="")
    created_at = Column(DateTime, server_default="now()")
    updated_at = Column(DateTime, server_default="now()", onupdate="now()")
    
    results = relationship("PostResult", back_populates="post", cascade="all, delete-orphan")


class PostResult(Base):
    __tablename__ = "social_post_results"

    id = Column(String, primary_key=True, default=generate_uuid)
    post_id = Column(String, ForeignKey("social_posts.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String, nullable=False)  # youtube | instagram | tiktok
    status = Column(String, nullable=False)  # pending | success | failed
    platform_id = Column(String, default="")  # video ID from platform
    platform_url = Column(String, default="")  # link to posted video
    error = Column(Text, default="")
    posted_at = Column(DateTime, nullable=True)
    
    post = relationship("Post", back_populates="results")


class PlatformConnection(Base):
    __tablename__ = "social_platform_connections"

    id = Column(String, primary_key=True, default=generate_uuid)
    platform = Column(String, unique=True, nullable=False)  # youtube | instagram | tiktok
    access_token = Column(String, nullable=False)
    refresh_token = Column(String, default="")
    expires_at = Column(DateTime, nullable=True)
    account_name = Column(String, default="")
    account_id = Column(String, default="")
    extra_data = Column(JSON, default="")  # platform-specific data (e.g., Instagram page ID)
    updated_at = Column(DateTime, server_default="now()", onupdate="now()")
