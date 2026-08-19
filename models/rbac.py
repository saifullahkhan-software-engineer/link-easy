"""
Role-based access control tables.

FILE: models/rbac.py

Three tables:

  * ``roles``       — one row per assignable role ("admin", "customer").
                      Seeded by the migration; new roles can be added with a
                      plain INSERT and need no code change.
  * ``user_roles``  — join table. A user may hold SEVERAL roles at once, which
                      is what lets one account see both the Admin Dashboard and
                      the App Dashboard buttons.
  * ``app_settings``— single-row-per-key store for admin-editable campaign
                      parameters, job limits, and rate-limit windows.

``users.role`` (the original single-value column) is kept in sync as the
"primary" role so older code paths and existing JWTs keep working. The join
table is the source of truth; ``users.role`` is a denormalised cache of the
highest-privilege role a user holds.
"""
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from database import Base


class Role(Base):
    """An assignable role. Seeded with 'admin' and 'customer'."""

    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Matches models.roles.UserRole values so both layers agree.
    name = Column(String(50), unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserRoleLink(Base):
    """Join row granting one role to one user."""

    __tablename__ = "user_roles"
    __table_args__ = (
        # A role can only be granted to a user once. This is what makes the
        # grant SQL safely re-runnable via ON CONFLICT DO NOTHING.
        UniqueConstraint("user_email", "role_id", name="uq_user_roles_user_role"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_email = Column(
        String,
        ForeignKey("users.email", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role_id = Column(
        Integer,
        ForeignKey("roles.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # Who granted it — useful when more than one admin exists later.
    granted_by = Column(String, nullable=True)
    granted_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppSetting(Base):
    """Admin-editable configuration, stored as typed key/value rows.

    Used for campaign parameters (daily connection/message/visit limits),
    job limits, and the Postgres rate-limit windows. Values are stored as
    text and coerced by ``services.app_settings`` so a single table can hold
    ints, floats, booleans, and strings without a migration per knob.
    """

    __tablename__ = "app_settings"

    key = Column(String(120), primary_key=True)
    value = Column(Text, nullable=False)
    # int | float | bool | str — drives coercion on read.
    value_type = Column(String(16), nullable=False, default="str")
    category = Column(String(60), nullable=False, default="general", index=True)
    description = Column(Text, nullable=True)
    updated_by = Column(String, nullable=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
