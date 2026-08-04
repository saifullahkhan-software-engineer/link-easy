"""add feed scroll tables

Revision ID: 5f7e70839c37
Revises: 73bf9be96dea
Create Date: 2026-08-03 21:17:10.648821

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "5f7e70839c37"
down_revision: Union[str, Sequence[str], None] = "73bf9be96dea"
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


def _fk_names(bind, table_name: str) -> set[str]:
    if not _table_exists(bind, table_name):
        return set()
    return {fk["name"] for fk in sa.inspect(bind).get_foreign_keys(table_name) if fk.get("name")}


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


def _add_column_if_missing(bind, table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(bind, table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(bind, index_name: str, table_name: str, columns: list[str]) -> None:
    if index_name not in _indexes(bind, table_name):
        op.create_index(index_name, table_name, columns)


def _create_fk_if_missing(
    bind,
    fk_name: str,
    source_table: str,
    referent_table: str,
    local_cols: list[str],
    remote_cols: list[str],
    ondelete: str | None = None,
) -> None:
    if fk_name not in _fk_names(bind, source_table):
        op.create_foreign_key(fk_name, source_table, referent_table, local_cols, remote_cols, ondelete=ondelete)


def upgrade() -> None:
    """Create/feed-scroll tables and backfill missing columns from older deploys.

    The previous revision stub was empty, so environments that did not manually
    run ``Base.metadata.create_all`` were missing the feed scroll tables entirely;
    environments that did create the tables before later fixes may be missing
    ``post_url``.  This migration is intentionally idempotent so it can repair
    both cases safely.
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
    else:
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("account_email", sa.String(), nullable=True))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("owner_email", sa.String(), nullable=True))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("name", sa.String(), nullable=False, server_default="Feed Scroll Job"))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("mode", mode_type, nullable=False, server_default="job_search"))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("status", status_type, nullable=False, server_default="draft"))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("experience_min_years", sa.Integer(), nullable=True))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("experience_max_years", sa.Integer(), nullable=True))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("job_titles", sa.JSON(), nullable=True))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("skill_set", sa.JSON(), nullable=True))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("keywords", sa.JSON(), nullable=True))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("feed_interval_hours", sa.Integer(), nullable=False, server_default="1"))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("posts_per_scan", sa.Integer(), nullable=False, server_default="10"))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("next_scan_at", sa.DateTime(timezone=True), nullable=True))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        _add_column_if_missing(bind, JOB_TABLE, sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        _create_fk_if_missing(bind, "feed_scroll_jobs_account_email_fkey", JOB_TABLE, "linkedin_accounts", ["account_email"], ["linkedin_email"])
        _create_fk_if_missing(bind, "feed_scroll_jobs_owner_email_fkey", JOB_TABLE, "users", ["owner_email"], ["email"], ondelete="CASCADE")

    _create_index_if_missing(bind, "ix_feed_scroll_jobs_account_email", JOB_TABLE, ["account_email"])
    _create_index_if_missing(bind, "ix_feed_scroll_jobs_owner_email", JOB_TABLE, ["owner_email"])

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
    else:
        _add_column_if_missing(bind, RESULT_TABLE, sa.Column("feed_scroll_job_id", sa.String(), nullable=True))
        _add_column_if_missing(bind, RESULT_TABLE, sa.Column("post_urn", sa.String(), nullable=True))
        _add_column_if_missing(bind, RESULT_TABLE, sa.Column("post_url", sa.String(), nullable=True))
        _add_column_if_missing(bind, RESULT_TABLE, sa.Column("author_name", sa.String(), nullable=True))
        _add_column_if_missing(bind, RESULT_TABLE, sa.Column("post_text", sa.Text(), nullable=True))
        _add_column_if_missing(bind, RESULT_TABLE, sa.Column("score", sa.Float(), nullable=False, server_default="0"))
        _add_column_if_missing(bind, RESULT_TABLE, sa.Column("matched_terms", sa.JSON(), nullable=True))
        _add_column_if_missing(bind, RESULT_TABLE, sa.Column("scan_batch_id", sa.String(), nullable=True))
        _add_column_if_missing(bind, RESULT_TABLE, sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True))
        _add_column_if_missing(bind, RESULT_TABLE, sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        _create_fk_if_missing(bind, "feed_scroll_results_feed_scroll_job_id_fkey", RESULT_TABLE, JOB_TABLE, ["feed_scroll_job_id"], ["id"], ondelete="CASCADE")

    _create_index_if_missing(bind, "ix_feed_scroll_results_feed_scroll_job_id", RESULT_TABLE, ["feed_scroll_job_id"])
    _create_index_if_missing(bind, "ix_feed_scroll_results_post_urn", RESULT_TABLE, ["post_urn"])
    _create_index_if_missing(bind, "ix_feed_scroll_results_scan_batch_id", RESULT_TABLE, ["scan_batch_id"])


def downgrade() -> None:
    """Drop feed-scroll tables and enum types."""
    bind = op.get_bind()
    for index_name, table_name in (
        ("ix_feed_scroll_results_scan_batch_id", RESULT_TABLE),
        ("ix_feed_scroll_results_post_urn", RESULT_TABLE),
        ("ix_feed_scroll_results_feed_scroll_job_id", RESULT_TABLE),
        ("ix_feed_scroll_jobs_owner_email", JOB_TABLE),
        ("ix_feed_scroll_jobs_account_email", JOB_TABLE),
    ):
        if _table_exists(bind, table_name) and index_name in _indexes(bind, table_name):
            op.drop_index(index_name, table_name=table_name)

    if _table_exists(bind, RESULT_TABLE):
        op.drop_table(RESULT_TABLE)
    if _table_exists(bind, JOB_TABLE):
        op.drop_table(JOB_TABLE)

    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS feed_scroll_job_status")
        op.execute("DROP TYPE IF EXISTS feed_scroll_mode")
