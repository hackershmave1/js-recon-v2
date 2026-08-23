"""Slice Y multi-asset fetch loop — DB-backed, the shared hop-core stubbed.

Stubs ``fetch._fetch_hops`` (not ``fetch.fetch_url``): Task 6 moved
``_fetch_assets``'s own asset fetch onto the header-carrying hop-core so it can
harvest the fingerprint signal, and ``fetch_url`` is now a thin wrapper THAT
CALLS ``_fetch_hops`` internally — so stubbing ``_fetch_hops`` transparently
covers both the JS asset's direct call and the source-map helper's indirect one
(via ``fetch_url``), exactly as one un-mocked ``httpx`` boundary always did.
"""

from __future__ import annotations

import json

import pytest

from recon import storage
from recon.config import clamp_fetch_bytes, get_settings
from recon.db import models
from recon.db.base import tenant_session
from recon.fetch import fetch
from recon.queue import retry
from recon.runs import assets, service

pytestmark = pytest.mark.integration

# Per-asset heartbeats write to a real `job` row keyed by this id (UUID column) —
# it must be well-formed even though no Job row exists for it in these tests
# (heartbeat.beat's UPDATE then just matches zero rows, which is not an error).
_JOB_ID = "11111111-1111-1111-1111-111111111111"


def _crawl_run(redis, tenant, session_id, urls):
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=view.id, urls=urls)
    return view.id


def _hop(body: bytes, **kw) -> fetch._FetchedResponse:
    """A minimal successful hop result — the fetch-loop tests only care about
    ``body``; headers/cookies are exercised by ``fetch_signal_test.py``."""
    return fetch._FetchedResponse(
        body=body, status=200, headers=kw.get("headers", {}), set_cookie=kw.get("set_cookie", [])
    )


def test_fetch_loop_records_ok_and_failed_per_asset(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    urls = ["https://acme.io/a.js", "https://acme.io/bad.js"]
    run_id = _crawl_run(redis, tenant, session_id, urls)

    def fake_fetch(url, scope, **kw):
        if url.endswith("bad.js"):
            raise retry.FatalError("HTTP 404")
        return _hop(b'fetch("/api/x");')

    monkeypatch.setattr(fetch, "_fetch_hops", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    rows = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    assert rows["https://acme.io/a.js"].fetch_status == "ok"
    assert rows["https://acme.io/a.js"].input_ref is not None
    assert rows["https://acme.io/bad.js"].fetch_status == "failed"


# --- DEBT D20: bounded per-asset retry of a transient 429/5xx --------------------
# These stub _await_host_slot + _beat_sleep to no-ops so the retry loop neither waits
# on the per-host politeness gate nor sleeps the real backoff (the retry DECISION is
# what's under test, not pacing).


def test_fetch_asset_retries_transient_5xx_then_succeeds(redis, authorized_session, monkeypatch):
    # A transient 5xx no longer drops the asset: it retries and, on the next success,
    # the asset is `ok` (so the run is not needlessly PARTIAL).
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js"])
    calls = {"n": 0}

    def flaky(url, scope, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise fetch._TransientStatus("target returned HTTP 503")
        return _hop(b'fetch("/api/x");')

    monkeypatch.setattr(fetch, "_fetch_hops", flaky)
    monkeypatch.setattr(fetch, "_await_host_slot", lambda *a, **k: None)
    monkeypatch.setattr(fetch, "_beat_sleep", lambda *a, **k: None)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    assert calls["n"] == 2  # one retry, then success
    row = {r.url: r for r in assets.list_for_run(tenant, run_id)}["https://acme.io/a.js"]
    assert row.fetch_status == "ok"
    assert row.input_ref is not None


def test_fetch_asset_retry_exhausted_marks_failed(redis, authorized_session, monkeypatch):
    # A persistently-5xx asset retries the bounded number of times, then fails exactly
    # as pre-D20 — the change only ADDS recovery attempts; the worst case is unchanged.
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js"])
    calls = {"n": 0}

    def always_503(url, scope, **kw):
        calls["n"] += 1
        raise fetch._TransientStatus("target returned HTTP 503")

    monkeypatch.setattr(fetch, "_fetch_hops", always_503)
    monkeypatch.setattr(fetch, "_await_host_slot", lambda *a, **k: None)
    monkeypatch.setattr(fetch, "_beat_sleep", lambda *a, **k: None)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    assert calls["n"] == 3  # default attempts=2 → 1 initial + 2 retries
    row = {r.url: r for r in assets.list_for_run(tenant, run_id)}["https://acme.io/a.js"]
    assert row.fetch_status == "failed"


def test_fetch_asset_deadline_retryable_is_not_retried(redis, authorized_session, monkeypatch):
    # Discrimination: a bare RetryableError (the "overall fetch deadline exceeded"
    # case) is NOT a _TransientStatus, so it is NOT retried — the time budget is spent.
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js"])
    calls = {"n": 0}

    def deadline(url, scope, **kw):
        calls["n"] += 1
        raise retry.RetryableError("overall fetch deadline exceeded")

    monkeypatch.setattr(fetch, "_fetch_hops", deadline)
    monkeypatch.setattr(fetch, "_await_host_slot", lambda *a, **k: None)
    monkeypatch.setattr(fetch, "_beat_sleep", lambda *a, **k: None)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    assert calls["n"] == 1  # not retried
    row = {r.url: r for r in assets.list_for_run(tenant, run_id)}["https://acme.io/a.js"]
    assert row.fetch_status == "failed"


def test_fetch_asset_retry_disabled_when_attempts_zero(redis, authorized_session, monkeypatch):
    # attempts=0 is the kill-switch: exactly the pre-D20 behavior (one try, no retry).
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js"])
    patched = fetch.get_settings().model_copy(update={"fetch_asset_retry_attempts": 0})
    monkeypatch.setattr(fetch, "get_settings", lambda: patched)
    calls = {"n": 0}

    def always_503(url, scope, **kw):
        calls["n"] += 1
        raise fetch._TransientStatus("target returned HTTP 503")

    monkeypatch.setattr(fetch, "_fetch_hops", always_503)
    monkeypatch.setattr(fetch, "_await_host_slot", lambda *a, **k: None)
    monkeypatch.setattr(fetch, "_beat_sleep", lambda *a, **k: None)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    assert calls["n"] == 1  # no retry
    row = {r.url: r for r in assets.list_for_run(tenant, run_id)}["https://acme.io/a.js"]
    assert row.fetch_status == "failed"


def test_fetch_asset_retry_backoff_is_capped(redis, authorized_session, monkeypatch):
    # Lease safety: a hostile Retry-After must NOT blow the in-loop backoff — the delay
    # handed to _beat_sleep is clamped to fetch_asset_retry_max_delay_seconds.
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js"])
    slept: list[float] = []

    def flaky(url, scope, **kw):
        if not slept:  # first try raises (→ one backoff), second returns
            raise fetch._TransientStatus("target returned HTTP 503", retry_after=3600.0)
        return _hop(b'fetch("/api/x");')

    def capture_sleep(redis_, *, tenant_id, run_id, job_id, seconds):
        slept.append(seconds)

    monkeypatch.setattr(fetch, "_fetch_hops", flaky)
    monkeypatch.setattr(fetch, "_await_host_slot", lambda *a, **k: None)
    monkeypatch.setattr(fetch, "_beat_sleep", capture_sleep)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    cap = fetch.get_settings().fetch_asset_retry_max_delay_seconds
    assert len(slept) == 1
    assert slept[0] == pytest.approx(cap)  # retry_after=3600 clamped down to the cap
    row = {r.url: r for r in assets.list_for_run(tenant, run_id)}["https://acme.io/a.js"]
    assert row.fetch_status == "ok"


def test_fetch_asset_beats_before_every_attempt(redis, authorized_session, monkeypatch):
    # The lease-safety fix: progress.beat runs before EVERY attempt (so a retry sequence
    # can't outrun the job lease), and only attempt 1 emits a progress event.
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js"])
    fetches: list[str] = []
    beats: list[bool] = []

    def always_503(url, scope, **kw):
        fetches.append(url)
        raise fetch._TransientStatus("target returned HTTP 503")

    def capture_beat(redis_, *, tenant_id, run_id, job_id, done, total, emit_event=True):
        beats.append(emit_event)

    monkeypatch.setattr(fetch, "_fetch_hops", always_503)
    monkeypatch.setattr(fetch, "_await_host_slot", lambda *a, **k: None)
    monkeypatch.setattr(fetch, "_beat_sleep", lambda *a, **k: None)
    monkeypatch.setattr(fetch.progress, "beat", capture_beat)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    assert len(fetches) == 3  # default attempts=2 → 3 tries
    assert beats == [True, False, False]  # a beat before each; only attempt 1 emits


def test_fetch_loop_is_idempotent_on_redelivery(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js"])
    monkeypatch.setattr(fetch, "_fetch_hops", lambda *a, **k: _hop(b"one();"))
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    def _must_not_fetch(*a, **k):
        raise AssertionError("re-fetched a terminal asset")

    monkeypatch.setattr(fetch, "_fetch_hops", _must_not_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)  # no-op


def test_fetch_loop_honors_cancel(redis, authorized_session, monkeypatch):
    # Genuinely mid-loop: cancel arrives WHILE asset 1 is "in flight" (the fake
    # hop-core itself requests it, as another actor would), not before the loop
    # starts — so this guards against a future refactor hoisting the control
    # check out of the loop body.
    from recon.runs import service as run_service

    tenant, session_id = authorized_session
    urls = ["https://acme.io/a.js", "https://acme.io/b.js"]
    run_id = _crawl_run(redis, tenant, session_id, urls)

    calls = []

    def fake_fetch(url, scope, **kw):
        calls.append(url)
        run_service.request_cancel(redis, tenant_id=tenant, run_id=run_id)
        return _hop(b"x();")

    monkeypatch.setattr(fetch, "_fetch_hops", fake_fetch)
    with pytest.raises(retry.ControlInterrupt) as ci:
        fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)
    assert ci.value.kind == "cancel"
    assert calls == ["https://acme.io/a.js"]  # asset 2 must never be fetched

    rows = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    assert rows["https://acme.io/a.js"].fetch_status == "ok"
    assert rows["https://acme.io/b.js"].fetch_status == "pending"


def test_fetch_loop_links_external_source_map(redis, authorized_session, monkeypatch):
    # REQ-CE2: a JS asset that references an external //# sourceMappingURL= gets its
    # .map fetched (through the egress guard), stored, and linked to the asset row.
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/app.js"])

    def fake_fetch(url, scope, **kw):
        if url.endswith(".map"):
            return _hop(b'{"version":3,"sources":["src/app.js"],"mappings":"AAAA"}')
        return _hop(b'fetch("/api/x");\n//# sourceMappingURL=app.js.map\n')

    monkeypatch.setattr(fetch, "_fetch_hops", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    row = {r.url: r for r in assets.list_for_run(tenant, run_id)}["https://acme.io/app.js"]
    assert row.fetch_status == "ok"
    assert row.input_ref is not None
    assert row.source_map_ref is not None  # the .map was fetched, stored, linked
    assert row.source_map_skipped is False  # D32: a recovered map is never flagged skipped


def test_fetch_loop_bad_source_map_is_soft_miss(redis, authorized_session, monkeypatch):
    # REQ-CE2 load-bearing invariant: a blocked/failing .map must NEVER fail the
    # asset (that would drop its JS finding). The asset stays fetch_ok with no
    # source_map_ref, and analyze later falls back to the minified bundle.
    # D32: the soft miss is no longer SILENT — the asset is flagged source_map_skipped
    # and a durable fetch.source_map_skipped event records the reason (REQ-D5 honesty).
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/app.js"])

    def fake_fetch(url, scope, **kw):
        if url.endswith(".map"):
            raise retry.FatalError("HTTP 404")
        return _hop(b'fetch("/api/x");\n//# sourceMappingURL=app.js.map\n')

    monkeypatch.setattr(fetch, "_fetch_hops", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    row = {r.url: r for r in assets.list_for_run(tenant, run_id)}["https://acme.io/app.js"]
    assert row.fetch_status == "ok"  # NOT failed — the JS fetch succeeded
    assert row.input_ref is not None
    assert row.source_map_ref is None  # the bad .map was a soft miss, not linked
    assert row.source_map_skipped is True  # D32: honest, not silent
    events = _source_map_skipped_events(tenant, run_id)
    assert len(events) == 1
    assert events[0].payload["url"] == "https://acme.io/app.js"
    assert events[0].payload["map_url"] == "https://acme.io/app.js.map"
    assert "404" in events[0].payload["reason"]


def test_fetch_loop_oversize_source_map_is_skipped(redis, authorized_session, monkeypatch):
    # D32's headline case: a real .map is 3-6x the bundle and trips the streamed byte cap.
    # Today that was a silent "none"; now it is an honest "skipped" with the byte-cap reason
    # preserved in the event payload (so oversize is distinguishable from a 404 downstream).
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/app.js"])

    def fake_fetch(url, scope, **kw):
        if url.endswith(".map"):
            raise retry.FatalError("response exceeds 10485760 bytes")
        return _hop(b'fetch("/api/x");\n//# sourceMappingURL=app.js.map\n')

    monkeypatch.setattr(fetch, "_fetch_hops", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    row = {r.url: r for r in assets.list_for_run(tenant, run_id)}["https://acme.io/app.js"]
    assert row.fetch_status == "ok"  # the oversized map never fails the JS asset
    assert row.source_map_ref is None
    assert row.source_map_skipped is True
    events = _source_map_skipped_events(tenant, run_id)
    assert len(events) == 1
    assert "exceeds" in events[0].payload["reason"]


def test_fetch_loop_source_map_uses_its_own_larger_cap(redis, authorized_session, monkeypatch):
    # D32-A1: the .map GET must use max_source_map_bytes (its OWN cap), NOT the shared
    # bundle cap — a real source map is 3-6x its bundle, so sharing the cap soft-drops it.
    # Capture the byte cap each GET receives at the _fetch_hops boundary and prove they
    # come from DIFFERENT sources (map cap vs bundle cap), the map being larger by default.
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/app.js"])
    caps: dict[str, int] = {}

    def fake_fetch(url, scope, **kw):
        caps["map" if url.endswith(".map") else "bundle"] = kw["max_bytes"]
        if url.endswith(".map"):
            return _hop(b'{"version":3,"sources":["src/app.js"],"mappings":"AAAA"}')
        return _hop(b'fetch("/api/x");\n//# sourceMappingURL=app.js.map\n')

    monkeypatch.setattr(fetch, "_fetch_hops", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    s = get_settings()
    assert caps["map"] == s.max_source_map_bytes  # the .map uses its OWN cap...
    assert caps["bundle"] == clamp_fetch_bytes(None, s)  # ...the bundle uses the shared cap
    assert caps["map"] > caps["bundle"]  # and by default the map cap is the larger one


def test_fetch_loop_source_map_generic_error_is_soft_miss(redis, authorized_session, monkeypatch):
    # Pins the BREADTH of the helper's `except Exception` (fetch.py _fetch_and_store_
    # source_map): a NON-fetch error while handling the .map — a malformed ref, a
    # storage hiccup, here a plain ValueError — must ALSO be swallowed as a soft miss.
    # If a later edit narrowed the catch to the fetch-error trio, this ValueError would
    # propagate out of the run and drop the asset's JS finding (the spec §5.2 regression).
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/app.js"])

    def fake_fetch(url, scope, **kw):
        if url.endswith(".map"):
            raise ValueError("boom: not a fetch-classified error")
        return _hop(b'fetch("/api/x");\n//# sourceMappingURL=app.js.map\n')

    monkeypatch.setattr(fetch, "_fetch_hops", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)  # must not raise

    row = {r.url: r for r in assets.list_for_run(tenant, run_id)}["https://acme.io/app.js"]
    assert row.fetch_status == "ok"
    assert row.source_map_ref is None
    assert row.source_map_skipped is True  # D32: any soft miss is flagged, not just fetch errors


def _fingerprint_events(tenant, run_id):
    with tenant_session(tenant) as session:
        return (
            session.query(models.RunEvent).filter_by(run_id=run_id, type="fingerprint.signal").all()
        )


def _source_map_skipped_events(tenant, run_id):
    with tenant_session(tenant) as session:
        return (
            session.query(models.RunEvent)
            .filter_by(run_id=run_id, type="fetch.source_map_skipped")
            .all()
        )


def test_fetch_loop_writes_one_consolidated_fingerprint_signal_blob(
    redis, authorized_session, monkeypatch
):
    # T6: the blob is written ONCE per run, folding every asset's contribution —
    # not once per asset (which would leave "latest" pointing at only the last
    # asset's host, dropping every earlier one). Two DIFFERENT hosts here proves
    # the write actually consolidates rather than just happening to run once for
    # a single-host fixture.
    tenant, session_id = authorized_session
    urls = ["https://acme.io/a.js", "https://api.acme.io/b.js"]
    run_id = _crawl_run(redis, tenant, session_id, urls)

    def fake_fetch(url, scope, **kw):
        if url == "https://acme.io/a.js":
            return _hop(
                b"console.log(1)",
                headers={
                    "server": "nginx",
                    "authorization": "Bearer super-secret",  # never allowlisted (T1)
                },
                set_cookie=["sid=SECRETVALUE; Path=/; HttpOnly"],
            )
        return _hop(b"console.log(2)", headers={"x-powered-by": "Express"})

    monkeypatch.setattr(fetch, "_fetch_hops", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    rows = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    assert rows["https://acme.io/a.js"].fetch_status == "ok"
    assert rows["https://api.acme.io/b.js"].fetch_status == "ok"

    events = _fingerprint_events(tenant, run_id)
    assert len(events) == 1  # ONE event for the whole run, not one per asset
    assert events[0].payload["hosts"] == 2

    signal = json.loads(storage.get_blob(events[0].payload["signal_ref"]))
    assert set(signal) == {"acme.io", "api.acme.io"}  # BOTH hosts survived the write

    acme = signal["acme.io"]
    assert acme["headers"] == {"server": "nginx"}  # authorization dropped (T1)
    assert acme["cookies"] == ["sid"]  # name only, value never persisted (T1)
    assert acme["scripts"] == ["https://acme.io/a.js"]
    assert acme["meta"] == []

    api = signal["api.acme.io"]
    assert api["headers"] == {"x-powered-by": "Express"}
    assert api["cookies"] == []
    assert api["scripts"] == ["https://api.acme.io/b.js"]
