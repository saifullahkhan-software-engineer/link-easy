from alembic.config import Config
from alembic import command

def run_migrations():
    """Applies all pending database migrations."""
    print("Running database migrations...")
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    print("Migrations complete.")

if __name__ == "__main__":
    run_migrations()
