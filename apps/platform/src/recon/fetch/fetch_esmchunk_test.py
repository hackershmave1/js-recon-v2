"""Hermetic wiring tests for fetch-stage native-ESM chunk enumeration (recursive BFS).

Exercise ``fetch._enumerate_and_seed_esm_chunks`` in isolation (no PG/Redis/MinIO), the
network + storage + DB collaborators monkeypatched, so the security-critical behavior is gated
in the fast lane: content-derived import URLs are routed through the egress guard and DROPPED
when blocked, the transitive graph is followed (unlike the flat webpack map), a webpack bundle
is left to the webpack enumerator, cycles terminate, the run cap is re-applied, and a cancel
propagates while persisting what was already fetched.
"""

from __future__ import annotations

import contextlib
import itertools
from types import SimpleNamespace

import pytest

from recon.config import get_settings
from recon.fetch import egress, fetch
from recon.queue import retry

_MAIN_URL = "https://acme.test/static/main.js"
# A minimal Rolldown-style entry: static side-effect imports of the runtime + app chunks.
_ENTRY = b'import{o as e}from"./runtime.js";import"./app.js";'

# Per-URL response bodies, so recursion (app -> route) is exercised: app.js imports route.js.
_BODIES = {
    "https://acme.test/static/runtime.js": b"export const o=1;",
    "https://acme.test/static/app.js": b'import"./route.js";fetch("/api/app");',
    "https://acme.test/static/route.js": b'fetch("/api/route");',
}


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
        body = _BODIES.get(url, b'fetch("/api/x");')
        return fetch._FetchedResponse(body=body, status=200, headers={}, set_cookie=[])

    monkeypatch.setattr(fetch, "_fetch_hops", fake_hops)
    monkeypatch.setattr(
        fetch.storage, "put_blob", lambda t, r, kind, data: f"blob:{kind}:{len(data)}"
    )
    monkeypatch.setattr(
        fetch.run_assets,
        "seed_captured",
        lambda s, *, tenant_id, run_id, rows: rec.seeded.extend(rows),
    )
    # The parent (main.js) is already a fetched row — so it is in `known` and never refetched.
    monkeypatch.setattr(
        fetch.run_assets, "list_for_run", lambda t, r: [SimpleNamespace(url=_MAIN_URL)]
    )
    monkeypatch.setattr(fetch.run_queries, "raise_if_control_requested", lambda t, r: None)
    monkeypatch.setattr(fetch.progress, "beat", lambda *a, **k: None)
    monkeypatch.setattr(fetch, "_await_host_slot", lambda *a, **k: None)
    monkeypatch.setattr(fetch, "tenant_session", lambda t: contextlib.nullcontext(None))
    return rec


def _call(js: bytes, *, asset_url: str = _MAIN_URL) -> int:
    return fetch._enumerate_and_seed_esm_chunks(
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


def test_seeds_and_recurses_the_transitive_graph(wired: _Recorder) -> None:
    # Entry imports runtime.js + app.js; app.js imports route.js -> ALL three seeded via the BFS
    # (webpack's flat map would stop at the entry's direct chunks). The parent is not refetched.
    assert _call(_ENTRY) == 3
    assert [r["url"] for r in wired.seeded] == [
        "https://acme.test/static/runtime.js",
        "https://acme.test/static/app.js",
        "https://acme.test/static/route.js",
    ]
    assert all(r["input_ref"].startswith("blob:input:") for r in wired.seeded)
    assert _MAIN_URL not in wired.fetched


def test_out_of_scope_import_dropped_via_guard(wired: _Recorder) -> None:
    # An absolute cross-origin import -> fetch_url raises EgressBlocked -> dropped, but it WAS
    # routed through the guarded fetch (never seeded off a raw content URL); the in-scope
    # siblings still seed.
    js = b'import"https://evil.test/x.js";import"./app.js";'
    assert _call(js) == 2
    assert "https://evil.test/x.js" in wired.fetched
    assert [r["url"] for r in wired.seeded] == [
        "https://acme.test/static/app.js",
        "https://acme.test/static/route.js",
    ]


def test_webpack_asset_is_left_to_the_webpack_enumerator(wired: _Recorder) -> None:
    # A webpack runtime substring -> ESM enum yields to _enumerate_and_seed_chunks (one parse
    # per asset); no parse, no fetch here.
    assert _call(b'self.webpackChunkapp=[];import"./app.js";') == 0
    assert wired.fetched == []


def test_no_import_or_export_skips_without_fetch(wired: _Recorder) -> None:
    assert _call(b'const x=fetch("/api/x");') == 0
    assert wired.fetched == []


def test_cycle_is_broken(wired: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    # app.js imports back the parent main.js AND route.js: the parent is in `known`, so the
    # cycle terminates and main.js is never refetched.
    monkeypatch.setitem(
        _BODIES, "https://acme.test/static/app.js", b'import"./main.js";import"./route.js";'
    )
    assert _call(_ENTRY) == 3  # runtime + app + route (main.js skipped, already known)
    assert _MAIN_URL not in wired.fetched
    assert sorted(r["url"] for r in wired.seeded) == [
        "https://acme.test/static/app.js",
        "https://acme.test/static/route.js",
        "https://acme.test/static/runtime.js",
    ]


def test_run_cap_reapplied_stops_all_seeding(
    wired: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(
        fetch.run_assets,
        "list_for_run",
        lambda t, r: [
            SimpleNamespace(url=f"https://acme.test/x{i}.js")
            for i in range(settings.crawl_max_assets)
        ],
    )
    assert _call(_ENTRY) == 0
    assert wired.seeded == []
    assert wired.fetched == []


def test_control_interrupt_propagates_and_persists_fetched(
    wired: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    # REQ-A4: a cancel raised before the 2nd import fetch must PROPAGATE (not be swallowed by the
    # soft-miss guard), and the 1st chunk (already fetched) must still be persisted. Checks:
    # 0 = parent while-top, 1 = runtime.js spec (fetch), 2 = app.js spec -> raise.
    checks = itertools.count()

    def fake_control(tenant_id: str, run_id: str) -> None:
        if next(checks) >= 2:
            raise retry.ControlInterrupt("cancel")

    monkeypatch.setattr(fetch.run_queries, "raise_if_control_requested", fake_control)
    with pytest.raises(retry.ControlInterrupt):
        _call(_ENTRY)
    assert [r["url"] for r in wired.seeded] == ["https://acme.test/static/runtime.js"]


def test_malformed_specifier_skips_only_that_chunk(wired: _Recorder) -> None:
    # A crafted malformed authority (`//[/...`) makes urljoin->urlsplit raise ValueError; it must
    # skip THAT chunk (recall-evasion at worst), not abort the BFS and drop the valid siblings.
    js = b'import"//[/x.js";import"./app.js";'
    assert _call(js) == 2  # app + route still seeded despite the malformed sibling
    assert [r["url"] for r in wired.seeded] == [
        "https://acme.test/static/app.js",
        "https://acme.test/static/route.js",
    ]


def test_hostile_all_out_of_scope_graph_bounds_requests(wired: _Recorder) -> None:
    # MUST-FIX #3: an all-out-of-scope entry seeds NOTHING and makes a BOUNDED number of requests
    # (the crawl_max_assets ceiling), never one per specifier — no request flood.
    small = get_settings().model_copy(update={"crawl_max_assets": 3})
    js = b"".join(f'import"https://evil.test/x{i}.js";'.encode() for i in range(10))
    n = fetch._enumerate_and_seed_esm_chunks(
        None,
        js=js,
        asset_url=_MAIN_URL,
        scope_hosts=["acme.test"],
        tenant_id="t1",
        run_id="r1",
        job_id=None,
        done=1,
        total=1,
        settings=small,
        max_bytes=1_000_000,
    )
    assert n == 0
    assert wired.seeded == []
    assert len(wired.fetched) <= 3  # bounded, not all 10
