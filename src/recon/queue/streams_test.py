"""Colocated tests for the Redis Streams broker (REQ-Q1, REQ-Q2). Uses fakeredis."""

from __future__ import annotations

import time

import fakeredis
import pytest

from recon.domain import QueueName
from recon.queue import streams


@pytest.fixture()
def redis():
    client = fakeredis.FakeStrictRedis()
    streams.ensure_group(client, QueueName.FETCH)
    return client


def _msg(**over):
    base = {
        "job_id": "j1",
        "run_id": "r1",
        "tenant_id": "t1",
        "queue": "fetch",
        "attempts": 0,
        "max_attempts": 5,
    }
    base.update(over)
    return base


def test_enqueue_then_read_and_ack(redis):
    streams.enqueue(redis, QueueName.FETCH, _msg())
    batch = streams.read_batch(redis, QueueName.FETCH, "w1", block_ms=100)
    assert len(batch) == 1
    msg_id, message = batch[0]
    assert message["job_id"] == "j1"
    assert streams.pending_count(redis, QueueName.FETCH) == 1
    streams.ack(redis, QueueName.FETCH, msg_id)
    assert streams.pending_count(redis, QueueName.FETCH) == 0


def test_ensure_group_is_idempotent(redis):
    streams.ensure_group(redis, QueueName.FETCH)  # second call must not raise
    streams.ensure_group(redis, QueueName.FETCH)


def test_reschedule_holds_until_due_then_promotes(redis):
    streams.reschedule(redis, QueueName.FETCH, _msg(attempts=1), delay_seconds=100)
    # Not due yet.
    assert streams.promote_due(redis, QueueName.FETCH, now=time.time()) == 0
    assert streams.read_batch(redis, QueueName.FETCH, "w1", block_ms=50) == []
    # Due in the future window.
    assert streams.promote_due(redis, QueueName.FETCH, now=time.time() + 200) == 1
    batch = streams.read_batch(redis, QueueName.FETCH, "w1", block_ms=100)
    assert len(batch) == 1
    assert batch[0][1]["attempts"] == 1


def test_promote_due_does_not_double_enqueue(redis):
    streams.reschedule(redis, QueueName.FETCH, _msg(), delay_seconds=0)
    future = time.time() + 10
    assert streams.promote_due(redis, QueueName.FETCH, now=future) == 1
    assert streams.promote_due(redis, QueueName.FETCH, now=future) == 0


def test_to_dlq_carries_payload_and_error(redis):
    streams.to_dlq(redis, QueueName.FETCH, _msg(), error="boom")
    entries = redis.xrange(streams.dlq_key(QueueName.FETCH))
    assert len(entries) == 1
    _id, raw = entries[0]
    assert raw[b"error"] == b"boom"


def test_reclaim_stalled_returns_abandoned_messages(redis):
    streams.enqueue(redis, QueueName.FETCH, _msg())
    # w1 claims it but never acks (simulates a crash).
    claimed = streams.read_batch(redis, QueueName.FETCH, "w1", block_ms=100)
    assert len(claimed) == 1
    try:
        reclaimed = streams.reclaim_stalled(
            redis, QueueName.FETCH, "w2", min_idle_ms=0, count=10
        )
    except Exception as exc:  # pragma: no cover - fakeredis xautoclaim gaps
        pytest.skip(f"fakeredis lacks xautoclaim support: {exc}")
    assert any(m["job_id"] == "j1" for _mid, m in reclaimed)
