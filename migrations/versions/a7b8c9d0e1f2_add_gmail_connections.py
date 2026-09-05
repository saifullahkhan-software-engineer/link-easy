"""add gmail connections

Revision ID: a7b8c9d0e1f2
Revises: a9b8c7d6e5f4
Create Date: 2026-09-05

Adds the per-user Gmail OAuth connection table. Following the conventions of
the other connection tables in this schema:

  * ``owner_email`` FK users.email CASCADE — the ownership key used by
    linkedin_accounts / social_platform_connections / whatsapp_sessions;
  * one row per user (unique owner_email) — a user connects one mailbox;
  * tokens are stored as AES-256-GCM ciphertext columns (core.security),
    never raw;
  * timestamps are timestamptz with a real ``now()`` server default.

Every step is guarded so the migration is safe to re-run, matching the
idempotent style of the other files in this directory. The startup path runs
``Base.metadata.create_all`` before Alembic, so on a fresh database this table
usually already exists by the time this revision runs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "gmail_connections"):
        return

    op.create_table(
        "gmail_connections",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_email", sa.String(), nullable=False),
        sa.Column("account_email", sa.String(), nullable=False, server_default=""),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("messages_total", sa.String(), nullable=False, server_default=""),
        sa.Column("threads_total", sa.String(), nullable=False, server_default=""),
        sa.Column("history_id", sa.String(), nullable=False, server_default=""),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["owner_email"], ["users.email"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_email", name="uq_gmail_connections_account_email"),
        sa.UniqueConstraint("owner_email", name="uq_gmail_connections_owner_email"),
    )
    op.create_index(
        "ix_gmail_connections_owner_email", "gmail_connections", ["owner_email"], unique=True
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "gmail_connections"):
        return
    op.drop_index("ix_gmail_connections_owner_email", table_name="gmail_connections")
    op.drop_table("gmail_connections")
