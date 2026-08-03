"""
FeedScrollResult model.
FILE: models/feed_scroll_result.py

Stores one row per scored post found during a feed scan.
Each scan run produces up to N results grouped by scan_batch_id.
"""
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.sql import func
from database import Base


class FeedScrollResult(Base):
    __tablename__ = "feed_scroll_results"

    id = Column(String, primary_key=True)  # UUID
    feed_scroll_job_id = Column(String, ForeignKey("feed_scroll_jobs.id", ondelete="CASCADE"),
                                nullable=False, index=True)

    post_urn = Column(String, nullable=True, index=True)  # LinkedIn post URN for dedup
    post_url = Column(String, nullable=True)
    author_name = Column(String, nullable=True)
    post_text = Column(Text, nullable=True)

    score = Column(Float, nullable=False, default=0.0)
    matched_terms = Column(JSON, nullable=True)  # ["Software Engineer", "database design"]
    scan_batch_id = Column(String, nullable=False, index=True)  # Groups posts from same scan

    scanned_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
