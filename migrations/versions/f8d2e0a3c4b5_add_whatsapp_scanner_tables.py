"""add whatsapp scanner tables

Revision ID: f8d2e0a3c4b5
Revises: e7c1f9a2b3c4
Create Date: 2026-08-08 00:00:00.000000

Creates tables for the WhatsApp Job Scanner module:
  whatsapp_sessions, whatsapp_monitored_groups, whatsapp_forward_group,
  whatsapp_raw_messages, whatsapp_scan_filters
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f8d2e0a3c4b5"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = [
    "whatsapp_sessions",
    "whatsapp_monitored_groups",
    "whatsapp_forward_group",
    "whatsapp_raw_messages",
    "whatsapp_scan_filters",
]


def _table_exists(bind, table_name: str) -> bool:
    return sa.inspect(bind).has_table(table_name)


def _columns(bind, table_name: str) -> set[str]:
    if not _table_exists(bind, table_name):
        return set()
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def _add_column_if_missing(bind, table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(bind, table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()

    # ── whatsapp_sessions ─────────────────────────────────────────────────
    if not _table_exists(bind, "whatsapp_sessions"):
        op.create_table(
            "whatsapp_sessions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("cookies_json", sa.JSON(), nullable=True),
            sa.Column("storage_state_json", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column(
                "status",
                sa.String(),
                nullable=False,
                server_default="disconnected",
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    # ── whatsapp_monitored_groups ─────────────────────────────────────────
    if not _table_exists(bind, "whatsapp_monitored_groups"):
        op.create_table(
            "whatsapp_monitored_groups",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("group_name", sa.String(), nullable=False),
            sa.Column("whatsapp_id", sa.String(), nullable=True),
            sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("last_message_timestamp", sa.String(), nullable=True),
            sa.Column("last_message_id", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    # ── whatsapp_forward_group ────────────────────────────────────────────
    if not _table_exists(bind, "whatsapp_forward_group"):
        op.create_table(
            "whatsapp_forward_group",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("group_name", sa.String(), nullable=False),
            sa.Column("whatsapp_id", sa.String(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    # ── whatsapp_raw_messages ─────────────────────────────────────────────
    if not _table_exists(bind, "whatsapp_raw_messages"):
        op.create_table(
            "whatsapp_raw_messages",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("group_id", sa.Integer(), nullable=False),
            sa.Column("sender_name", sa.String(), nullable=True),
            sa.Column("message_text", sa.Text(), nullable=True),
            sa.Column("ocr_text", sa.Text(), nullable=True),
            sa.Column(
                "message_type",
                sa.String(),
                nullable=False,
                server_default="text",
            ),
            sa.Column("match_score", sa.Float(), nullable=True),
            sa.Column(
                "status",
                sa.String(),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("forwarded", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("forwarded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("raw_image_bytes", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("ocr_failed", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("whatsapp_message_id", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    # ── whatsapp_scan_filters ─────────────────────────────────────────────
    if not _table_exists(bind, "whatsapp_scan_filters"):
        op.create_table(
            "whatsapp_scan_filters",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("role", sa.String(), nullable=True),
            sa.Column("job_title", sa.String(), nullable=True),
            sa.Column("keywords", sa.JSON(), nullable=True),
            sa.Column("experience_level", sa.String(), nullable=True),
            sa.Column(
                "match_threshold",
                sa.Float(),
                nullable=False,
                server_default="60",
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(TABLES):
        if _table_exists(bind, table_name):
            op.drop_table(table_name)
