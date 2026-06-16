from app.services.retention_cleanup import run_retention_cleanup


def test_retention_cleanup_runs_without_task_queue(tmp_path):
    result = run_retention_cleanup(
        base_path=str(tmp_path),
        file_ttl_days=30,
        sourcemap_ttl_days=30,
        max_deletions=10,
        dry_run=True,
    )

    assert result["success"] is True
    assert result["dryRun"] is True
    assert result["summary"]["candidates"] == 0
    assert result["summary"]["deleted"] == 0
    assert result["guardrails"]["maxDeletionsPerRun"] == 10
