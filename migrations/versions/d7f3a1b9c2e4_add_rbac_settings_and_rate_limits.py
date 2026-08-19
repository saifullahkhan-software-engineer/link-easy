"""add roles, user_roles, app_settings and rate_limit_counters

Revision ID: d7f3a1b9c2e4
Revises: c3d5e7f9a1b2
Create Date: 2026-08-20

Adds role-based access control plus the two admin-facing stores:

  * ``roles`` / ``user_roles`` — multi-role support, so one account can hold
    both ``admin`` and ``customer`` at the same time.
  * ``app_settings``           — admin-editable campaign parameters and limits.
  * ``rate_limit_counters``    — Postgres-backed API rate limiting (Redis is
    saturated with Celery job traffic on this deployment).

Every step is guarded so the migration is safe to re-run, matching the
existing idempotent style in this project. Existing users are backfilled from
the legacy ``users.role`` column, so nobody loses access.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7f3a1b9c2e4"
# Chains onto the current single head so Alembic keeps one linear history.
down_revision: Union[str, Sequence[str], None] = "c3d5e7f9a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "roles"):
        op.create_table(
            "roles",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("name", sa.String(length=50), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )
        op.create_index("ix_roles_name", "roles", ["name"], unique=True)

    if not _has_table(bind, "user_roles"):
        op.create_table(
            "user_roles",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_email", sa.String(), nullable=False),
            sa.Column("role_id", sa.Integer(), nullable=False),
            sa.Column("granted_by", sa.String(), nullable=True),
            sa.Column(
                "granted_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["user_email"], ["users.email"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "user_email", "role_id", name="uq_user_roles_user_role"
            ),
        )
        op.create_index("ix_user_roles_user_email", "user_roles", ["user_email"])
        op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"])

    if not _has_table(bind, "app_settings"):
        op.create_table(
            "app_settings",
            sa.Column("key", sa.String(length=120), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column(
                "value_type", sa.String(length=16), nullable=False, server_default="str"
            ),
            sa.Column(
                "category", sa.String(length=60), nullable=False, server_default="general"
            ),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("updated_by", sa.String(), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("key"),
        )
        op.create_index("ix_app_settings_category", "app_settings", ["category"])

    if not _has_table(bind, "rate_limit_counters"):
        op.create_table(
            "rate_limit_counters",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("identity", sa.String(length=320), nullable=False),
            sa.Column("bucket", sa.String(length=80), nullable=False),
            sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "request_count", sa.Integer(), nullable=False, server_default="0"
            ),
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
            sa.PrimaryKeyConstraint("id"),
            # The upsert conflict target — required by the atomic counter.
            sa.UniqueConstraint(
                "identity", "bucket", "window_started_at", name="uq_rate_limit_window"
            ),
        )
        op.create_index(
            "ix_rate_limit_counters_identity", "rate_limit_counters", ["identity"]
        )
        op.create_index(
            "ix_rate_limit_counters_bucket", "rate_limit_counters", ["bucket"]
        )
        op.create_index(
            "ix_rate_limit_window_started_at",
            "rate_limit_counters",
            ["window_started_at"],
        )

    # ── Seed the two roles (idempotent) ──────────────────────────────────────
    for name, description in (
        ("admin", "Full access: user management, settings, and limits"),
        ("customer", "Standard application access"),
    ):
        op.execute(
            sa.text(
                "INSERT INTO roles (name, description) VALUES (:name, :description) "
                "ON CONFLICT (name) DO NOTHING"
            ).bindparams(name=name, description=description)
        )

    # ── Backfill from the legacy users.role column ───────────────────────────
    # Existing accounts keep exactly the access they already had.
    op.execute(
        sa.text(
            """
            INSERT INTO user_roles (user_email, role_id, granted_by)
            SELECT u.email, r.id, 'migration'
            FROM users u
            JOIN roles r ON r.name = u.role
            ON CONFLICT (user_email, role_id) DO NOTHING
            """
        )
    )
    # Everyone can use the app, so guarantee the customer grant as well.
    op.execute(
        sa.text(
            """
            INSERT INTO user_roles (user_email, role_id, granted_by)
            SELECT u.email, r.id, 'migration'
            FROM users u
            CROSS JOIN roles r
            WHERE r.name = 'customer'
            ON CONFLICT (user_email, role_id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in (
        "rate_limit_counters",
        "app_settings",
        "user_roles",
        "roles",
    ):
        if _has_table(bind, table):
            op.drop_table(table)
