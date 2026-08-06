"""
FeedLead model — the "Feed Leads" pool.
FILE: models/feed_lead.py

A feed lead is a LinkedIn profile the user saved from a scored post in the
Feed Scroll results view.  Saving does **not** create a campaign lead: the
profile is staged in a per-feed-scroll-job pool ("list") until the user picks
it up from the "Feed Leads" tab of a campaign, at which point it is inserted
into the shared ``leads`` table like any CSV/manual lead and the pool entry is
consumed (marked imported so the pool empties as it is used).

The scan/scoring metadata travels with the profile so analytics can tell later
which leads came from a Feed Scroll match.
"""
import enum

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    JSON,
    String,
)
from sqlalchemy.sql import func

from database import Base

# Marks every profile that entered the system through a feed scan.  Stored on
# both the pool row and the campaign lead it eventually becomes.
FEED_LEAD_SOURCE = "job_feed_scan"


class FeedLeadStatus(str, enum.Enum):
    SAVED = "saved"        # Sitting in the pool, waiting to be used
    IMPORTED = "imported"  # Already pulled into a campaign (consumed)


class FeedLead(Base):
    __tablename__ = "feed_leads"

    id = Column(String, primary_key=True)  # UUID
    owner_email = Column(String, nullable=False, index=True)

    # The pool this profile belongs to: one pool per feed scroll job.
    feed_scroll_job_id = Column(
        String,
        ForeignKey("feed_scroll_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The scored post row the profile was saved from (kept for traceability;
    # results can be re-scored/removed, so this is intentionally not a FK).
    feed_scroll_result_id = Column(String, nullable=True)

    # Profile fields — identical shape/validation to CSV + manual lead import.
    linkedin_url = Column(String, nullable=False)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    headline = Column(String, nullable=True)

    # Optional user label typed in the save popover ("Backend hires", ...).
    label = Column(String, nullable=True)

    # Source metadata (not shown on the post card, stored for analytics).
    source = Column(String, nullable=False, default=FEED_LEAD_SOURCE)
    source_post_url = Column(String, nullable=True)
    matched_score = Column(Float, nullable=True)
    matched_criteria = Column(JSON, nullable=True)  # ["Software Engineer", ...]
    scan_id = Column(String, nullable=True)         # feed_scroll_results.scan_batch_id

    status = Column(
        SAEnum(
            FeedLeadStatus,
            name="feed_lead_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=FeedLeadStatus.SAVED,
    )

    # Set when the pool entry is consumed by a campaign import.
    imported_campaign_id = Column(String, nullable=True)
    imported_lead_id = Column(String, nullable=True)
    imported_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# Fast "is this post's author already in the pool?" lookups when the results
# page paints saved/added states for a whole scan at once.
Index("ix_feed_leads_job_url", FeedLead.feed_scroll_job_id, FeedLead.linkedin_url)
