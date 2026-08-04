"""T-027 — recon crawl resilience to non-recoverable ingest failures.

Covers the two non-ingestion halves of the DB-integrity crash fix:

* ``ReconJobRunner._flush_ingest_batch`` must degrade a non-recoverable batch
  error (an IntegrityError from a concurrent writer, or any other hard error) to
  "these files not stored this run" instead of re-raising and aborting the crawl.
* ``run_recon_job_worker`` must roll back a poisoned transaction and record a
  typed, non-empty ``error`` on the job (the observed bug was an EMPTY job.error
  on failed jobs), never silently swallowing a finalize failure.

These are pure-Python unit tests with mock sessions — no DB — so they run on the
host regardless of the SQLite/UUID limitation that blocks the other recon suites.
"""
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

import app.services.recon_job_runner as runner_mod
from app.services.recon_job_runner import ReconJobRunner, ReconRunnerOptions
from app.api.routes.ingestion import FileIn
from app.api.routes import recon


def _runner_with_one_asset(url: str) -> ReconJobRunner:
    options = ReconRunnerOptions(urls=[url], session_id=str(uuid.uuid4()), perform_analysis=True)
    runner = ReconJobRunner(options=options, db=MagicMock())
    runner.assets = {runner._canonical_url(url): {"url": url, "discoveredAt": "2026-08-04T00:00:00"}}
    return runner


def _batch_for(url: str) -> list[FileIn]:
    return [FileIn(url=url, contentHash="h" * 8, sessionId="s", contentLength=3, content="abc")]


def test_flush_ingest_batch_skips_on_integrity_error_and_continues(monkeypatch):
    """A concurrent-collision IntegrityError degrades to skip-and-continue: no raise,
    the transaction is rolled back, and the batch's assets are flagged as skipped."""
    url = "https://example.com/collide.js"
    runner = _runner_with_one_asset(url)

    def raise_integrity(*args, **kwargs):
        raise IntegrityError("dup", None, Exception("files_session_content_unique"))

    monkeypatch.setattr(runner_mod, "save_files", raise_integrity)

    # Must NOT raise — the crawl survives.
    runner._flush_ingest_batch(_batch_for(url))

    runner.db.rollback.assert_called()  # poisoned tx cleared
    asset = runner.assets[runner._canonical_url(url)]
    assert asset["ingested"] is False
    assert asset["ingestSkipped"] is True
    assert asset["ingestConflict"] is True  # tagged as a concurrency conflict


def test_flush_ingest_batch_skips_on_generic_non_recoverable_error(monkeypatch):
    """Defensively, ANY non-recoverable batch error (not just IntegrityError) is
    skipped rather than aborting the crawl — but is not tagged a conflict."""
    url = "https://example.com/boom.js"
    runner = _runner_with_one_asset(url)

    def raise_generic(*args, **kwargs):
        raise ValueError("unexpected boom")

    monkeypatch.setattr(runner_mod, "save_files", raise_generic)

    runner._flush_ingest_batch(_batch_for(url))

    runner.db.rollback.assert_called()
    asset = runner.assets[runner._canonical_url(url)]
    assert asset["ingestSkipped"] is True
    assert asset["ingestConflict"] is False


def test_recoverable_ingest_error_still_retries_capture_only(monkeypatch):
    """The existing recoverable-error path (jsonb overflow) is preserved: it retries
    capture-only rather than skipping the batch."""
    url = "https://example.com/big.js"
    runner = _runner_with_one_asset(url)
    calls = {"n": 0}

    def flaky_save(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # First (with-analysis) attempt overflows jsonb — a recoverable error.
            raise ValueError("total size of jsonb array elements exceeds the maximum")
        # Capture-only retry succeeds.
        return {"stored": 1, "fileIds": ["fid"], "files": [{"url": url, "fileId": "fid", "analysis": {"status": "skipped"}}]}

    monkeypatch.setattr(runner_mod, "save_files", flaky_save)

    runner._flush_ingest_batch(_batch_for(url))

    assert calls["n"] == 2  # retried, did not skip
    asset = runner.assets[runner._canonical_url(url)]
    assert asset.get("ingested") is True
    assert asset.get("ingestSkipped") is not True


def test_worker_records_typed_error_and_rolls_back_on_failure(monkeypatch):
    """run_recon_job_worker rolls back first, then finalizes the job with a typed,
    non-empty error — fixing the empty-job.error symptom."""

    class _FailingRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            raise RuntimeError("kaboom in the crawl")

    captured: dict = {}

    def _capture_finalize(job_id, status, result=None, error=None, db_session=None):
        captured["status"] = status
        captured["error"] = error

    monkeypatch.setattr(recon, "ReconJobRunner", _FailingRunner)
    monkeypatch.setattr(recon, "finalize_job", _capture_finalize)

    db = MagicMock()
    job_row = MagicMock()
    job_row.state_json = {}
    db.query.return_value.filter.return_value.first.return_value = job_row

    job_id = str(uuid.uuid4())
    options = ReconRunnerOptions(urls=["https://example.com"], session_id=str(uuid.uuid4()))

    # Should not propagate — the worker must handle its own failure.
    recon.run_recon_job_worker(job_id, options, worker_session_factory=lambda: db)

    db.rollback.assert_called()  # cleared the poisoned tx before finalize
    db.close.assert_called()
    assert captured["status"] == "failed"
    assert captured["error"], "job.error must not be empty on a failed job"
    assert captured["error"].startswith("RuntimeError")  # typed
    assert "kaboom in the crawl" in captured["error"]
