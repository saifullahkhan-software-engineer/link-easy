"""add feed scroll result dismissed flag

Revision ID: e7c1f9a2b3c4
Revises: d4a1c7b8e920
Create Date: 2026-08-06 00:00:00.000000

Lets a user remove a single scanned post from the Feed Scroll results view
after reading it and deciding it is not useful.

The removal is a *soft dismiss*: we flag the row with ``dismissed_at`` instead
of hard-deleting it.  The results query already filters dismissed rows out, but
the row stays in the ``feed_scroll_results`` table so the background scanner's
de-dup (which keys off every stored row for the job) still treats the post as
"already seen" — a dismissed post therefore never reappears on the next
scheduled scan.  ``restore`` clears the flag.

The column is added idempotently so the startup migration runner can re-apply
this safely on databases that were created with ``Base.metadata.create_all()``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7c1f9a2b3c4"
down_revision: Union[str, Sequence[str], None] = "d4a1c7b8e920"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RESULTS_TABLE = "feed_scroll_results"
DISMISSED_COLUMN = sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True)


def _columns(bind, table_name: str) -> set[str]:
    if not sa.inspect(bind).has_table(table_name):
        return set()
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if DISMISSED_COLUMN.name not in _columns(bind, RESULTS_TABLE):
        op.add_column(RESULTS_TABLE, DISMISSED_COLUMN)


def downgrade() -> None:
    bind = op.get_bind()
    if DISMISSED_COLUMN.name in _columns(bind, RESULTS_TABLE):
        op.drop_column(RESULTS_TABLE, DISMISSED_COLUMN.name)
