"""
FeedScrollJob model.
FILE: models/feed_scroll_job.py

A FeedScrollJob belongs to one platform user and one LinkedIn account.
It stores the user's feed scanning configuration: mode (job_search or post_search),
search criteria, and scheduling interval.
"""
import enum
from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.sql import func
from database import Base


class FeedScrollMode(str, enum.Enum):
    JOB_SEARCH = "job_search"
    POST_SEARCH = "post_search"


class FeedScrollJobStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"


class FeedScrollJob(Base):
    __tablename__ = "feed_scroll_jobs"

    id = Column(String, primary_key=True)  # UUID
    account_email = Column(String, ForeignKey("linkedin_accounts.linkedin_email"), nullable=False)
    owner_email = Column(String, ForeignKey("users.email", ondelete="CASCADE"), nullable=False)

    name = Column(String, nullable=False)
    mode = Column(SAEnum(FeedScrollMode, name="feed_scroll_mode",
                         values_callable=lambda enum_cls: [e.value for e in enum_cls]),
                  nullable=False, default=FeedScrollMode.JOB_SEARCH)
    status = Column(SAEnum(FeedScrollJobStatus, name="feed_scroll_job_status",
                           values_callable=lambda enum_cls: [e.value for e in enum_cls]),
                    nullable=False, default=FeedScrollJobStatus.DRAFT)

    # ── Job Search criteria ──
    experience_min_years = Column(Integer, nullable=True)
    experience_max_years = Column(Integer, nullable=True)
    job_titles = Column(JSON, nullable=True)    # ["Software Engineer", "Python Developer"]
    skill_set = Column(JSON, nullable=True)      # ["database design", "development"]

    # ── Post Search criteria ──
    keywords = Column(JSON, nullable=True)       # ["AI", "machine learning"]

    # ── Scheduling ──
    feed_interval_hours = Column(Integer, nullable=False, default=1)
    posts_per_scan = Column(Integer, nullable=False, default=10)

    # ── Timestamps ──
    last_scanned_at = Column(DateTime(timezone=True), nullable=True)
    next_scan_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
