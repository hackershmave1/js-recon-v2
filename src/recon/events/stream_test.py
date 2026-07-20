"""Colocated tests for the per-run Redis event stream + SSE replay (REQ-R2)."""

from __future__ import annotations

import fakeredis
import pytest

from recon.events import stream


@pytest.fixture()
def redis():
    return fakeredis.FakeStrictRedis()


def test_publish_then_replay_from_start(redis):
    stream.publish(redis, "r1", pg_id=1, event_type="run.created", payload={"a": 1}, maxlen=1000)
    stream.publish(redis, "r1", pg_id=2, event_type="run.transition", payload={"to": "fetching"}, maxlen=1000)
    events = stream.replay(redis, "r1", last_id=None)
    assert [e["type"] for e in events] == ["run.created", "run.transition"]
    assert events[0]["payload"] == {"a": 1}
    assert events[0]["pg_id"] == 1


def test_replay_after_last_id_returns_only_newer(redis):
    first = stream.publish(redis, "r1", pg_id=1, event_type="a", payload={}, maxlen=1000)
    stream.publish(redis, "r1", pg_id=2, event_type="b", payload={}, maxlen=1000)
    events = stream.replay(redis, "r1", last_id=first)
    assert [e["type"] for e in events] == ["b"]


def test_replay_reconnect_loses_nothing(redis):
    ids = [
        stream.publish(redis, "r1", pg_id=i, event_type=f"e{i}", payload={}, maxlen=1000)
        for i in range(5)
    ]
    # Client saw up to the 3rd event, then reconnected.
    seen_through = ids[2]
    missed = stream.replay(redis, "r1", last_id=seen_through)
    assert [e["type"] for e in missed] == ["e3", "e4"]


def test_streams_are_isolated_per_run(redis):
    stream.publish(redis, "r1", pg_id=1, event_type="x", payload={}, maxlen=1000)
    assert stream.replay(redis, "r2", last_id=None) == []
