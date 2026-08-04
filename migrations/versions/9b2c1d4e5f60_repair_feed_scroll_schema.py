"""repair feed scroll schema

Revision ID: 9b2c1d4e5f60
Revises: 5f7e70839c37
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9b2c1d4e5f60"
down_revision: Union[str, Sequence[str], None] = "5f7e70839c37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JOB_TABLE = "feed_scroll_jobs"
RESULT_TABLE = "feed_scroll_results"


def _table_exists(bind, table_name: str) -> bool:
    return sa.inspect(bind).has_table(table_name)


def _columns(bind, table_name: str) -> set[str]:
    if not _table_exists(bind, table_name):
        return set()
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def _indexes(bind, table_name: str) -> set[str]:
    if not _table_exists(bind, table_name):
        return set()
    return {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}


def _create_index_if_missing(bind, index_name: str, table_name: str, columns: list[str]) -> None:
    if _table_exists(bind, table_name) and index_name not in _indexes(bind, table_name):
        op.create_index(index_name, table_name, columns)


def _create_postgres_enums(bind) -> None:
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $$
        BEGIN
            CREATE TYPE feed_scroll_mode AS ENUM ('job_search', 'post_search');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            CREATE TYPE feed_scroll_job_status AS ENUM ('draft', 'active', 'paused');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )


def _mode_type(bind):
    if bind.dialect.name == "postgresql":
        return sa.Enum("job_search", "post_search", name="feed_scroll_mode", create_type=False)
    return sa.String()


def _status_type(bind):
    if bind.dialect.name == "postgresql":
        return sa.Enum("draft", "active", "paused", name="feed_scroll_job_status", create_type=False)
    return sa.String()


def upgrade() -> None:
    """Repair DBs that already marked the previous empty feed-scroll revision.

    Revision 5f7e70839c37 was previously an empty stub.  If a deployment already
    applied that old empty migration, editing it will not run again.  This new
    repair migration adds the missing feed-scroll tables/columns (especially
    feed_scroll_results.post_url) idempotently.
    """
    bind = op.get_bind()
    _create_postgres_enums(bind)
    mode_type = _mode_type(bind)
    status_type = _status_type(bind)

    if not _table_exists(bind, JOB_TABLE):
        op.create_table(
            JOB_TABLE,
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("account_email", sa.String(), nullable=False),
            sa.Column("owner_email", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("mode", mode_type, nullable=False, server_default="job_search"),
            sa.Column("status", status_type, nullable=False, server_default="draft"),
            sa.Column("experience_min_years", sa.Integer(), nullable=True),
            sa.Column("experience_max_years", sa.Integer(), nullable=True),
            sa.Column("job_titles", sa.JSON(), nullable=True),
            sa.Column("skill_set", sa.JSON(), nullable=True),
            sa.Column("keywords", sa.JSON(), nullable=True),
            sa.Column("feed_interval_hours", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("posts_per_scan", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_scan_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["account_email"], ["linkedin_accounts.linkedin_email"]),
            sa.ForeignKeyConstraint(["owner_email"], ["users.email"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _table_exists(bind, RESULT_TABLE):
        op.create_table(
            RESULT_TABLE,
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("feed_scroll_job_id", sa.String(), nullable=False),
            sa.Column("post_urn", sa.String(), nullable=True),
            sa.Column("post_url", sa.String(), nullable=True),
            sa.Column("author_name", sa.String(), nullable=True),
            sa.Column("post_text", sa.Text(), nullable=True),
            sa.Column("score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("matched_terms", sa.JSON(), nullable=True),
            sa.Column("scan_batch_id", sa.String(), nullable=False),
            sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["feed_scroll_job_id"], ["feed_scroll_jobs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    elif "post_url" not in _columns(bind, RESULT_TABLE):
        op.add_column(RESULT_TABLE, sa.Column("post_url", sa.String(), nullable=True))

    _create_index_if_missing(bind, "ix_feed_scroll_jobs_account_email", JOB_TABLE, ["account_email"])
    _create_index_if_missing(bind, "ix_feed_scroll_jobs_owner_email", JOB_TABLE, ["owner_email"])
    _create_index_if_missing(bind, "ix_feed_scroll_results_feed_scroll_job_id", RESULT_TABLE, ["feed_scroll_job_id"])
    _create_index_if_missing(bind, "ix_feed_scroll_results_post_urn", RESULT_TABLE, ["post_urn"])
    _create_index_if_missing(bind, "ix_feed_scroll_results_scan_batch_id", RESULT_TABLE, ["scan_batch_id"])


def downgrade() -> None:
    """No destructive downgrade for this repair migration."""
    # Keep tables/columns intact.  The previous migration owns the destructive
    # downgrade path; this repair must be safe to roll back without data loss.
    pass
