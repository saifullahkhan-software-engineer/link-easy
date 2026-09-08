"""assistant conversations and messages

Revision ID: j5k6l7m8n9p0
Revises: i4j5k6l7m8n
Create Date: 2026-09-09

AI assistant chat history:

  * ``assistant_conversations`` — one row per conversation, owned by the
    user (owner_email FK → users.email, same ownership key as every other
    user-owned table). ``title`` holds the clipped first message for the
    history list.
  * ``assistant_messages`` — the turns (role: user | assistant | tool).
    Tool rows (the model's tool calls + their JSON results, which can
    contain capped previews of the user's inbox messages) are audit records:
    they are never replayed to the model and never returned by the history
    endpoint.

Every step is guarded so the migration is safe to re-run, matching the
idempotent style of this directory. The startup path runs
``Base.metadata.create_all`` before Alembic, so on a fresh database these
tables usually already exist by the time this revision runs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "j5k6l7m8n9p0"
down_revision: Union[str, Sequence[str], None] = "i4j5k6l7m8n"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONVERSATIONS = "assistant_conversations"
MESSAGES = "assistant_messages"


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_index(bind, table: str, name: str) -> bool:
    return any(idx["name"] == name for idx in sa.inspect(bind).get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, CONVERSATIONS):
        op.create_table(
            CONVERSATIONS,
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("owner_email", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["owner_email"], ["users.email"], ondelete="CASCADE"),
        )
    if not _has_index(bind, CONVERSATIONS, "ix_assistant_conversations_owner_updated"):
        op.create_index(
            "ix_assistant_conversations_owner_updated",
            CONVERSATIONS,
            ["owner_email", "updated_at"],
        )

    if not _has_table(bind, MESSAGES):
        op.create_table(
            MESSAGES,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("actions_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["conversation_id"], [f"{CONVERSATIONS}.id"], ondelete="CASCADE"
            ),
        )
    if not _has_index(bind, MESSAGES, "ix_assistant_messages_conversation"):
        op.create_index(
            "ix_assistant_messages_conversation",
            MESSAGES,
            ["conversation_id", "id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, MESSAGES):
        op.drop_index("ix_assistant_messages_conversation", table_name=MESSAGES)
        op.drop_table(MESSAGES)
    if _has_table(bind, CONVERSATIONS):
        op.drop_index("ix_assistant_conversations_owner_updated", table_name=CONVERSATIONS)
        op.drop_table(CONVERSATIONS)
