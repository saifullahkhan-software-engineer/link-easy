"""rename encrypted_cookies to encrypted_storage_state

Revision ID: rename_cookies_to_storage_state
Revises: convert_enum_values_to_lowercase
Create Date: 2026-07-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'rename_cookies_to_storage_state'
down_revision: Union[str, Sequence[str], None] = 'convert_enum_values_to_lowercase'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename encrypted_cookies column to encrypted_storage_state."""
    # Rename the column - existing data is preserved as-is
    op.alter_column('linkedin_accounts', 'encrypted_cookies', new_column_name='encrypted_storage_state')


def downgrade() -> None:
    """Revert encrypted_storage_state back to encrypted_cookies."""
    op.alter_column('linkedin_accounts', 'encrypted_storage_state', new_column_name='encrypted_cookies')
