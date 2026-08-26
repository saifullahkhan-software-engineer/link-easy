"""allow LinkedIn accounts connected by session cookie (no password)

Accounts connected by importing an ``li_at`` session cookie never supply a
password, so ``encrypted_password`` has to become nullable. ``auth_method``
records how each account was connected so the relogin fallback knows whether
a password is even available.

Revision ID: e1a4b7c9d2f3
Revises: d7f3a1b9c2e4
"""
from alembic import op
import sqlalchemy as sa

revision = "e1a4b7c9d2f3"
down_revision = "d7f3a1b9c2e4"
branch_labels = None
depends_on = None

TABLE = "linkedin_accounts"


def _columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade():
    bind = op.get_bind()
    columns = _columns(bind, TABLE)
    if not columns:
        # Fresh database — create_all() builds the current model directly.
        return

    if "auth_method" not in columns:
        op.add_column(
            TABLE,
            sa.Column(
                "auth_method",
                sa.String(),
                nullable=False,
                server_default="password",
            ),
        )

    if "encrypted_password" in columns:
        # SQLite cannot ALTER a column in place; batch_alter_table rebuilds
        # the table for it and is a plain ALTER everywhere else.
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.alter_column(
                "encrypted_password",
                existing_type=sa.String(),
                nullable=True,
            )


def downgrade():
    bind = op.get_bind()
    columns = _columns(bind, TABLE)
    if not columns:
        return

    # Cookie-connected accounts have no password and cannot satisfy a NOT NULL
    # constraint; drop them so the column can be tightened again.
    op.execute(
        sa.text(
            f"DELETE FROM {TABLE} WHERE encrypted_password IS NULL"  # noqa: S608
        )
    )

    if "encrypted_password" in columns:
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.alter_column(
                "encrypted_password",
                existing_type=sa.String(),
                nullable=False,
            )

    if "auth_method" in columns:
        op.drop_column(TABLE, "auth_method")
