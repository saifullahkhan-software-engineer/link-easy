"""
WhatsApp Job Scanner — SQLAlchemy models.
FILE: models/whatsapp.py

Tables:
  whatsapp_sessions          — persisted WhatsApp Web login sessions
  whatsapp_monitored_groups  — groups being monitored for job posts
  whatsapp_forward_group     — target group for forwarding matched messages
  whatsapp_raw_messages      — scraped messages with match scores
  whatsapp_scan_filters      — user-defined search filters for matching
"""
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    Boolean,
)
from sqlalchemy.sql import func
from database import Base


class WhatsAppSession(Base):
    __tablename__ = "whatsapp_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cookies_json = Column(JSON, nullable=True)
    storage_state_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    is_active = Column(Boolean, nullable=False, default=True)
    status = Column(
        String, nullable=False, default="disconnected"
    )  # disconnected | waiting_qr | connected | error


class WhatsAppMonitoredGroup(Base):
    __tablename__ = "whatsapp_monitored_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # A group configuration belongs to one filter job.  ``NULL`` keeps the
    # original singleton scanner configuration working for existing installs.
    filter_id = Column(Integer, ForeignKey("whatsapp_scan_filters.id", ondelete="CASCADE"), nullable=True)
    group_name = Column(String, nullable=False)
    whatsapp_id = Column(String, nullable=True)  # WhatsApp internal group ID (e.g. g.us chat id)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Track the last message timestamp we've seen so we only scrape new messages.
    last_message_timestamp = Column(String, nullable=True)
    last_message_id = Column(String, nullable=True)


class WhatsAppForwardGroup(Base):
    __tablename__ = "whatsapp_forward_group"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # See ``WhatsAppMonitoredGroup.filter_id``.  Legacy rows may be NULL.
    filter_id = Column(Integer, ForeignKey("whatsapp_scan_filters.id", ondelete="CASCADE"), nullable=True)
    group_name = Column(String, nullable=False)
    whatsapp_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class WhatsAppRawMessage(Base):
    __tablename__ = "whatsapp_raw_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Messages are kept per filter so each filter's results and counters are
    # isolated.  Existing singleton-scanner rows remain NULL.
    filter_id = Column(Integer, ForeignKey("whatsapp_scan_filters.id", ondelete="CASCADE"), nullable=True)
    group_id = Column(Integer, nullable=False)
    sender_name = Column(String, nullable=True)
    message_text = Column(Text, nullable=True)
    ocr_text = Column(Text, nullable=True)
    message_type = Column(String, nullable=False, default="text")  # text | image
    match_score = Column(Float, nullable=True)
    status = Column(String, nullable=False, default="pending")  # pending | matched | rejected | ocr_failed
    forwarded = Column(Boolean, nullable=False, default=False)
    forwarded_at = Column(DateTime(timezone=True), nullable=True)
    raw_image_bytes = Column(Text, nullable=True)  # base64-encoded image bytes
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ocr_failed = Column(Boolean, nullable=False, default=False)
    # WhatsApp internal message ID for dedup
    whatsapp_message_id = Column(String, nullable=True)


class WhatsAppScanFilter(Base):
    __tablename__ = "whatsapp_scan_filters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Filter jobs are user-owned, just like Feed Scroll jobs.  Nullable keeps
    # rows created by the original singleton scanner valid until they are
    # edited from the new workflow.
    owner_email = Column(String, ForeignKey("users.email", ondelete="CASCADE"), nullable=True)
    name = Column(String, nullable=False, default="WhatsApp Filter", server_default="WhatsApp Filter")
    # draft is used for a newly-created filter; active/paused control the
    # scheduler.  This is intentionally a String (rather than a database enum)
    # so old installations can migrate without creating a second enum type.
    status = Column(String, nullable=False, default="draft", server_default="draft")
    role = Column(String, nullable=True)
    job_title = Column(String, nullable=True)
    keywords = Column(JSON, nullable=True)  # ["python", "react", "remote"]
    experience_level = Column(String, nullable=True)  # entry | mid | senior
    match_threshold = Column(Float, nullable=False, default=60.0)
    # Minimum delay between automatic WhatsApp scans.
    interval_hours = Column(Float, nullable=False, default=1.0)
    last_scan_at = Column(DateTime(timezone=True), nullable=True)
    # Scheduling fields mirror FeedScrollJob so pause/resume survives reloads.
    remaining_seconds = Column(Integer, nullable=True)
    next_scan_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
