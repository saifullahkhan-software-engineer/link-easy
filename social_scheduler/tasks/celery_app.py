from celery import Celery
from core.config import settings

celery_app = Celery(
    "social_scheduler",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["tasks.scheduler"]
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    worker_prefetch_multiplier=1,
)
