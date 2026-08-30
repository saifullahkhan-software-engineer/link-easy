"""add per-user whatsapp sessions

Revision ID: e9d2f1a0b3c4
Revises: d7f3a1b9c2e4
Create Date: 2026-08-30 00:00:00.000000

WhatsApp connections become per-user, mirroring the LinkedIn accounts model:

  * ``whatsapp_sessions.owner_email`` — FK to users.email. NULL on legacy
    rows; the first authenticated user without a session adopts them (and
    keeps using the legacy shared flat profile dir).
  * ``whatsapp_sessions.profile_dir`` — durable per-session Chromium
    user-data-dir. Set at creation time to
    ``{PROFILE_STORAGE_DIR}/whatsapp/session-{id}``. NULL resolves to the
    legacy flat dir ``{PROFILE_STORAGE_DIR}/whatsapp`` so pre-migration
    installations keep their working session without moving files.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e9d2f1a0b3c4"
down_revision: Union[str, Sequence[str], None] = "d7f3a1b9c2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_sessions",
        sa.Column("owner_email", sa.String(), nullable=True),
    )
    op.add_column(
        "whatsapp_sessions",
        sa.Column("profile_dir", sa.String(), nullable=True),
    )
    # Production runs PostgreSQL, which supports adding the constraint
    # directly. SQLite cannot ALTER constraints without a table copy; the
    # ORM performs its own ownership checks, so skip it there (mirrors how
    # the earlier scanner-table migration guards dialect-specific work).
    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_whatsapp_sessions_owner_email_users",
            "whatsapp_sessions",
            "users",
            ["owner_email"],
            ["email"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            "fk_whatsapp_sessions_owner_email_users",
            "whatsapp_sessions",
            type_="foreignkey",
        )
    op.drop_column("whatsapp_sessions", "profile_dir")
    op.drop_column("whatsapp_sessions", "owner_email")
