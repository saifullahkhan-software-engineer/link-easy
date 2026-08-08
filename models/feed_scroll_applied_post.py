"""
FeedScrollAppliedPost model.
FILE: models/feed_scroll_applied_post.py

Records posts marked as 'Applied' by the user.
Kept permanently in the database so repeated feed scans and filters
crossmatch against this table and never surface already-applied posts as duplicates.
"""
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.sql import func
from database import Base


class FeedScrollAppliedPost(Base):
    __tablename__ = "feed_scroll_applied_posts"

    id = Column(String, primary_key=True)  # UUID
    feed_scroll_job_id = Column(
        String,
        ForeignKey("feed_scroll_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_email = Column(String, nullable=False, index=True)

    post_urn = Column(String, nullable=True, index=True)
    post_url = Column(String, nullable=False)
    author_name = Column(String, nullable=True)
    author_first_name = Column(String, nullable=True)
    author_last_name = Column(String, nullable=True)
    author_profile_url = Column(String, nullable=False)
    connection_degree = Column(String, nullable=True)
    post_time = Column(String, nullable=True)
    post_text = Column(Text, nullable=True)

    score = Column(Float, nullable=False, default=0.0)
    matched_terms = Column(JSON, nullable=True)
    scan_batch_id = Column(String, nullable=True)

    applied_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
