"""add feed leads pool and lead source metadata

Revision ID: d4a1c7b8e920
Revises: c6e2a20f5c20
Create Date: 2026-08-06 00:00:00.000000

Two related changes:

1. ``feed_leads`` — the Feed Leads pool.  Profiles saved from Feed Scroll scan
   results are staged here (one pool per feed scroll job) until the user
   imports a selection of them from a campaign's "Feed Leads" tab.  Nothing
   here duplicates the leads pathway: an imported entry is marked consumed and
   points at the campaign lead it became.

2. ``leads`` provenance columns — ``source`` ("manual" | "csv_import" |
   "job_feed_scan") plus the feed-scan analytics fields (``source_post_url``,
   ``matched_score``, ``matched_criteria``, ``scan_id``).  Existing rows keep a
   NULL source; the UI renders those as "—".

Both steps are idempotent so the startup migration runner can re-apply them
safely on databases that were created with ``Base.metadata.create_all()``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4a1c7b8e920"
down_revision: Union[str, Sequence[str], None] = "c6e2a20f5c20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FEED_LEADS_TABLE = "feed_leads"
LEADS_TABLE = "leads"

LEAD_SOURCE_COLUMNS = [
    sa.Column("source", sa.String(), nullable=True),
    sa.Column("source_post_url", sa.String(), nullable=True),
    sa.Column("matched_score", sa.Float(), nullable=True),
    sa.Column("matched_criteria", sa.JSON(), nullable=True),
    sa.Column("scan_id", sa.String(), nullable=True),
]


def _has_table(bind, table_name: str) -> bool:
    return sa.inspect(bind).has_table(table_name)


def _columns(bind, table_name: str) -> set[str]:
    if not _has_table(bind, table_name):
        return set()
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def _indexes(bind, table_name: str) -> set[str]:
    if not _has_table(bind, table_name):
        return set()
    return {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}


def _create_postgres_enum(bind) -> None:
    """Create the ``feed_lead_status`` enum type; ignore it if it already exists."""
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $$
        BEGIN
            CREATE TYPE feed_lead_status AS ENUM ('saved', 'imported');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )


def _status_type(bind):
    """Postgres gets the real enum; other dialects (tests) fall back to text."""
    if bind.dialect.name == "postgresql":
        return sa.Enum("saved", "imported", name="feed_lead_status", create_type=False)
    return sa.String()


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. Feed Leads pool ────────────────────────────────────────────────
    _create_postgres_enum(bind)

    if not _has_table(bind, FEED_LEADS_TABLE):
        op.create_table(
            FEED_LEADS_TABLE,
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("owner_email", sa.String(), nullable=False),
            sa.Column("feed_scroll_job_id", sa.String(), nullable=False),
            sa.Column("feed_scroll_result_id", sa.String(), nullable=True),
            sa.Column("linkedin_url", sa.String(), nullable=False),
            sa.Column("first_name", sa.String(), nullable=True),
            sa.Column("last_name", sa.String(), nullable=True),
            sa.Column("headline", sa.String(), nullable=True),
            sa.Column("label", sa.String(), nullable=True),
            sa.Column("source", sa.String(), nullable=False, server_default="job_feed_scan"),
            sa.Column("source_post_url", sa.String(), nullable=True),
            sa.Column("matched_score", sa.Float(), nullable=True),
            sa.Column("matched_criteria", sa.JSON(), nullable=True),
            sa.Column("scan_id", sa.String(), nullable=True),
            sa.Column("status", _status_type(bind), nullable=False, server_default="saved"),
            sa.Column("imported_campaign_id", sa.String(), nullable=True),
            sa.Column("imported_lead_id", sa.String(), nullable=True),
            sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["feed_scroll_job_id"], ["feed_scroll_jobs.id"], ondelete="CASCADE"
            ),
        )

    existing_indexes = _indexes(bind, FEED_LEADS_TABLE)
    if _has_table(bind, FEED_LEADS_TABLE):
        if "ix_feed_leads_owner_email" not in existing_indexes:
            op.create_index("ix_feed_leads_owner_email", FEED_LEADS_TABLE, ["owner_email"])
        if "ix_feed_leads_feed_scroll_job_id" not in existing_indexes:
            op.create_index(
                "ix_feed_leads_feed_scroll_job_id", FEED_LEADS_TABLE, ["feed_scroll_job_id"]
            )
        if "ix_feed_leads_job_url" not in existing_indexes:
            op.create_index(
                "ix_feed_leads_job_url", FEED_LEADS_TABLE, ["feed_scroll_job_id", "linkedin_url"]
            )

    # ── 2. Lead provenance columns ────────────────────────────────────────
    if _has_table(bind, LEADS_TABLE):
        lead_columns = _columns(bind, LEADS_TABLE)
        for column in LEAD_SOURCE_COLUMNS:
            if column.name not in lead_columns:
                op.add_column(LEADS_TABLE, column)


def downgrade() -> None:
    bind = op.get_bind()

    lead_columns = _columns(bind, LEADS_TABLE)
    for column in LEAD_SOURCE_COLUMNS:
        if column.name in lead_columns:
            op.drop_column(LEADS_TABLE, column.name)

    if _has_table(bind, FEED_LEADS_TABLE):
        op.drop_table(FEED_LEADS_TABLE)
        if bind.dialect.name == "postgresql":
            op.execute("DROP TYPE IF EXISTS feed_lead_status")
