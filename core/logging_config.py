"""
Logging configuration helper for production vs development separation.

A single root StreamHandler is attached the first time get_logger() is
called.  Uvicorn's own loggers (uvicorn, uvicorn.error, uvicorn.access)
still work because they propagate to root only if they don't already have
a handler; we skip attaching if root is already configured.
"""
import logging
import sys

from core.config import settings


_CONFIGURED = False


def is_development() -> bool:
    """Check if running in development environment."""
    return settings.ENVIRONMENT.lower() == "development" or settings.DEBUG


def should_log_debug() -> bool:
    """Check if debug logging should be enabled."""
    return is_development()


def should_take_screenshots() -> bool:
    """Check if screenshots should be taken (only in development)."""
    return is_development()


def _configure_root() -> None:
    """Attach a single StreamHandler to the root logger (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()

    # If something else (e.g. uvicorn) already configured logging, respect it
    # and just make sure the level matches our environment.
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(levelname)-7s - %(name)s - %(message)s"
            )
        )
        root.addHandler(handler)

    root.setLevel(logging.DEBUG if is_development() else logging.INFO)

    # Tame chatty third-party loggers — keep them at INFO/WARNING so real
    # errors still surface but routine traffic doesn't drown the console.
    for noisy in ("httpx", "httpcore", "urllib3", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("alembic").setLevel(logging.INFO)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Get a logger wired up to the root console handler."""
    _configure_root()
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if is_development() else logging.INFO)
    # Records bubble up to the root handler we attached in _configure_root().
    logger.propagate = True
    return logger
