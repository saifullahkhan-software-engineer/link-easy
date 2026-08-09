"""add configurable WhatsApp latest-message limit

Revision ID: c3d5e7f9a1b2
Revises: b2c4d6e8f0a1
Create Date: 2026-08-10

Each filter controls how many of the newest messages are inspected per group.
The existing ``last_message_id`` on every monitored-group row remains the
incremental scan cursor.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d5e7f9a1b2"
down_revision: Union[str, Sequence[str], None] = "b2c4d6e8f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if "latest_messages_limit" not in _columns(bind, "whatsapp_scan_filters"):
        op.add_column(
            "whatsapp_scan_filters",
            sa.Column(
                "latest_messages_limit",
                sa.Integer(),
                nullable=False,
                server_default="20",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "latest_messages_limit" in _columns(bind, "whatsapp_scan_filters"):
        op.drop_column("whatsapp_scan_filters", "latest_messages_limit")
