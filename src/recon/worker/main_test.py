"""Unit tests for worker failure routing (no infra).

The DB/queue side effects (finish_job, reschedule, ack) are stubbed so the pure
routing decision — how a retry delay is chosen — is tested in isolation.
"""

from __future__ import annotations

from recon.domain import QueueName, RunStage
from recon.progress import heartbeat as progress
from recon.queue import retry, streams
from recon.worker import main as worker


def _stub_side_effects(monkeypatch) -> dict:
    captured: dict = {}
    monkeypatch.setattr(progress, "finish_job", lambda *a, **k: True)
    monkeypatch.setattr(streams, "ack", lambda *a, **k: None)
    monkeypatch.setattr(
        streams,
        "reschedule",
        lambda redis, queue, message, delay: captured.__setitem__("delay", delay),
    )
    return captured


def _handle(exc):
    return worker._handle_failure(
        None,
        QueueName.FETCH,
        "1-0",
        {"attempts": 0, "max_attempts": 5},
        exc,
        tenant_id="t",
        run_id="r",
        job_id="j",
        stage=RunStage.FETCHING,
        attempts=0,
        max_attempts=5,
    )


def test_retry_after_is_a_lower_bound_on_the_backoff(monkeypatch):
    # A politeness throttle asks for 30s; the sampled exponential backoff (<=1s for
    # attempt 1) must never undercut it (REQ-Q3).
    captured = _stub_side_effects(monkeypatch)
    result = _handle(retry.RetryableError("throttled", retry_after=30.0))
    assert result == "retry"
    assert captured["delay"] >= 30.0


def test_no_retry_after_uses_plain_backoff(monkeypatch):
    captured = _stub_side_effects(monkeypatch)
    result = _handle(retry.RetryableError("transient"))
    assert result == "retry"
    # attempt 1 backoff ceiling is base_delay (1.0); no artificial floor applied.
    assert 0.0 <= captured["delay"] <= 1.0
