"""
CampaignJob — one row per Celery task execution.
This is the audit log for every action NexusFlow takes on LinkedIn.
FILE: models/campaign_job.py
"""
import enum
from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, String, Text
from sqlalchemy.sql import func
from database import Base
 
 
class JobStatus(str, enum.Enum):
    QUEUED    = "queued"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    SKIPPED   = "skipped"
 
 
class CampaignJob(Base):
    __tablename__ = "campaign_jobs"
 
    id              = Column(String, primary_key=True)
    campaign_id     = Column(String, ForeignKey("campaigns.id"), nullable=False, index=True)
    lead_id         = Column(String, ForeignKey("leads.id"), nullable=False, index=True)
    step_type       = Column(String, nullable=False)
 
    celery_task_id  = Column(String, nullable=True)     # Celery task UUID for tracking
    status          = Column(SAEnum(JobStatus, name="job_status",
                             values_callable=lambda enum_cls: [e.value for e in enum_cls]),
                             nullable=False, default=JobStatus.QUEUED)
 
    error_message   = Column(Text, nullable=True)       # Store failure reason
    scheduled_at    = Column(DateTime(timezone=True), nullable=True)
    started_at      = Column(DateTime(timezone=True), nullable=True)
    completed_at    = Column(DateTime(timezone=True), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
