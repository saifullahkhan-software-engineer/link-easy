"""merge migration heads and add scheduling method

Revision ID: h3i4j5k6l7m8
Revises: f1a2b3c4d5e6, g2h3i4j5k6l7
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h3i4j5k6l7m8"
down_revision: Union[str, Sequence[str], None] = ("f1a2b3c4d5e6", "g2h3i4j5k6l7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    if not sa.inspect(bind).has_table(table):
        return False
    return any(item["name"] == column for item in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "social_posts", "scheduling_method"):
        op.add_column(
            "social_posts",
            sa.Column(
                "scheduling_method",
                sa.String(),
                nullable=False,
                server_default="linkeasy",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "social_posts", "scheduling_method"):
        op.drop_column("social_posts", "scheduling_method")
