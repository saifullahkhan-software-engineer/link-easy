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


def upgrade():
    op.add_column("whatsapp_scan_filters", sa.Column("interval_hours", sa.Float(), nullable=False, server_default="1.0"))
    op.add_column("whatsapp_scan_filters", sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("whatsapp_scan_filters", "last_scan_at")
    op.drop_column("whatsapp_scan_filters", "interval_hours")
