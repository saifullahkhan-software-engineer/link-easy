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


class _FlushStreamHandler(logging.StreamHandler):
    """StreamHandler that explicitly flushes stdout after every log record.

    On Windows, Python's ``sys.stdout`` is block-buffered when the process
    is launched from PowerShell / cmd rather than an interactive REPL.
    Without an explicit ``flush()`` call the API-call middleware logs (and
    other INFO lines) sit in the user-mode buffer and never reach the
    terminal until the process exits — giving the impression that logging
    is broken.
    """

    def emit(self, record):
        super().emit(record)
        self.flush()


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
        handler = _FlushStreamHandler(sys.stdout)
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
