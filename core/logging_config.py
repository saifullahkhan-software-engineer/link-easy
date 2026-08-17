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


def _configure_root(force: bool = False) -> None:
    """Attach a StreamHandler to the root logger and guarantee it stays.

    Alembic's ``migrations/env.py`` calls ``logging.config.fileConfig``
    which by default wipes existing handlers and sets root level to WARNING.
    That makes it look like logs disappear after ``Running database migrations``
    finishes.  This helper is therefore resilient:

    * If ``force=False`` and we already configured, it still ensures a stdout
      handler exists and the level is correct — so a prior fileConfig wipe
      gets repaired on the next ``get_logger()`` call.
    * If ``force=True`` it fully reinstalls the handler (used after migrations
      in the FastAPI lifespan).

    Uvicorn's own loggers (uvicorn, uvicorn.error, uvicorn.access) are also
    ensured to propagate to root and have at least INFO level so startup
    messages are visible in the terminal.
    """
    global _CONFIGURED

    root = logging.getLogger()

    target_level = logging.DEBUG if is_development() else logging.INFO

    if _CONFIGURED and not force:
        # Repair if fileConfig wiped our handler or downgraded level.
        has_stdout_handler = any(
            isinstance(h, _FlushStreamHandler) or
            (isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) in (sys.stdout, sys.__stdout__))
            for h in root.handlers
        )
        if not has_stdout_handler:
            # Remove Alembic's generic stderr handler that fileConfig left
            # behind — otherwise we get duplicate lines (stderr generic +
            # stdout formatted) until reset_logging() runs.
            for h in list(root.handlers):
                if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                    try:
                        fmt = getattr(h.formatter, "_fmt", "") if h.formatter else ""
                        is_generic = "%(levelname)" in str(fmt) and "%(name)s" in str(fmt)
                    except Exception:
                        is_generic = False
                    # If it's going to stderr and looks like alembic's generic formatter, drop it
                    if getattr(h, "stream", None) in (sys.stderr, sys.__stderr__) and is_generic:
                        root.removeHandler(h)
            handler = _FlushStreamHandler(sys.stdout)
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(levelname)-7s - %(name)s - %(message)s"
                )
            )
            root.addHandler(handler)
        # Always enforce our desired level — Alembic used to set WARNING which hid INFO.
        if root.level != target_level:
            root.setLevel(target_level)
        # Ensure uvicorn loggers propagate too
        for uv_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(uv_name)
            lg.setLevel(target_level)
            lg.propagate = True
        return

    # force=True or first time: (re)install
    if force:
        # On force, wipe ALL existing StreamHandlers (including Alembic's
        # stderr handler from fileConfig) and reinstall a single flushing
        # stdout handler. This prevents duplicate lines (stderr generic
        # + stdout formatted) that appeared when both handlers co-existed.
        for h in list(root.handlers):
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                root.removeHandler(h)
        # After wipe, we will unconditionally add our handler below.

    # Ensure at least one flushing stdout handler
    has_stdout = any(
        isinstance(h, _FlushStreamHandler) for h in root.handlers
    )
    if not has_stdout or force:
        handler = _FlushStreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(levelname)-7s - %(name)s - %(message)s"
            )
        )
        root.addHandler(handler)

    root.setLevel(target_level)

    # Tame chatty third-party loggers — keep them at INFO/WARNING so real
    # errors still surface but routine traffic doesn't drown the console.
    for noisy in ("httpx", "httpcore", "urllib3", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("alembic").setLevel(logging.INFO)

    # Make uvicorn loggers share the same level and propagate to root so their
    # messages use our formatter/handler and are visible in terminal.
    for uv_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(uv_name)
        lg.setLevel(target_level)
        # Don't add duplicate handlers — let it propagate to root.
        lg.propagate = True

    _CONFIGURED = True


def reset_logging() -> None:
    """Force re-configure after Alembic wipes logging."""
    _configure_root(force=True)


def get_logger(name: str) -> logging.Logger:
    """Get a logger wired up to the root console handler."""
    _configure_root()
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if is_development() else logging.INFO)
    # Records bubble up to the root handler we attached in _configure_root().
    logger.propagate = True
    return logger
