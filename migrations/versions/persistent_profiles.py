"""persistent per-account browser profiles

Revision ID: persistent_profiles
Revises: rename_cookies_to_storage_state
Create Date: 2026-07-28

Moves LinkedIn session state out of the database (encrypted JSON blob) and
onto disk: every account gets a durable Chromium user-data-dir identified by
a server-generated UUID, plus pinned browser-fingerprint columns.

ROLLOUT NOTE: all previous account/session data is being wiped before this
ships — there is no legacy state to migrate. This migration therefore deletes
existing rows (and dependent campaign rows) so the new NOT NULL columns need
no defaults. Every LinkedIn account must be re-linked after this ships.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'persistent_profiles'
down_revision: Union[str, Sequence[str], None] = 'rename_cookies_to_storage_state'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Wipe legacy rows ──────────────────────────────────────────────────────
    # No legacy accounts survive this change (sessions don't carry over to
    # persistent profiles). Dependent rows go first: campaign_jobs has FKs to
    # both campaigns and leads, and leads cascades from campaigns.
    op.execute("DELETE FROM campaign_jobs")
    op.execute("DELETE FROM campaigns")   # leads cascade from campaigns
    op.execute("DELETE FROM linkedin_accounts")

    # ── Drop dead storage-state columns ───────────────────────────────────────
    # Session state now lives in the on-disk profile directory; the encrypt/
    # decrypt round-trip through Postgres is gone entirely.
    op.drop_column('linkedin_accounts', 'encrypted_storage_state')
    op.drop_column('linkedin_accounts', 'cookies_updated_at')

    # ── Swap primary key: linkedin_email → surrogate UUID id ─────────────────
    # linkedin_email is user-controlled input and must never be used to build
    # filesystem paths. The unique index ix_linkedin_accounts_linkedin_email
    # stays in place, so campaigns.account_email FKs remain valid.
    op.drop_constraint('linkedin_accounts_pkey', 'linkedin_accounts', type_='primary')
    op.add_column(
        'linkedin_accounts',
        sa.Column(
            'id', sa.String(), nullable=False,
            server_default=sa.text('gen_random_uuid()::text'),
        ),
    )
    op.create_primary_key('linkedin_accounts_pkey', 'linkedin_accounts', ['id'])

    # ── Durable per-account Chromium profile directory ───────────────────────
    # Built ONLY from the server-generated UUID: {PROFILE_STORAGE_DIR}/{id}.
    op.add_column(
        'linkedin_accounts',
        sa.Column('profile_dir', sa.String(), nullable=False),
    )

    # ── Pinned browser fingerprint (set once at first login) ─────────────────
    # user_agent already exists; the rest are new. Nullable until first login.
    op.add_column('linkedin_accounts', sa.Column('viewport_width', sa.Integer(), nullable=True))
    op.add_column('linkedin_accounts', sa.Column('viewport_height', sa.Integer(), nullable=True))
    op.add_column('linkedin_accounts', sa.Column('timezone_id', sa.String(), nullable=True))
    op.add_column('linkedin_accounts', sa.Column('locale', sa.String(), nullable=True))
    op.add_column('linkedin_accounts', sa.Column('hardware_concurrency', sa.Integer(), nullable=True))
    op.add_column('linkedin_accounts', sa.Column('device_memory', sa.Integer(), nullable=True))

    # ── Warm-up stage override for rate-limit pacing ─────────────────────────
    # NULL = derived from account age by worker.rate_limit.warmup_stage_for_account.
    op.add_column('linkedin_accounts', sa.Column('warmup_stage', sa.String(), nullable=True))
    op.create_check_constraint(
        'ck_linkedin_accounts_warmup_stage',
        'linkedin_accounts',
        "warmup_stage IS NULL OR warmup_stage IN ('new', 'ramping', 'established')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_linkedin_accounts_warmup_stage', 'linkedin_accounts', type_='check')
    op.drop_column('linkedin_accounts', 'warmup_stage')
    op.drop_column('linkedin_accounts', 'device_memory')
    op.drop_column('linkedin_accounts', 'hardware_concurrency')
    op.drop_column('linkedin_accounts', 'locale')
    op.drop_column('linkedin_accounts', 'timezone_id')
    op.drop_column('linkedin_accounts', 'viewport_height')
    op.drop_column('linkedin_accounts', 'viewport_width')
    op.drop_column('linkedin_accounts', 'profile_dir')

    op.drop_constraint('linkedin_accounts_pkey', 'linkedin_accounts', type_='primary')
    op.drop_column('linkedin_accounts', 'id')
    op.create_primary_key('linkedin_accounts_pkey', 'linkedin_accounts', ['linkedin_email'])

    op.add_column('linkedin_accounts', sa.Column('cookies_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('linkedin_accounts', sa.Column('encrypted_storage_state', sa.Text(), nullable=True))
