"""keep twenty verified feed posts

Revision ID: c6e2a20f5c20
Revises: b7c92e41f3d8
Create Date: 2026-08-05 00:00:00.000000

New scans retain up to twenty scored posts.  Earlier versions created jobs with
an implicit 10- or 15-post setting, so those legacy defaults are raised to 20
as part of this migration.  Explicit custom values outside those old defaults
are left untouched; the worker still applies the hard 20-post maximum.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6e2a20f5c20"
down_revision: Union[str, Sequence[str], None] = "b7c92e41f3d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JOB_TABLE = "feed_scroll_jobs"


def _has_posts_per_scan_column(bind) -> bool:
    if not sa.inspect(bind).has_table(JOB_TABLE):
        return False
    return "posts_per_scan" in {
        column["name"] for column in sa.inspect(bind).get_columns(JOB_TABLE)
    }


def _set_posts_per_scan_default(bind, value: str) -> None:
    """Set the default on PostgreSQL and SQLite-based local/test databases."""
    options = {
        "existing_type": sa.Integer(),
        "existing_nullable": False,
        "server_default": sa.text(value),
    }
    if bind.dialect.name == "sqlite":
        # SQLite cannot ALTER a column in place; Alembic's batch mode recreates
        # the table while preserving rows and foreign keys.
        with op.batch_alter_table(JOB_TABLE) as batch_op:
            batch_op.alter_column("posts_per_scan", **options)
    else:
        op.alter_column(JOB_TABLE, "posts_per_scan", **options)


def upgrade() -> None:
    """Make twenty the database default and upgrade prior UI defaults."""
    bind = op.get_bind()
    if not _has_posts_per_scan_column(bind):
        return

    # The old API/schema defaults were 10 and 15.  Those values were not
    # user-selectable in the UI, so safely bring those jobs to the new policy.
    bind.execute(
        sa.text(
            "UPDATE feed_scroll_jobs SET posts_per_scan = 20 "
            "WHERE posts_per_scan IN (10, 15)"
        )
    )
    _set_posts_per_scan_default(bind, "20")


def downgrade() -> None:
    """Restore the legacy database default without deleting any results."""
    bind = op.get_bind()
    if not _has_posts_per_scan_column(bind):
        return
    _set_posts_per_scan_default(bind, "10")
