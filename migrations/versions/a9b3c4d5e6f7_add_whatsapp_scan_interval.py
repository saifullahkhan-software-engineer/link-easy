"""add WhatsApp scan interval

Revision ID: a9b3c4d5e6f7
Revises: f8d2e0a3c4b5
"""
from alembic import op
import sqlalchemy as sa

revision = "a9b3c4d5e6f7"
down_revision = "f8d2e0a3c4b5"
branch_labels = None
depends_on = None


def _columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade():
    bind = op.get_bind()
    columns = _columns(bind, "whatsapp_scan_filters")
    if "interval_hours" not in columns:
        op.add_column(
            "whatsapp_scan_filters",
            sa.Column("interval_hours", sa.Float(), nullable=False, server_default="1.0"),
        )
    if "last_scan_at" not in columns:
        op.add_column(
            "whatsapp_scan_filters",
            sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade():
    bind = op.get_bind()
    columns = _columns(bind, "whatsapp_scan_filters")
    if "last_scan_at" in columns:
        op.drop_column("whatsapp_scan_filters", "last_scan_at")
    if "interval_hours" in columns:
        op.drop_column("whatsapp_scan_filters", "interval_hours")
