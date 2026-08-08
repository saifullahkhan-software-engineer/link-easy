"""add feed scroll remaining seconds

Revision ID: 8c4f1a2b3d4e
Revises: e7c1f9a2b3c4
Create Date: 2026-08-08 00:00:00.000000

Adds `remaining_seconds` to `feed_scroll_jobs` so that when a job is paused or
when the application is closed/started, the remaining scan countdown is preserved
and resumes dropping accurately from the time difference onwards.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8c4f1a2b3d4e"
down_revision: Union[str, Sequence[str], None] = "e7c1f9a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JOB_TABLE = "feed_scroll_jobs"
REMAINING_SECONDS_COLUMN = sa.Column("remaining_seconds", sa.Integer(), nullable=True)


def _columns(bind, table_name: str) -> set[str]:
    if not sa.inspect(bind).has_table(table_name):
        return set()
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if REMAINING_SECONDS_COLUMN.name not in _columns(bind, JOB_TABLE):
        op.add_column(JOB_TABLE, REMAINING_SECONDS_COLUMN)


def downgrade() -> None:
    bind = op.get_bind()
    if REMAINING_SECONDS_COLUMN.name in _columns(bind, JOB_TABLE):
        op.drop_column(JOB_TABLE, REMAINING_SECONDS_COLUMN.name)
