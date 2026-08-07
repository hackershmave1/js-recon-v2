"""Slice Y multi-asset fetch loop — DB-backed, fetch_url stubbed."""

from __future__ import annotations

import pytest

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


def test_fetch_loop_records_ok_and_failed_per_asset(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    urls = ["https://acme.io/a.js", "https://acme.io/bad.js"]
    run_id = _crawl_run(redis, tenant, session_id, urls)

    def fake_fetch(url, scope, **kw):
        if url.endswith("bad.js"):
            raise retry.FatalError("HTTP 404")
        return b'fetch("/api/x");'

    monkeypatch.setattr(fetch, "fetch_url", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    rows = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    assert rows["https://acme.io/a.js"].fetch_status == "ok"
    assert rows["https://acme.io/a.js"].input_ref is not None
    assert rows["https://acme.io/bad.js"].fetch_status == "failed"


def test_fetch_loop_is_idempotent_on_redelivery(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js"])
    monkeypatch.setattr(fetch, "fetch_url", lambda *a, **k: b"one();")
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    def _must_not_fetch(*a, **k):
        raise AssertionError("re-fetched a terminal asset")

    monkeypatch.setattr(fetch, "fetch_url", _must_not_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)  # no-op


def test_fetch_loop_honors_cancel(redis, authorized_session, monkeypatch):
    # Genuinely mid-loop: cancel arrives WHILE asset 1 is "in flight" (the fake
    # fetch_url itself requests it, as another actor would), not before the loop
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
        return b"x();"

    monkeypatch.setattr(fetch, "fetch_url", fake_fetch)
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
            return b'{"version":3,"sources":["src/app.js"],"mappings":"AAAA"}'
        return b'fetch("/api/x");\n//# sourceMappingURL=app.js.map\n'

    monkeypatch.setattr(fetch, "fetch_url", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    row = {r.url: r for r in assets.list_for_run(tenant, run_id)}["https://acme.io/app.js"]
    assert row.fetch_status == "ok"
    assert row.input_ref is not None
    assert row.source_map_ref is not None  # the .map was fetched, stored, linked


def test_fetch_loop_bad_source_map_is_soft_miss(redis, authorized_session, monkeypatch):
    # REQ-CE2 load-bearing invariant: a blocked/failing .map must NEVER fail the
    # asset (that would drop its JS finding). The asset stays fetch_ok with no
    # source_map_ref, and analyze later falls back to the minified bundle.
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/app.js"])

    def fake_fetch(url, scope, **kw):
        if url.endswith(".map"):
            raise retry.FatalError("HTTP 404")
        return b'fetch("/api/x");\n//# sourceMappingURL=app.js.map\n'

    monkeypatch.setattr(fetch, "fetch_url", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    row = {r.url: r for r in assets.list_for_run(tenant, run_id)}["https://acme.io/app.js"]
    assert row.fetch_status == "ok"  # NOT failed — the JS fetch succeeded
    assert row.input_ref is not None
    assert row.source_map_ref is None  # the bad .map was a soft miss, not linked


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
        return b'fetch("/api/x");\n//# sourceMappingURL=app.js.map\n'

    monkeypatch.setattr(fetch, "fetch_url", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)  # must not raise

    row = {r.url: r for r in assets.list_for_run(tenant, run_id)}["https://acme.io/app.js"]
    assert row.fetch_status == "ok"
    assert row.source_map_ref is None
