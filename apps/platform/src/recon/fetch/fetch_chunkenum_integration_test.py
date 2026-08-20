"""Integration proof (P4): a webpack lazy chunk is enumerated at fetch time, fetched
through the guarded pipeline, and its endpoints recovered by analyze.

Mirrors ``discover/multi_asset_integration_test.py`` Part A: it stubs ONLY the network
boundary (``fetch._fetch_hops``, which ``fetch_url`` wraps) and drives the REAL
``fetch_run`` -> ``analyze_run`` -> ``coordinator.advance`` over a seeded ``run_asset``,
proving the cross-stage chunk-enum flow against genuine per-asset DB state. Needs live
PG/Redis/MinIO, so it runs in the CI integration lane (marker ``integration``), not the
fast lane. The chunk-enum wiring's isolated logic is covered hermetically in
``fetch_chunkenum_test.py``; this is the end-to-end "the endpoint actually lands" proof.
"""

from __future__ import annotations

import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import RunStage, RunState
from recon.events.log import record_event
from recon.fetch import fetch
from recon.findings import analyze
from recon.findings import queries as findings_queries
from recon.runs import assets, coordinator, service

pytestmark = pytest.mark.integration

# Well-formed UUID for per-asset heartbeats; no Job row exists, so heartbeat.beat's
# UPDATE matches zero rows (not an error) — same convention as multi_asset_integration_test.
_JOB_ID = "22222222-2222-2222-2222-222222222222"

# A minimal webpack runtime bundle: the chunk-load global (which passes the b"webpack"
# gate), the .u chunk-URL builder, and one ensure-call -> chunk id 42. It carries NO
# fetch() of its own, so the ONLY endpoint the run can produce comes from the lazy chunk.
_MAIN_URL = "https://acme.io/static/main.js"
_CHUNK_URL = "https://acme.io/static/42.chunk.js"  # urljoin(_MAIN_URL, "42.chunk.js")
_MAIN_JS = b'self.webpackChunkapp=[];var n={};n.u=e=>e+".chunk.js";n.e(42);'
_CHUNK_JS = b'fetch("/api/orders");'


def _seed_bundle_run(redis, tenant, session_id):
    """A crawl run seeded with ONLY the runtime bundle (plus the discover.assets event
    the coordinator uses to recognize a crawl run)."""
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=view.id, urls=[_MAIN_URL])
        record_event(
            s,
            tenant_id=tenant,
            run_id=view.id,
            event_type="discover.assets",
            payload={"count": 1, "assets_ref": "x", "status": "ok"},
        )
    return view.id


def _walk_to_correlating(redis, tenant, run_id) -> None:
    for st in (
        RunState.DISCOVERING,
        RunState.FETCHING,
        RunState.INGESTING,
        RunState.ANALYZING,
        RunState.CORRELATING,
    ):
        service.transition(
            redis, tenant_id=tenant, run_id=run_id, to_state=st, stage=RunStage(st.value)
        )


def test_lazy_chunk_enumerated_fetched_and_endpoint_recovered(
    redis, authorized_session, monkeypatch
):
    """Seed ONLY the runtime bundle. Real ``fetch_run`` runs the chunk-enum hook, which
    enumerates ``42.chunk.js``, resolves it against the bundle URL, fetches it through the
    guarded pipeline, and seeds it as an OK asset. Real ``analyze_run`` then recovers the
    chunk's ``fetch("/api/orders")`` and attributes it to the chunk URL; the run finalizes
    DONE (both the bundle and the enumerated chunk analyze ok)."""
    tenant, session_id = authorized_session
    run_id = _seed_bundle_run(redis, tenant, session_id)

    blobs = {_MAIN_URL: _MAIN_JS, _CHUNK_URL: _CHUNK_JS}

    def fake_fetch(url, scope, **kw):
        return fetch._FetchedResponse(body=blobs[url], status=200, headers={}, set_cookie=[])

    monkeypatch.setattr(fetch, "_fetch_hops", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    rows = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    # The lazy chunk was discovered at fetch time, fetched, and analyzed — all in one run.
    assert _CHUNK_URL in rows, sorted(rows)
    assert rows[_MAIN_URL].fetch_status == "ok"
    assert rows[_CHUNK_URL].fetch_status == "ok"
    assert rows[_CHUNK_URL].analyze_status == "ok"

    _walk_to_correlating(redis, tenant, run_id)
    coordinator.advance(redis, tenant_id=tenant, run_id=run_id, completed=RunStage.CORRELATING)
    with tenant_session(tenant) as s:
        assert s.get(models.Run, run_id).state == "done"

    view = findings_queries.list_findings(tenant, run_id)
    endpoints = {f.value: f for f in view.findings if f.type == "endpoint"}
    assert "GET /api/orders" in endpoints, sorted(endpoints)
    assert _CHUNK_URL in {o.asset_url for o in endpoints["GET /api/orders"].occurrences}
