"""Regression coverage for Social Scheduler schema repairs.

An existing production ``social_posts`` table is not changed by
``Base.metadata.create_all()``.  These tests execute the targeted Alembic
revision against that legacy shape, which is the state that previously made
``GET /social-scheduler/posts`` fail with UndefinedColumnError.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT
    / "migrations"
    / "versions"
    / "i4j5k6l7m8n_add_youtube_playlists_by_account.py"
)


def _migration_module():
    spec = spec_from_file_location("add_youtube_playlists_by_account", MIGRATION_PATH)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade(connection, migration) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.upgrade()


def test_per_account_playlist_migration_repairs_a_legacy_social_posts_table():
    engine = create_engine("sqlite://")
    migration = _migration_module()

    try:
        with engine.begin() as connection:
            # This represents an already-running installation: it contains the
            # older playlist column but not the newer per-account JSON mapping.
            connection.execute(
                text(
                    "CREATE TABLE social_posts ("
                    "id VARCHAR PRIMARY KEY, youtube_playlist_ids JSON NOT NULL DEFAULT '[]'"
                    ")"
                )
            )
            connection.execute(text("INSERT INTO social_posts (id) VALUES ('legacy-post')"))

            _upgrade(connection, migration)
            assert "youtube_playlists_by_account" in {
                column["name"] for column in inspect(connection).get_columns("social_posts")
            }
            assert connection.execute(
                text("SELECT youtube_playlists_by_account FROM social_posts WHERE id = 'legacy-post'")
            ).scalar_one() == "{}"

            # A restarted service invokes `alembic upgrade head` again. The
            # inspector guard must keep that retry harmless.
            _upgrade(connection, migration)
    finally:
        engine.dispose()
