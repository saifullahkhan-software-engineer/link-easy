"""
In-process async event hub for live streams.

FILE: core/live_hub.py

Two kinds of events flow through hubs in this app:

  * ``log_hub``  — Application log records (from the logging handler).
                   Used for backward compatibility; in production, logs
                   go to the terminal/backend, not the frontend.
  * browser view hub (owned by ``services.browser_view``) — screencast JPEG
                   frames + status events. Consumed by the
                   ``/api/v1/live/browser/stream`` SSE stream.

Hubs are purely in-process: FastAPI and the browser manager share the same
event loop, so a publish from either side reaches every subscriber with no
external broker needed.  Celery worker processes are separate, so their log
lines are NOT streamed here — API-call and in-process logs are.
"""
import asyncio
import logging
import os
import time
from collections import deque
from typing import Deque, Optional, Set


def _should_stream_logs() -> bool:
    """Check if logs should be streamed to frontend (dev only)."""
    env = os.environ.get("STREAM_LOGS_TO_FRONTEND", "").lower()
    return env in ("true", "1", "yes")


class EventHub:
    """Async pub/sub hub with optional bounded history for late joiners."""

    def __init__(self, max_history: int = 0):
        self._max_history = max_history
        self._subscribers: Set[asyncio.Queue] = set()
        self._history: Deque[dict] = deque(maxlen=max_history) if max_history else deque()
        self._lock = asyncio.Lock()

    async def publish(self, event: dict) -> None:
        """Broadcast an event dict to every subscriber (best-effort)."""
        event = dict(event)
        event.setdefault("ts", time.time())

        async with self._lock:
            if self._max_history:
                self._history.append(event)
            subscribers = list(self._subscribers)

        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest queued item so the stream keeps moving.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    def history(self, limit: Optional[int] = None) -> list[dict]:
        """Recent events (for replay when a subscriber connects)."""
        items = list(self._history)
        return items[-limit:] if limit else items

    async def subscribe(self) -> asyncio.Queue:
        """Register a subscriber; returns its own asyncio.Queue."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)


# Singleton used for API-call logs + app log records.
log_hub = EventHub(max_history=200)


class HubLogHandler(logging.Handler):
    """logging.Handler that forwards records to an EventHub.

    The handler is attached to the root logger (and a few uvicorn loggers)
    during FastAPI startup.  ``emit`` is synchronous (logging contract), so it
    schedules the async publish on whichever loop is running — either the
    current loop when called from inside it, or the app loop captured at
    startup via :meth:`bind_loop`.

    In production, logs are written to the terminal/backend only.
    Set STREAM_LOGS_TO_FRONTEND=true to enable frontend log streaming (dev only).
    """

    def __init__(self, hub: EventHub = log_hub, level: int = logging.INFO):
        super().__init__(level=level)
        self.hub = hub
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the app's event loop so records from other threads can publish."""
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        # In production, logs go to terminal only — don't stream to frontend
        if not _should_stream_logs():
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self._loop

        if loop is None or loop.is_closed():
            return

        event = {
            "type": "app",
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
            "ts": record.created,
        }

        try:
            if loop.is_running():
                loop.create_task(self.hub.publish(event))
            else:
                asyncio.run_coroutine_threadsafe(self.hub.publish(event), loop)
        except Exception:
            # Logging must never crash the request that triggered it.
            pass
