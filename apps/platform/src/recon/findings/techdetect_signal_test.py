"""Unit tests for loading a run's merged fingerprint signal (DEBT D77).

Pure: the event read, blob store and compaction write are monkeypatched, so merge, growth
budget and compaction are asserted without PG/S3. End-to-end is covered by the integration
``api/capture_technologies_test.py``."""

from __future__ import annotations

import json

import pytest

from recon.findings import techdetect_signal as ts


def test_merge_unions_hosts_and_lists_and_later_headers_win() -> None:
    first = {
        "acme.io": {
            "headers": {"server": "nginx/1.24", "x-powered-by": "Express"},
            "scripts": ["https://acme.io/a.js"],
            "meta": [],
            "cookies": ["sid"],
        }
    }
    second = {
        "acme.io": {
            "headers": {"server": "nginx/1.25.3"},
            "scripts": ["https://acme.io/a.js", "https://acme.io/b.js"],
            "meta": ["WordPress 6.4"],
            "cookies": ["sid", "XSRF-TOKEN"],
        },
        "cdn.acme.io": {"headers": {}, "scripts": ["https://cdn.acme.io/x.js"]},
    }

    merged = ts.merge_signals([first, second])

    assert merged["acme.io"] == {
        "headers": {"server": "nginx/1.25.3", "x-powered-by": "Express"},
        "scripts": ["https://acme.io/a.js", "https://acme.io/b.js"],
        "meta": ["WordPress 6.4"],
        "cookies": ["sid", "XSRF-TOKEN"],
    }
    assert merged["cdn.acme.io"] == {
        "headers": {},
        "scripts": ["https://cdn.acme.io/x.js"],
        "meta": [],
        "cookies": [],
    }


def test_merge_ignores_malformed_entries() -> None:
    merged = ts.merge_signals(
        [{"acme.io": "not a dict", "ok.io": {"headers": ["x"], "scripts": "y"}}, ["not a dict"]]
    )

    assert merged == {"ok.io": {"headers": {}, "scripts": [], "meta": [], "cookies": []}}


def test_repeated_headers_cost_no_budget() -> None:
    # Every extension batch repeats its host's header set; only what CHANGES counts, so a long
    # capture does not spend the budget on duplicates.
    merger = ts.SignalMerger()
    batch = {"acme.io": {"headers": {"content-security-policy": "x" * 1000}}}
    merger.add(batch)
    spent = merger.added_bytes
    for _ in range(100):
        merger.add(batch)

    assert merger.added_bytes == spent


class _Store:
    """In-memory events + blobs standing in for PG/S3."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.blobs: dict[str, bytes] = {}
        self.signal_refs: list[str] = []
        self.merged_ref: str | None = None
        self.reads: list[str] = []
        monkeypatch.setattr(ts, "_signal_events", lambda t, r: (self.signal_refs, self.merged_ref))
        monkeypatch.setattr(ts.storage, "get_blob", self._get)
        monkeypatch.setattr(ts, "_store_merged", self._store_merged)

    def add_signal(self, ref: str, signal: dict) -> None:
        self.blobs[ref] = json.dumps(signal).encode()
        self.signal_refs.append(ref)

    def _get(self, ref: str) -> bytes:
        self.reads.append(ref)
        return self.blobs[ref]

    def _store_merged(self, tenant_id: str, run_id: str, signal: dict, refs: list[str]) -> None:
        ref = f"merged-{len(self.blobs)}"
        self.blobs[ref] = json.dumps({"signal": signal, "refs": refs}).encode()
        self.merged_ref = ref


def _host(n: int) -> dict:
    return {f"h{n}.io": {"headers": {"server": f"nginx/{n}"}, "scripts": [f"https://h{n}.io/a.js"]}}


def test_load_merges_every_batch_and_reads_a_retried_ref_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _Store(monkeypatch)
    store.add_signal("ref-1", _host(1))
    store.add_signal("ref-2", _host(2))
    store.signal_refs.append("ref-1")  # a retried batch re-records the same content ref

    signal = ts.load_fingerprint_signal("t", "r")

    assert set(signal) == {"h1.io", "h2.io"}
    assert store.reads.count("ref-1") == 1


def test_a_later_pass_starts_from_the_merge_and_reads_only_new_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _Store(monkeypatch)
    store.add_signal("ref-1", _host(1))
    store.add_signal("ref-2", _host(2))
    ts.load_fingerprint_signal("t", "r")
    assert store.merged_ref is not None
    store.add_signal("ref-3", _host(3))
    store.reads.clear()

    signal = ts.load_fingerprint_signal("t", "r")

    assert set(signal) == {"h1.io", "h2.io", "h3.io"}
    assert "ref-1" not in store.reads and "ref-2" not in store.reads
    assert "ref-3" in store.reads


def test_a_late_committing_older_event_is_still_merged(monkeypatch: pytest.MonkeyPatch) -> None:
    # Coverage is an explicit ref set, not an event-id watermark: a batch whose event commits
    # after the merge was written is still read by the next pass, wherever it sorts.
    store = _Store(monkeypatch)
    store.add_signal("ref-2", _host(2))
    store.add_signal("ref-3", _host(3))
    ts.load_fingerprint_signal("t", "r")
    store.blobs["ref-1"] = json.dumps(_host(1)).encode()
    store.signal_refs.insert(0, "ref-1")

    assert "h1.io" in ts.load_fingerprint_signal("t", "r")


def test_a_spent_budget_stops_reading_and_keeps_what_merged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _Store(monkeypatch)
    for n in range(1, 6):
        store.add_signal(f"ref-{n}", _host(n))
    # Room for about two hosts' worth of growth.
    one_host = ts.SignalMerger()
    one_host.add(_host(1))
    monkeypatch.setattr(ts, "MAX_SIGNAL_BYTES", one_host.added_bytes * 2)

    signal = ts.load_fingerprint_signal("t", "r")

    assert set(signal) == {"h1.io", "h2.io"}
    assert store.reads == ["ref-1", "ref-2"]  # the rest are never downloaded


def test_no_signal_is_an_empty_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    _Store(monkeypatch)
    assert ts.load_fingerprint_signal("t", "r") == {}
