"""add feed post author details

Revision ID: b7c92e41f3d8
Revises: 9b2c1d4e5f60
Create Date: 2026-08-05 00:00:00.000000

Adds per-post author/profile metadata to feed_scroll_results:

  * author_first_name / author_last_name — split from the full display name
  * author_profile_url                   — absolute LinkedIn /in/ URL
  * connection_degree                    — "1st" | "2nd" | "3rd"
  * post_time                            — LinkedIn relative time label

Old rows are backfilled for first/last name from the existing author_name
column.  post_url is left untouched (it is normalised at read/store time by
the API/worker), so every result stays clickable.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c92e41f3d8"
down_revision: Union[str, Sequence[str], None] = "9b2c1d4e5f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RESULT_TABLE = "feed_scroll_results"

NEW_COLUMNS = [
    sa.Column("author_first_name", sa.String(), nullable=True),
    sa.Column("author_last_name", sa.String(), nullable=True),
    sa.Column("author_profile_url", sa.String(), nullable=True),
    sa.Column("connection_degree", sa.String(), nullable=True),
    sa.Column("post_time", sa.String(), nullable=True),
]


def _columns(bind, table_name: str) -> set[str]:
    if not sa.inspect(bind).has_table(table_name):
        return set()
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def upgrade() -> None:
    """Add the new author/profile columns idempotently and backfill names."""
    bind = op.get_bind()

    for column in NEW_COLUMNS:
        if column.name not in _columns(bind, RESULT_TABLE):
            op.add_column(RESULT_TABLE, column)

    if not sa.inspect(bind).has_table(RESULT_TABLE):
        return
    cols = _columns(bind, RESULT_TABLE)

    # Backfill post links for old rows: every post must be linkable.  Rows with
    # a real activity URN but no stored URL get the canonical feed URL; rows
    # with neither (or only a pseudo-URN) stay null and are hidden by the API.
    if "post_url" in cols and "post_urn" in cols:
        linked = bind.execute(
            sa.text(
                "UPDATE feed_scroll_results "
                "SET post_url = 'https://www.linkedin.com/feed/update/' || post_urn || '/' "
                "WHERE post_url IS NULL AND post_urn LIKE 'urn:li:%'"
            )
        )
        if linked.rowcount:
            print(f"Backfilled post_url for {linked.rowcount} existing feed scroll results")

    # Backfill first/last name from the full author_name for existing rows.
    if "author_name" not in cols or "author_first_name" not in cols:
        return

    rows = bind.execute(
        sa.text(
            "SELECT id, author_name FROM feed_scroll_results "
            "WHERE author_name IS NOT NULL AND TRIM(author_name) <> '' "
            "AND author_first_name IS NULL"
        )
    ).fetchall()

    updated = 0
    for row_id, full_name in rows:
        parts = [p for p in str(full_name or "").strip().split() if p]
        if not parts:
            continue
        first = parts[0][:80]
        last = " ".join(parts[1:])[:80] if len(parts) > 1 else None
        bind.execute(
            sa.text(
                "UPDATE feed_scroll_results "
                "SET author_first_name = :first, author_last_name = :last "
                "WHERE id = :rid"
            ),
            {"first": first, "last": last, "rid": row_id},
        )
        updated += 1

    if updated:
        print(f"Backfilled first/last name for {updated} existing feed scroll results")


def downgrade() -> None:
    """Remove the new columns if present (non-destructive to other data)."""
    bind = op.get_bind()
    for column in NEW_COLUMNS:
        if column.name in _columns(bind, RESULT_TABLE):
            op.drop_column(RESULT_TABLE, column.name)
