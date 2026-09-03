"""
Celery application instance.
FILE: worker/celery_app.py
 
Start the worker with:
    celery -A worker.celery_app worker --loglevel=info --concurrency=2 --pool=prefork -Q linkedin_sessions,default
"""
import os

from celery import Celery
from core.config import settings
from core.security import validate_encryption_key

# Fail loudly at worker startup if the credential encryption key is missing
# or malformed — better than failing mid-task on a decrypt.
validate_encryption_key()

celery_app = Celery(
    "LinkeFlow",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "worker.tasks.campaign_tasks",
        "worker.tasks.feed_scroll_tasks",
        "worker.tasks.whatsapp_tasks",
        "worker.tasks.social_scheduler_tasks",
    ],
)
 
celery_app.conf.update(
    # Serialisation
    task_serializer          = "json",
    result_serializer        = "json",
    accept_content           = ["json"],
 
    # Timezone — always UTC internally
    timezone                 = "UTC",
    enable_utc               = True,
 
    # Keep reconnecting when the worker starts (required explicitly by Celery 6).
    broker_connection_retry_on_startup = True,

    # Task behaviour
    task_acks_late           = True,    # Acknowledge AFTER task finishes (not before)
    task_reject_on_worker_lost = True,  # Re-queue if worker dies mid-task
    worker_prefetch_multiplier = 1,     # One task at a time per worker (important for browser tasks)
 
    # Result expiry
    result_expires           = 86400,   # Keep results for 24 hours

    # Never reuse a checked-in/local Beat database.  A persistent Beat file
    # contains old task names and can resurrect schedules after code is
    # deleted.  Use an ephemeral path by default; operators can override it
    # when they explicitly want a durable Beat database.
    beat_schedule_filename   = os.getenv(
        "CELERY_BEAT_SCHEDULE_FILE",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "celerybeat-schedule")
    ),

    # Celery Beat schedule for periodic tasks.
    #
    # Local and self-hosted environments keep the complete scheduler. The
    # public demo is intentionally different: Beat remains alive for social
    # uploads, but does not wake the browser for LinkedIn drip sessions, feed
    # scans, or recurring WhatsApp scans. This keeps demo traffic from
    # saturating Celery while preserving user-triggered operations.
    beat_schedule = {} if not settings.scheduled_jobs_enabled else (
        {
            'dispatch-due-social-posts': {
                'task': 'tasks.dispatch_due_social_posts',
                'schedule': 60.0,
            },
        }
        if settings.is_deployment else {
            # Database-backed dispatch survives worker restarts unlike long
            # ETA tasks held in a worker's in-memory timer.
            'dispatch-due-account-sessions': {
                'task': 'tasks.dispatch_due_account_sessions',
                'schedule': 60.0,
            },
            'dispatch-due-feed-scans': {
                'task': 'tasks.dispatch_due_feed_scans',
                'schedule': 60.0,
            },
            'dispatch-due-whatsapp-scans': {
                'task': 'tasks.dispatch_due_whatsapp_scans',
                'schedule': 60.0,
            },
            'dispatch-due-social-posts': {
                'task': 'tasks.dispatch_due_social_posts',
                'schedule': 60.0,
            },
        }
    ),
)
