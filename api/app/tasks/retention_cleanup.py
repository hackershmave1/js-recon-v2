from .celery_app import celery_app
from ..services.retention_cleanup import run_retention_cleanup


@celery_app.task(name="retention_cleanup")
def retention_cleanup_task(
    dry_run: bool = True,
    max_deletions: int | None = None,
) -> dict:
    return run_retention_cleanup(
        dry_run=dry_run,
        max_deletions=max_deletions,
    )
