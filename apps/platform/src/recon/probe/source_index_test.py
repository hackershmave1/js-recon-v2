"""Background indexing of an older run's bundles: the worker stores each bundle's served text so
search gains line numbers, in short time slices, without ever looping or re-downloading copies."""

from __future__ import annotations

import uuid

import fakeredis
import pytest

from recon.domain import QueueName
from recon.findings import deobfuscate
from recon.probe import formatted_text, source_index, sources
from recon.queue import streams

T, R = str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture
def run(monkeypatch):
    """Fake run: `assets` path -> raw text; `stored` = digests that already have a copy."""
    state: dict = {"assets": {}, "stored": set(), "gone": False, "puts": []}

    def keys(_t, _r):
        if state["gone"]:
            return {}
        return {p: f"{T}/{R}/input/{p}" for p in state["assets"]}

    monkeypatch.setattr(sources, "asset_keys", keys)
    monkeypatch.setattr(
        source_index.storage, "list_keys", lambda prefix: {f"{prefix}{d}" for d in state["stored"]}
    )
    monkeypatch.setattr(
        source_index.storage,
        "get_blob",
        lambda key: state["assets"][key.rsplit("/", 1)[1]].encode(),
    )

    def store(key, content):
        state["puts"].append(key.rsplit("/", 1)[1])
        state["stored"].add(key.rsplit("/", 1)[1])

    monkeypatch.setattr(formatted_text, "store", store)
    return state


@pytest.fixture
def redis():
    return fakeredis.FakeRedis(decode_responses=True)


def _drain(redis, limit=50):
    """Run the worker's index handler until the queue is empty; return handled count."""
    streams.ensure_group(redis, QueueName.INDEX)
    handled = 0
    for _ in range(limit):
        batch = streams.read_batch(redis, QueueName.INDEX, "c", count=1, block_ms=0)
        if not batch:
            return handled
        for msg_id, message in batch:
            source_index.handle(redis, message)
            streams.ack(redis, QueueName.INDEX, msg_id)
            handled += 1
    raise AssertionError("index job never finished")


def test_status_counts_from_one_listing_never_downloads(run, monkeypatch):
    run["assets"].update({"a": "x", "b": "y", "c": "z"})
    run["stored"].add("a")
    monkeypatch.setattr(
        source_index.storage, "get_blob", lambda _k: pytest.fail("status must not download")
    )
    assert source_index.status(fakeredis.FakeRedis(), T, R) == {
        "indexed": 1,
        "total": 3,
        "indexing": False,
    }


def test_request_enqueues_once_and_the_job_indexes_every_missing_bundle(run, redis):
    run["assets"].update({f"f{i}": f"a();b({i})" for i in range(30)})
    run["stored"].add("f0")
    assert source_index.request_index(redis, T, R) is True
    assert source_index.request_index(redis, T, R) is False  # one job per run at a time
    assert source_index.status(redis, T, R)["indexing"] is True
    _drain(redis)
    assert sorted(run["puts"]) == sorted(f"f{i}" for i in range(1, 30))  # f0 was already stored
    assert source_index.status(redis, T, R) == {"indexed": 30, "total": 30, "indexing": False}
    assert source_index.request_index(redis, T, R) is False  # nothing left to do


def test_an_unindexable_bundle_never_loops_the_job(run, redis, monkeypatch):
    # Design review #1: a store that always fails used to keep "remaining > 0" forever.
    run["assets"].update({"good": "a()", "bad": "b()"})
    real = formatted_text.store

    def flaky(key, content):
        if not key.endswith("/bad"):
            real(key, content)

    monkeypatch.setattr(formatted_text, "store", flaky)
    source_index.request_index(redis, T, R)
    assert _drain(redis) <= 2
    assert source_index.status(redis, T, R) == {"indexed": 1, "total": 2, "indexing": False}


def test_slices_are_time_bounded_and_resume_from_a_cursor(run, redis, monkeypatch):
    run["assets"].update({f"f{i}": "a()" for i in range(10)})
    ticks = iter(range(0, 10**6, 3))
    monkeypatch.setattr(source_index.time, "monotonic", lambda: next(ticks))
    source_index.request_index(redis, T, R)
    handled = _drain(redis)
    assert handled > 1  # several short slices, not one long one
    assert len(run["puts"]) == 10 and len(set(run["puts"])) == 10  # cursor: no file twice


def test_a_deleted_run_stops_writing(run, redis, monkeypatch):
    run["assets"].update({"a": "a()", "b": "b()"})
    real = deobfuscate.served_text

    def delete_after_first(src):
        run["gone"] = True  # the session is deleted while the slice runs
        return real(src)

    monkeypatch.setattr(deobfuscate, "served_text", delete_after_first)
    source_index.request_index(redis, T, R)
    _drain(redis)
    assert run["puts"] == []  # re-checked before each write


def test_a_bad_message_is_acked_not_retried_forever(redis, run):
    # Design review #3: a message the handler can't use must never raise into the worker loop.
    assert source_index.handle(redis, {"tenant_id": "not-a-uuid", "run_id": R}) == "rejected"
    assert source_index.handle(redis, {}) == "rejected"


def test_an_unexpected_error_is_contained(redis, run, monkeypatch):
    run["assets"]["a"] = "a()"
    monkeypatch.setattr(
        source_index.storage, "get_blob", lambda _k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    source_index.request_index(redis, T, R)
    _drain(redis)  # no exception escapes
    assert source_index.status(redis, T, R)["indexing"] is False
