"""
Lead model — one row per LinkedIn profile being targeted by a campaign.
FILE: models/lead.py
"""
import enum
from sqlalchemy import Column, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.sql import func
from database import Base


# How a lead entered the campaign.  Every pathway writes the same leads table;
# this only records provenance so the UI/analytics can tell them apart.
class LeadSource(str, enum.Enum):
    MANUAL = "manual"            # "Add manually" form
    CSV_IMPORT = "csv_import"    # CSV bulk upload
    JOB_FEED_SCAN = "job_feed_scan"  # Saved from a Feed Scroll scan result
 
 
class LeadStatus(str, enum.Enum):
    PENDING     = "pending"      # Not yet contacted
    VISITING    = "visiting"     # Profile visited / post liked
    REQUESTED   = "requested"    # Connection request sent
    ACCEPTED    = "accepted"     # Connection accepted
    MESSAGED    = "messaged"     # Message sent
    REPLIED     = "replied"      # Lead replied
    SKIPPED     = "skipped"      # Skipped (over limit, CAPTCHA, etc.)
    FAILED      = "failed"       # Action failed after retries
    COMPLETE    = "complete"     # All campaign steps completed
 
 
class Lead(Base):
    __tablename__ = "leads"
 
    id             = Column(String, primary_key=True)   # UUID
    campaign_id    = Column(String, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
 
    linkedin_url   = Column(String, nullable=False)     # Full profile URL
    first_name     = Column(String, nullable=True)
    last_name      = Column(String, nullable=True)
    headline       = Column(String, nullable=True)
 
    status         = Column(SAEnum(LeadStatus, name="lead_status",
                            values_callable=lambda enum_cls: [e.value for e in enum_cls]),
                            nullable=False, default=LeadStatus.PENDING)
    current_step   = Column(Integer, default=0)         # Which step was last executed
 
    connection_sent_at = Column(DateTime(timezone=True), nullable=True)
    accepted_at        = Column(DateTime(timezone=True), nullable=True)
    completed_at       = Column(DateTime(timezone=True), nullable=True)  # When all steps completed
    last_action_at     = Column(DateTime(timezone=True), nullable=True)
    next_action_at     = Column(DateTime(timezone=True), nullable=True)
 
    notes          = Column(Text, nullable=True)        # Admin / debug notes

    # ── Provenance ────────────────────────────────────────────────────────
    # Where this lead came from ("manual" | "csv_import" | "job_feed_scan").
    # Legacy rows created before this column existed stay NULL.
    source           = Column(String, nullable=True)
    # Feed-scan only: the post the profile was matched on, its relevance score
    # and the criteria that matched, plus the scan batch it belonged to.
    source_post_url  = Column(String, nullable=True)
    matched_score    = Column(Float, nullable=True)
    matched_criteria = Column(JSON, nullable=True)
    scan_id          = Column(String, nullable=True)

    created_at     = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
