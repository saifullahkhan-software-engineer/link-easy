"""add the new field

Revision ID: 73bf9be96dea
Revises:
Create Date: 2026-08-02 08:50:39.491815

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "73bf9be96dea"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(table_name):
        return False
    return column_name in {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def upgrade() -> None:
    """Add campaign_jobs.action_message if the column is not already present.

    Some deployments used ``Base.metadata.create_all`` before Alembic was run,
    so the column can already exist while alembic_version is still behind.  Make
    this migration safe for those databases.
    """
    if not _has_column("campaign_jobs", "action_message"):
        op.add_column("campaign_jobs", sa.Column("action_message", sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove campaign_jobs.action_message if present."""
    if _has_column("campaign_jobs", "action_message"):
        op.drop_column("campaign_jobs", "action_message")
