"""add platform_credentials table

Revision ID: c8d4e6f1a2b3
Revises: f1a2b3c4d5e6
Create Date: 2026-09-04

Operator-set OAuth *app* credentials for the social scheduler platforms. A
row here overrides the corresponding environment pair (YOUTUBE_CLIENT_ID …)
so app credentials can be entered from the settings page instead of only via
env vars. One row per platform; deployment-global (no owner_email — the
credentials belong to the instance, exactly like the env vars they replace).

Guarded so the migration is safe to re-run, matching the idempotent style of
the other files in this directory. The startup path runs
``Base.metadata.create_all`` before Alembic, so on a fresh database this table
usually already exists by the time this revision runs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8d4e6f1a2b3"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "platform_credentials"):
        op.create_table(
            "platform_credentials",
            sa.Column("platform", sa.String(), nullable=False),
            sa.Column("client_id", sa.String(), nullable=False, server_default=""),
            sa.Column("client_secret", sa.String(), nullable=False, server_default=""),
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
            sa.PrimaryKeyConstraint("platform"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "platform_credentials"):
        op.drop_table("platform_credentials")
