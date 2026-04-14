from celery import Celery
from celery.schedules import crontab

from ..config import settings


celery_app = Celery(
    "js_extractor",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    beat_schedule={
        "retention_cleanup_daily": {
            "task": "retention_cleanup",
            "schedule": crontab(hour=3, minute=0),
            "kwargs": {"dry_run": False},
        },
    },
)

# Explicit imports register task decorators during module import.
from . import process_file  # noqa: E402,F401
from . import enhanced_processing  # noqa: E402,F401
from . import retention_cleanup  # noqa: E402,F401
