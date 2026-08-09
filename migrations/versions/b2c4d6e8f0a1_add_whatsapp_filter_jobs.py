"""add WhatsApp filter jobs and per-filter data

Revision ID: b2c4d6e8f0a1
Revises: a9b3c4d5e6f7
Create Date: 2026-08-09

The first WhatsApp scanner stored one global filter and one global group
selection.  The filter-jobs workflow needs ownership, lifecycle state and
independent group/results data for every filter.  All new columns are nullable
or have a safe default so existing singleton-scanner installations continue to
work while users move to the new pages.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c4d6e8f0a1"
down_revision: Union[str, Sequence[str], None] = "a9b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, table_name: str) -> bool:
    return sa.inspect(bind).has_table(table_name)


def _columns(bind, table_name: str) -> set[str]:
    if not _table_exists(bind, table_name):
        return set()
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def _add_column_if_missing(bind, table_name: str, column: sa.Column) -> None:
    if _table_exists(bind, table_name) and column.name not in _columns(bind, table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()

    # Filter-job identity and scheduler state.  Existing rows are marked
    # active so the old singleton scanner does not unexpectedly stop after an
    # upgrade; newly-created rows use the model's draft default.
    _add_column_if_missing(
        bind,
        "whatsapp_scan_filters",
        sa.Column("owner_email", sa.String(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "whatsapp_scan_filters",
        sa.Column(
            "name",
            sa.String(),
            nullable=False,
            server_default="WhatsApp Filter",
        ),
    )
    _add_column_if_missing(
        bind,
        "whatsapp_scan_filters",
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default="active",
        ),
    )
    _add_column_if_missing(
        bind,
        "whatsapp_scan_filters",
        sa.Column("remaining_seconds", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "whatsapp_scan_filters",
        sa.Column("next_scan_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "whatsapp_scan_filters",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )

    if _table_exists(bind, "whatsapp_scan_filters"):
        # Keep legacy rows usable.  ``name`` and ``status`` may be NULL on a
        # partially migrated database, so normalize them before the model's
        # non-nullable mapping is used.
        op.execute(
            sa.text(
                "UPDATE whatsapp_scan_filters "
                "SET name = 'WhatsApp Filter' WHERE name IS NULL"
            )
        )
        op.execute(
            sa.text(
                "UPDATE whatsapp_scan_filters "
                "SET status = 'active' WHERE status IS NULL"
            )
        )

    # A filter owns its group configuration and raw messages.  NULL means the
    # row belongs to the old global scanner and is intentionally preserved.
    _add_column_if_missing(
        bind,
        "whatsapp_monitored_groups",
        sa.Column("filter_id", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "whatsapp_forward_group",
        sa.Column("filter_id", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "whatsapp_raw_messages",
        sa.Column("filter_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name, column_name in (
        ("whatsapp_raw_messages", "filter_id"),
        ("whatsapp_forward_group", "filter_id"),
        ("whatsapp_monitored_groups", "filter_id"),
        ("whatsapp_scan_filters", "created_at"),
        ("whatsapp_scan_filters", "next_scan_at"),
        ("whatsapp_scan_filters", "remaining_seconds"),
        ("whatsapp_scan_filters", "status"),
        ("whatsapp_scan_filters", "name"),
        ("whatsapp_scan_filters", "owner_email"),
    ):
        if _table_exists(bind, table_name) and column_name in _columns(bind, table_name):
            op.drop_column(table_name, column_name)
