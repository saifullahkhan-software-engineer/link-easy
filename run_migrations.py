import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic import command


def run_migrations() -> None:
    """Applies all pending database migrations.

    Uses an absolute path for alembic.ini so the command works regardless of
    the current working directory — important on Windows where the project
    path contains a space (e.g. ``Linkdin Automation``) and ``Config("alembic.ini")``
    would otherwise resolve relative to the wrong folder.
    """
    print("Running database migrations...", flush=True)

    # Resolve alembic.ini relative to *this* file, not the CWD.
    here = Path(__file__).resolve().parent
    ini_path = here / "alembic.ini"
    if not ini_path.exists():
        # Fallback to CWD for backwards compatibility
        ini_path = Path("alembic.ini")

    if not ini_path.exists():
        raise FileNotFoundError(f"alembic.ini not found (looked in {here} and CWD)")

    # Ensure DATABASE_URL is present before Alembic tries to connect —
    # otherwise env.py would set sqlalchemy.url to "None" and the only
    # clue would be a cryptic driver error after "Will assume transactional DDL."
    if not os.getenv("DATABASE_URL"):
        # Try to load .env from the project root (migrations/env.py also does
        # this, but we fail here with a clear message before Alembic hides it)
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path=here / ".env")
        except Exception:
            pass
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError(
            "DATABASE_URL is not set. Set it in your .env file or environment. "
            f"Looked for .env in {here}"
        )

    alembic_cfg = Config(str(ini_path))
    # When alembic.ini contains %(here)s it must point at the real file location
    alembic_cfg.set_main_option("script_location", str(here / "migrations"))

    try:
        command.upgrade(alembic_cfg, "head")
    except Exception:
        print("Migration failed — see traceback above.", file=sys.stderr, flush=True)
        raise

    print("Migrations complete.", flush=True)


if __name__ == "__main__":
    run_migrations()
