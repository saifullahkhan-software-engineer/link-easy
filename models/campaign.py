"""
Campaign and CampaignStep models.
FILE: models/campaign.py
 
A Campaign belongs to one platform user (owner_email).
It targets LinkedIn profiles matching a saved search filter.
Steps define what action happens on which day of the drip sequence.
"""
import enum
from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, Boolean, JSON
from sqlalchemy.sql import func
from database import Base
 
 
class CampaignStatus(str, enum.Enum):
    DRAFT    = "draft"
    ACTIVE   = "active"
    PAUSED   = "paused"
    COMPLETE = "complete"
    FAILED   = "failed"
 
 
class CampaignStepType(str, enum.Enum):
    VISIT_PROFILE         = "visit_profile"
    LIKE_POST             = "like_post"
    VISIT_AND_LIKE        = "visit_and_like"
    SEND_CONNECTION       = "send_connection"
    SEND_MESSAGE          = "send_message"
    FOLLOW_UP_IF_PENDING  = "follow_up_if_pending"
    THANKS_IF_ACCEPTED    = "thanks_if_accepted"
 
 
class Campaign(Base):
    __tablename__ = "campaigns"
 
    id           = Column(String, primary_key=True)   # UUID set in API layer
    account_email= Column(String, ForeignKey("linkedin_accounts.linkedin_email"), nullable=False)
 
    name         = Column(String, nullable=False)
    description  = Column(Text, nullable=True)
    status       = Column(SAEnum(CampaignStatus, name="campaign_status"),
                          nullable=False, default=CampaignStatus.DRAFT)
 
    # LinkedIn search filters saved as JSON
    # Example: {"keywords": "CTO", "location": "New York", "industry": "Technology"}
    search_filters = Column(JSON, nullable=True)
 
    # Daily action limits for this campaign (overrides global defaults if set)
    daily_connection_limit = Column(Integer, default=15)
    daily_message_limit    = Column(Integer, default=20)
    daily_visit_limit      = Column(Integer, default=80)
 
    # Connection request note template (use {{first_name}} placeholder)
    connection_note_template = Column(Text, nullable=True)
    # Message templates for each step (stored as JSON list indexed by step order)
    message_templates = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
 
 
class CampaignStep(Base):
    """Defines the drip sequence for a campaign."""
    __tablename__ = "campaign_steps"
 
    id           = Column(String, primary_key=True)
    campaign_id  = Column(String, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
 
    step_order   = Column(Integer, nullable=False)   # 1, 2, 3, 4, 5
    step_type    = Column(SAEnum(CampaignStepType, name="campaign_step_type"), nullable=False)
 
    # Delay in hours AFTER the previous step before this step fires
    # Day 1 = 0, Day 2 = ~24, Day 3 = ~48, etc.
    delay_hours  = Column(Integer, nullable=False, default=0)
 
    # Only run this step if a condition is met (used for steps 4 and 5)
    # Values: null (always run), "not_accepted", "accepted"
    condition    = Column(String, nullable=True)
