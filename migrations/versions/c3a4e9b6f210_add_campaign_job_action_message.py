"""Add a screen-ready message to campaign action history.

Revision ID: c3a4e9b6f210
Revises: 843733ff8226
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c3a4e9b6f210"
down_revision: Union[str, Sequence[str], None] = "843733ff8226"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaign_jobs", sa.Column("action_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaign_jobs", "action_message")
