"""allow several accounts per user (socials + Gmail)

Revision ID: g2h3i4j5k6l7
Revises: b4c8d1e2f3a0
Create Date: 2026-09-07

Multi-account connections:

  * ``social_platform_connections`` — the uniqueness moves from
    ``(owner_email, platform)`` (one connection per user per platform) to
    ``(owner_email, platform, account_id)`` (one row per *account*). A user
    can now connect two YouTube channels, two Facebook Pages, a personal and
    a work Instagram, …; the same account can never be connected twice under
    one user.
  * ``social_posts.platform_accounts`` — JSON map
    ``{platform: social_platform_connections.id}`` recording which account of
    each selected platform the user picked in the upload editor. Empty (the
    default) keeps publishing with the platform's first-connected account,
    so rows written before this change publish exactly as before.
  * ``gmail_connections`` — the one-mailbox-per-user constraint goes; a user
    can connect several mailboxes (personal + work). ``account_email`` stays
    globally unique (one mailbox = one live window into one inbox).

Every step is guarded so the migration is safe to re-run, matching the
idempotent style of the other files in this directory. The startup path runs
``Base.metadata.create_all`` before Alembic, so on a fresh database these
changes usually already exist by the time this revision runs.

SQLite note: CREATE TABLE UNIQUE constraints only surface as unnamed
auto-indexes, which SQLAlchemy's ``get_indexes()`` hides — detection here
uses ``PRAGMA index_list``/``index_info`` directly, and constraint changes
use the table-rebuild pattern (SQLite cannot ALTER constraints).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g2h3i4j5k6l7"
down_revision: Union[str, Sequence[str], None] = "b4c8d1e2f3a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONNECTIONS = "social_platform_connections"
POSTS = "social_posts"
GMAIL = "gmail_connections"

OLD_UNIQUE_COLUMNS = ("owner_email", "platform")
NEW_UNIQUE_COLUMNS = ("owner_email", "platform", "account_id")
NEW_UNIQUE_NAME = "uq_social_platform_connections_owner_platform_account"
NEW_INDEX_NAME = "ix_social_platform_connections_account"

GMAIL_OLD_UNIQUE_NAME = "uq_gmail_connections_owner_email"
GMAIL_OWNER_INDEX = "ix_gmail_connections_owner_email"


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _ensure_fk(
    bind,
    *,
    table: str,
    constraint_name: str,
    column: str,
    ref_table: str,
    ref_column: str,
    ondelete: str,
) -> None:
    """Add a foreign key if it is missing (idempotent).

    Postgres supports ALTER TABLE ADD CONSTRAINT. SQLite cannot add a FK to an
    existing table, so it is skipped there — the column still works, and the
    application layer validates ownership of any bound session id.
    """
    if bind.dialect.name == "sqlite":
        return
    existing = {
        fk["name"]
        for fk in sa.inspect(bind).get_foreign_keys(table)
    }
    if constraint_name in existing:
        return
    # A FK may exist under a different (auto-generated) name; detect by shape.
    for fk in sa.inspect(bind).get_foreign_keys(table):
        if column in fk.get("constrained_columns", []) and ref_table in (
            fk.get("referred_table") or ""
        ):
            return
    op.create_foreign_key(
        constraint_name,
        table,
        ref_table,
        [column],
        [ref_column],
        ondelete=ondelete,
    )


def _has_column(bind, table: str, name: str) -> bool:
    return any(column["name"] == name for column in sa.inspect(bind).get_columns(table))


def _index_columns(bind, table: str) -> dict[str, tuple[str, ...]]:
    """Named indexes (SQLAlchemy reflection) — Postgres and friends."""
    return {
        index["name"]: tuple(index["column_names"])
        for index in sa.inspect(bind).get_indexes(table)
    }


def _sqlite_indexes(bind, table: str) -> list[dict]:
    """Every index SQLite knows about, including the auto-indexes behind
    CREATE TABLE UNIQUE constraints (``get_indexes`` hides those).

    Each entry: {"name", "unique", "origin", "columns"}.
    """
    out = []
    for row in bind.execute(sa.text(f"PRAGMA index_list({table})")).fetchall():
        name, unique, origin = row[1], bool(row[2]), row[3]
        columns = [
            r[2] for r in bind.execute(sa.text(f"PRAGMA index_info({name})")).fetchall()
        ]
        out.append({"name": name, "unique": unique, "origin": origin, "columns": columns})
    return out


def _unique_present(bind, table: str, columns: tuple[str, ...]) -> bool:
    """True when a UNIQUE constraint/index exists on exactly ``columns``."""
    want = set(columns)
    if bind.dialect.name == "sqlite":
        return any(
            index["unique"] and set(index["columns"]) == want
            for index in _sqlite_indexes(bind, table)
        )
    for index in sa.inspect(bind).get_indexes(table):
        if set(index["column_names"]) == want and index.get("unique"):
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. social_posts.platform_accounts ──────────────────────────────────
    if _has_table(bind, POSTS) and not _has_column(bind, POSTS, "platform_accounts"):
        op.add_column(
            POSTS,
            sa.Column("platform_accounts", sa.JSON(), nullable=False, server_default="{}"),
        )

    # ── 2+3. social_platform_connections: swap the unique constraint ──────
    if _has_table(bind, CONNECTIONS):
        if bind.dialect.name == "sqlite":
            has_old = _unique_present(bind, CONNECTIONS, OLD_UNIQUE_COLUMNS)
            if has_old:
                # SQLite cannot drop a CREATE TABLE UNIQUE constraint; the
                # rebuild replaces it with the per-account one and recreates
                # the helper index in the same pass.
                _rebuild_sqlite_connections(bind)
            elif NEW_INDEX_NAME not in _sqlite_index_names(bind, CONNECTIONS):
                op.create_index(NEW_INDEX_NAME, CONNECTIONS, ["platform", "account_id"])
        else:
            indexes = _index_columns(bind, CONNECTIONS)
            already_new = NEW_UNIQUE_NAME in indexes
            # The old constraint surfaces as a unique index (or the unique
            # constraint itself) named after it; require the unique flag so a
            # plain index of the same shape could never be dropped by name.
            old_index_name = next(
                (
                    index["name"]
                    for index in sa.inspect(bind).get_indexes(CONNECTIONS)
                    if index.get("unique")
                    and set(index["column_names"]) == set(OLD_UNIQUE_COLUMNS)
                ),
                None,
            )
            if old_index_name is not None:
                op.drop_constraint(old_index_name, table_name=CONNECTIONS, type_="unique")
            if not already_new:
                op.create_unique_constraint(
                    NEW_UNIQUE_NAME,
                    CONNECTIONS,
                    list(NEW_UNIQUE_COLUMNS),
                )
            if NEW_INDEX_NAME not in _index_columns(bind, CONNECTIONS):
                op.create_index(NEW_INDEX_NAME, CONNECTIONS, ["platform", "account_id"])

    # ── 4. gmail_connections: drop the one-mailbox-per-user constraint ─────
    _upgrade_gmail(bind)

    # ── 5. whatsapp_scan_filters: bind each filter to a session ────────────
    # A user with several connected WhatsApp devices picks one per filter.
    # NULL (the default) keeps existing filters scanning the owner's default
    # (newest) session, so current automation is unchanged.
    FILTERS = "whatsapp_scan_filters"
    if _has_table(bind, FILTERS) and not _has_column(bind, FILTERS, "session_id"):
        op.add_column(
            FILTERS,
            sa.Column("session_id", sa.Integer(), nullable=True),
        )
        # The FK is added separately so a partial re-run (column present,
        # constraint not yet) does not re-add the column and fail.
        _ensure_fk(
            bind,
            table=FILTERS,
            constraint_name="fk_whatsapp_scan_filters_session_id",
            column="session_id",
            ref_table="whatsapp_sessions",
            ref_column="id",
            ondelete="SET NULL",
        )


def _sqlite_index_names(bind, table: str) -> set[str]:
    return {index["name"] for index in _sqlite_indexes(bind, table)}


def _rebuild_sqlite_connections(bind) -> None:
    """Replace the per-platform constraint with the per-account one.

    No other table references social_platform_connections, so dropping it
    inside the migration transaction is safe with foreign key checks on.
    """
    op.execute(
        sa.text(
            """
            CREATE TABLE social_platform_connections_new (
                id TEXT NOT NULL,
                owner_email TEXT NOT NULL,
                platform TEXT NOT NULL,
                encrypted_access_token TEXT NOT NULL,
                encrypted_refresh_token TEXT,
                expires_at DATETIME,
                account_name TEXT NOT NULL DEFAULT '',
                account_id TEXT NOT NULL DEFAULT '',
                extra_data JSON,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                CONSTRAINT uq_social_platform_connections_owner_platform_account
                    UNIQUE (owner_email, platform, account_id),
                FOREIGN KEY (owner_email) REFERENCES users (email)
                    ON DELETE CASCADE
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO social_platform_connections_new (
                id, owner_email, platform, encrypted_access_token,
                encrypted_refresh_token, expires_at, account_name,
                account_id, extra_data, created_at, updated_at
            )
            SELECT
                id, owner_email, platform, encrypted_access_token,
                encrypted_refresh_token, expires_at, account_name,
                account_id, extra_data, created_at, updated_at
            FROM social_platform_connections
            """
        )
    )
    op.execute(sa.text("DROP TABLE social_platform_connections"))
    op.execute(
        sa.text("ALTER TABLE social_platform_connections_new RENAME TO social_platform_connections")
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_social_platform_connections_account "
            "ON social_platform_connections (platform, account_id)"
        )
    )


def _upgrade_gmail(bind) -> None:
    """gmail_connections: a user may connect several mailboxes.

    Drops the single-column UNIQUE(owner_email) that capped each user at one
    mailbox and adds a plain index for the per-owner lookups. The
    account_email column stays globally unique, untouched here.
    """
    if not _has_table(bind, GMAIL):
        return

    if bind.dialect.name == "sqlite":
        if _unique_present(bind, GMAIL, ("owner_email",)):
            _rebuild_sqlite_gmail(bind)
        elif GMAIL_OWNER_INDEX not in _sqlite_index_names(bind, GMAIL):
            op.create_index(GMAIL_OWNER_INDEX, GMAIL, ["owner_email"])
        return

    owner_index_name = _unique_owner_email_index(bind, GMAIL)
    if owner_index_name is not None:
        # Postgres: the singleton constraint is a unique index — replace it
        # with a non-unique index of the same shape (leading owner_email).
        op.drop_constraint(owner_index_name, table_name=GMAIL, type_="unique")
    if GMAIL_OWNER_INDEX not in _index_columns(bind, GMAIL):
        op.create_index(GMAIL_OWNER_INDEX, GMAIL, ["owner_email"])


def _unique_owner_email_index(bind, table: str) -> str | None:
    """Name of a UNIQUE constraint/index on exactly (owner_email), else None.

    Postgres surfaces named unique constraints through the inspector; a
    unique index of the same shape (as created by older migration styles)
    is accepted as a fallback.
    """
    insp = sa.inspect(bind)
    for cons in insp.get_unique_constraints(table):
        if list(cons.get("column_names") or []) == ["owner_email"]:
            return cons.get("name")
    for index in insp.get_indexes(table):
        if list(index["column_names"]) == ["owner_email"] and index.get("unique"):
            return index["name"]
    return None


def _rebuild_sqlite_gmail(bind) -> None:
    """SQLite cannot drop a CREATE TABLE UNIQUE constraint; rebuild the table.

    No other table references gmail_connections, so dropping it inside the
    migration transaction is safe.
    """
    op.execute(
        sa.text(
            """
            CREATE TABLE gmail_connections_new (
                id TEXT NOT NULL,
                owner_email TEXT NOT NULL,
                account_email TEXT NOT NULL DEFAULT '',
                encrypted_access_token TEXT NOT NULL,
                encrypted_refresh_token TEXT,
                expires_at DATETIME,
                granted_scopes TEXT NOT NULL DEFAULT '',
                messages_total TEXT NOT NULL DEFAULT '',
                threads_total TEXT NOT NULL DEFAULT '',
                history_id TEXT NOT NULL DEFAULT '',
                last_checked_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                CONSTRAINT uq_gmail_connections_account_email UNIQUE (account_email),
                FOREIGN KEY (owner_email) REFERENCES users (email)
                    ON DELETE CASCADE
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO gmail_connections_new (
                id, owner_email, account_email, encrypted_access_token,
                encrypted_refresh_token, expires_at, granted_scopes,
                messages_total, threads_total, history_id, last_checked_at,
                created_at, updated_at
            )
            SELECT
                id, owner_email, account_email, encrypted_access_token,
                encrypted_refresh_token, expires_at, granted_scopes,
                messages_total, threads_total, history_id, last_checked_at,
                created_at, updated_at
            FROM gmail_connections
            """
        )
    )
    op.execute(sa.text("DROP TABLE gmail_connections"))
    op.execute(sa.text("ALTER TABLE gmail_connections_new RENAME TO gmail_connections"))
    op.execute(
        sa.text(
            "CREATE INDEX ix_gmail_connections_owner_email ON gmail_connections (owner_email)"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, POSTS) and _has_column(bind, POSTS, "platform_accounts"):
        op.drop_column(POSTS, "platform_accounts")

    if _has_table(bind, GMAIL):
        if bind.dialect.name != "sqlite":
            if GMAIL_OWNER_INDEX in _index_columns(bind, GMAIL):
                op.drop_index(GMAIL_OWNER_INDEX, table_name=GMAIL)
            if not _unique_present(bind, GMAIL, ("owner_email",)):
                op.create_unique_constraint(GMAIL_OLD_UNIQUE_NAME, GMAIL, ["owner_email"])
        # SQLite: restoring the singleton constraint needs a table rebuild;
        # the downgrade leaves it (extra mailboxes simply keep working).

    if not _has_table(bind, CONNECTIONS):
        return
    if bind.dialect.name == "sqlite":
        # SQLite cannot add back a CREATE TABLE constraint without another
        # rebuild; the downgrade is intentionally a no-op there.
        return
    indexes = _index_columns(bind, CONNECTIONS)
    if NEW_INDEX_NAME in indexes:
        op.drop_index(NEW_INDEX_NAME, table_name=CONNECTIONS)
    if NEW_UNIQUE_NAME in indexes:
        op.drop_constraint(NEW_UNIQUE_NAME, table_name=CONNECTIONS, type_="unique")
    old_index_name = next(
        (
            name
            for name, columns in indexes.items()
            if len(columns) == len(OLD_UNIQUE_COLUMNS)
            and set(columns) == set(OLD_UNIQUE_COLUMNS)
        ),
        None,
    )
    if old_index_name is None:
        op.create_unique_constraint(
            "uq_social_platform_connections_owner_platform",
            CONNECTIONS,
            list(OLD_UNIQUE_COLUMNS),
        )
