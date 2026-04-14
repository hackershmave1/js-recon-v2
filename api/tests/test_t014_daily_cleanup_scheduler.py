from app.tasks.celery_app import celery_app


def test_retention_cleanup_daily_schedule_registered():
    schedule = celery_app.conf.beat_schedule or {}
    assert "retention_cleanup_daily" in schedule

    entry = schedule["retention_cleanup_daily"]
    assert entry["task"] == "retention_cleanup"
    assert entry.get("kwargs", {}).get("dry_run") is False

    cron = entry["schedule"]
    minute = getattr(cron, "_orig_minute", "")
    hour = getattr(cron, "_orig_hour", "")
    assert str(minute) == "0"
    assert str(hour) == "3"
