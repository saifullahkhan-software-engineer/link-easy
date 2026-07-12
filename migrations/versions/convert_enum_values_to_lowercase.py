"""convert enum values to lowercase for values_callable compatibility

Revision ID: convert_enum_values_to_lowercase
Revises: change_delay_hours_to_float
Create Date: 2026-07-12

This migration converts PostgreSQL enum values from uppercase to lowercase
to match the new values_callable configuration in the models.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'convert_enum_values_to_lowercase'
down_revision: Union[str, Sequence[str], None] = 'revert_status_to_enum'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - convert enum values to lowercase."""
    
    # Process lead_status enum
    op.execute("CREATE TYPE lead_status_new AS ENUM ('pending', 'visiting', 'requested', 'accepted', 'messaged', 'replied', 'skipped', 'failed', 'complete')")
    op.execute("ALTER TABLE leads ALTER COLUMN status TYPE lead_status_new USING lower(status::text)::lead_status_new")
    op.execute("DROP TYPE lead_status")
    op.execute("ALTER TYPE lead_status_new RENAME TO lead_status")
    
    # Process campaign_status enum
    op.execute("CREATE TYPE campaign_status_new AS ENUM ('draft', 'active', 'paused', 'complete', 'failed')")
    op.execute("ALTER TABLE campaigns ALTER COLUMN status TYPE campaign_status_new USING lower(status::text)::campaign_status_new")
    op.execute("DROP TYPE campaign_status")
    op.execute("ALTER TYPE campaign_status_new RENAME TO campaign_status")
    
    # Process campaign_step_type enum
    op.execute("CREATE TYPE campaign_step_type_new AS ENUM ('visit_profile', 'like_post', 'visit_and_like', 'send_connection', 'send_message', 'follow_up_if_pending', 'thanks_if_accepted')")
    op.execute("ALTER TABLE campaign_steps ALTER COLUMN step_type TYPE campaign_step_type_new USING lower(step_type::text)::campaign_step_type_new")
    op.execute("DROP TYPE campaign_step_type")
    op.execute("ALTER TYPE campaign_step_type_new RENAME TO campaign_step_type")
    
    # Process job_status enum
    op.execute("CREATE TYPE job_status_new AS ENUM ('queued', 'running', 'done', 'failed', 'skipped')")
    op.execute("ALTER TABLE campaign_jobs ALTER COLUMN status TYPE job_status_new USING lower(status::text)::job_status_new")
    op.execute("DROP TYPE job_status")
    op.execute("ALTER TYPE job_status_new RENAME TO job_status")


def downgrade() -> None:
    """Downgrade schema - convert enum values back to uppercase."""
    
    # Process lead_status enum (reverse)
    op.execute("CREATE TYPE lead_status_old AS ENUM ('PENDING', 'VISITING', 'REQUESTED', 'ACCEPTED', 'MESSAGED', 'REPLIED', 'SKIPPED', 'FAILED', 'COMPLETE')")
    op.execute("ALTER TABLE leads ALTER COLUMN status TYPE lead_status_old USING upper(status::text)::lead_status_old")
    op.execute("DROP TYPE lead_status")
    op.execute("ALTER TYPE lead_status_old RENAME TO lead_status")
    
    # Process campaign_status enum (reverse)
    op.execute("CREATE TYPE campaign_status_old AS ENUM ('DRAFT', 'ACTIVE', 'PAUSED', 'COMPLETE', 'FAILED')")
    op.execute("ALTER TABLE campaigns ALTER COLUMN status TYPE campaign_status_old USING upper(status::text)::campaign_status_old")
    op.execute("DROP TYPE campaign_status")
    op.execute("ALTER TYPE campaign_status_old RENAME TO campaign_status")
    
    # Process campaign_step_type enum (reverse)
    op.execute("CREATE TYPE campaign_step_type_old AS ENUM ('VISIT_PROFILE', 'LIKE_POST', 'VISIT_AND_LIKE', 'SEND_CONNECTION', 'SEND_MESSAGE', 'FOLLOW_UP_IF_PENDING', 'THANKS_IF_ACCEPTED')")
    op.execute("ALTER TABLE campaign_steps ALTER COLUMN step_type TYPE campaign_step_type_old USING upper(step_type::text)::campaign_step_type_old")
    op.execute("DROP TYPE campaign_step_type")
    op.execute("ALTER TYPE campaign_step_type_old RENAME TO campaign_step_type")
    
    # Process job_status enum (reverse)
    op.execute("CREATE TYPE job_status_old AS ENUM ('QUEUED', 'RUNNING', 'DONE', 'FAILED', 'SKIPPED')")
    op.execute("ALTER TABLE campaign_jobs ALTER COLUMN status TYPE job_status_old USING upper(status::text)::job_status_old")
    op.execute("DROP TYPE job_status")
    op.execute("ALTER TYPE job_status_old RENAME TO job_status")
