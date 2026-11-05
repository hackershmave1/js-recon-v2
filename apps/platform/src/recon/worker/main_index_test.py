"""The worker serves the INDEX queue through its own handler: an index message never reaches the
run-stage path (it has no job_id/stage and would raise, un-acked, forever)."""

from __future__ import annotations

import fakeredis

from recon.domain import QueueName
from recon.probe import source_index
from recon.queue import streams
from recon.worker import main


def test_index_messages_go_to_the_index_handler_and_are_acked(monkeypatch):
    redis = fakeredis.FakeRedis(decode_responses=True)
    seen = []
    monkeypatch.setattr(source_index, "handle", lambda r, m: seen.append(m) or "done")
    monkeypatch.setattr(main, "SERVED_QUEUES", (QueueName.INDEX,))
    streams.ensure_group(redis, QueueName.INDEX)
    streams.enqueue(redis, QueueName.INDEX, {"tenant_id": "t", "run_id": "r", "cursor": 0})
    assert main.run_once(redis, "c", block_ms=0) == 1
    assert seen and seen[0]["run_id"] == "r"
    assert redis.xpending(streams.queue_key(QueueName.INDEX), streams.GROUP)["pending"] == 0


def test_index_is_served_last_one_message_at_a_time():
    # A backlog of index slices must not hold up a run's stages.
    assert main.SERVED_QUEUES[-1] is QueueName.INDEX
    assert main.BATCH_FOR[QueueName.INDEX] == 1
