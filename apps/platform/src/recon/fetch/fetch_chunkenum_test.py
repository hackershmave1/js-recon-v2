"""Hermetic wiring tests for fetch-stage webpack chunk enumeration.

Exercise ``fetch._enumerate_and_seed_chunks`` in isolation (no PG/Redis/MinIO) with the
network + storage + DB collaborators monkeypatched, so the security-critical behavior is
gated in the fast lane: content-derived chunk URLs are routed through the egress guard
and DROPPED when blocked, the run cap is re-applied, non-webpack input is skipped without
a parse, and already-known chunks are not re-fetched.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from recon.config import get_settings
from recon.fetch import egress, fetch

# A minimal webpack runtime: the chunk-load global (the b"webpack" gate), the .u builder,
# and two ensure-calls -> chunk ids 1 and 2.
_BUNDLE = b'self.webpackChunkapp=[];var n={};n.u=e=>e+".chunk.js";n.e(1);n.e(2);'
_MAIN_URL = "https://acme.test/static/main.js"


class _Recorder:
    def __init__(self) -> None:
        self.seeded: list[dict[str, str]] = []
        self.fetched: list[str] = []


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()

    def fake_hops(url: str, scope_hosts: list[str], **kwargs: object) -> fetch._FetchedResponse:
        rec.fetched.append(url)
        if "evil" in url:  # stand in for an out-of-scope host the guard would reject
            raise egress.EgressBlocked(f"host not in engagement scope: {url}")
        return fetch._FetchedResponse(
            body=b'fetch("/api/v3/orders");', status=200, headers={}, set_cookie=[]
        )

    monkeypatch.setattr(fetch, "_fetch_hops", fake_hops)
    monkeypatch.setattr(
        fetch.storage, "put_blob", lambda t, r, kind, data: f"blob:{kind}:{len(data)}"
    )
    monkeypatch.setattr(
        fetch.run_assets,
        "seed_captured",
        lambda s, *, tenant_id, run_id, rows: rec.seeded.extend(rows),
    )
    monkeypatch.setattr(fetch.run_assets, "list_for_run", lambda t, r: [])
    monkeypatch.setattr(fetch.progress, "beat", lambda *a, **k: None)
    monkeypatch.setattr(fetch, "_await_host_slot", lambda *a, **k: None)
    monkeypatch.setattr(fetch, "tenant_session", lambda t: contextlib.nullcontext(None))
    return rec


def _call(js: bytes, *, asset_url: str = _MAIN_URL) -> int:
    return fetch._enumerate_and_seed_chunks(
        None,  # redis unused (progress/host-slot monkeypatched)
        js=js,
        asset_url=asset_url,
        scope_hosts=["acme.test"],
        tenant_id="t1",
        run_id="r1",
        job_id=None,
        done=1,
        total=1,
        settings=get_settings(),
        max_bytes=1_000_000,
    )


def test_seeds_resolved_chunk_urls(wired: _Recorder) -> None:
    assert _call(_BUNDLE) == 2
    assert [r["url"] for r in wired.seeded] == [
        "https://acme.test/static/1.chunk.js",
        "https://acme.test/static/2.chunk.js",
    ]
    assert all(r["input_ref"].startswith("blob:input:") for r in wired.seeded)


def test_out_of_scope_chunk_is_dropped_via_guard(wired: _Recorder) -> None:
    # An absolute out-of-scope builder -> fetch_url raises EgressBlocked -> dropped, but it
    # WAS routed through the guarded fetch (never seeded off a raw content URL).
    js = b'self.webpackChunkapp=[];var n={};n.u=e=>"https://evil.test/"+e+".js";n.e(9);'
    assert _call(js) == 0
    assert wired.seeded == []
    assert "https://evil.test/9.js" in wired.fetched


def test_non_webpack_js_skips_without_parse_or_fetch(wired: _Recorder) -> None:
    assert _call(b'const x = fetch("/api/v3/orders");') == 0
    assert wired.fetched == []


def test_already_known_chunk_is_not_refetched(
    wired: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        fetch.run_assets,
        "list_for_run",
        lambda t, r: [SimpleNamespace(url="https://acme.test/static/1.chunk.js")],
    )
    assert _call(_BUNDLE) == 1
    assert [r["url"] for r in wired.seeded] == ["https://acme.test/static/2.chunk.js"]
    assert "https://acme.test/static/1.chunk.js" not in wired.fetched


def test_run_cap_reapplied_at_seed_site(wired: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(
        fetch.run_assets,
        "list_for_run",
        lambda t, r: [
            SimpleNamespace(url=f"https://acme.test/x{i}.js")
            for i in range(settings.crawl_max_assets)
        ],
    )
    assert _call(_BUNDLE) == 0
    assert wired.seeded == []
    assert wired.fetched == []
