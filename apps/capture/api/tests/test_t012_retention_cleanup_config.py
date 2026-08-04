from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings
from app.services.retention_cleanup import run_retention_cleanup


def _write_file(path: Path, content: str, age_days: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    old = datetime.now(timezone.utc) - timedelta(days=age_days)
    timestamp = old.timestamp()
    path.touch()
    path.stat()
    # Use utime after touch so mtime reflects the requested age.
    import os
    os.utime(path, (timestamp, timestamp))


def test_settings_expose_retention_ttl_values():
    cfg = Settings(
        file_content_ttl_days=11,
        sourcemap_content_ttl_days=22,
        cleanup_max_deletions_per_run=333,
    )
    assert cfg.file_content_ttl_days == 11
    assert cfg.sourcemap_content_ttl_days == 22
    assert cfg.cleanup_max_deletions_per_run == 333


def test_retention_cleanup_uses_ttl_and_dry_run(tmp_path):
    session_dir = tmp_path / "sessions" / "session-1"
    old_file = session_dir / "files" / "old.js"
    new_file = session_dir / "files" / "new.js"
    old_map = session_dir / "maps" / "old.map"

    _write_file(old_file, "old-file", age_days=40)
    _write_file(new_file, "new-file", age_days=2)
    _write_file(old_map, "old-map", age_days=12)

    dry_result = run_retention_cleanup(
        base_path=str(tmp_path),
        file_ttl_days=30,
        sourcemap_ttl_days=10,
        dry_run=True,
    )
    assert dry_result["dryRun"] is True
    assert dry_result["ttlDays"]["fileContent"] == 30
    assert dry_result["ttlDays"]["sourceMapContent"] == 10
    assert dry_result["summary"]["selectedForDeletion"] == 2
    assert dry_result["guardrails"]["maxDeletionsPerRun"] == 500
    assert dry_result["events"][0]["event"] == "cleanup_start"
    assert dry_result["events"][-1]["event"] == "cleanup_finished"
    assert dry_result["summary"]["candidates"] == 2
    assert old_file.exists() is True
    assert old_map.exists() is True

    apply_result = run_retention_cleanup(
        base_path=str(tmp_path),
        file_ttl_days=30,
        sourcemap_ttl_days=10,
        dry_run=False,
    )
    assert apply_result["dryRun"] is False
    assert apply_result["summary"]["deleted"] == 2
    assert old_file.exists() is False
    assert old_map.exists() is False
    assert new_file.exists() is True


def test_retention_cleanup_caps_deletions(tmp_path):
    session_dir = tmp_path / "sessions" / "session-cap"
    old_file_a = session_dir / "files" / "old-a.js"
    old_file_b = session_dir / "files" / "old-b.js"
    old_map = session_dir / "maps" / "old.map"

    _write_file(old_file_a, "old-a", age_days=45)
    _write_file(old_file_b, "old-b", age_days=44)
    _write_file(old_map, "old-map", age_days=40)

    result = run_retention_cleanup(
        base_path=str(tmp_path),
        file_ttl_days=10,
        sourcemap_ttl_days=10,
        max_deletions=2,
        dry_run=False,
    )

    assert result["summary"]["candidates"] == 3
    assert result["summary"]["selectedForDeletion"] == 2
    assert result["summary"]["deleted"] == 2
    assert result["summary"]["skippedDueToCap"] == 1
    assert result["summary"]["capped"] is True
    assert len(result["deletedPaths"]) == 2

    remaining = [path for path in (old_file_a, old_file_b, old_map) if path.exists()]
    assert len(remaining) == 1
