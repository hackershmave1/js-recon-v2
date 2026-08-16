"""Slice Y integration proof — the cross-stage pipeline no single prior task
exercises together: real ``fetch_run`` -> real ``analyze_run`` -> real
``coordinator.advance`` finalize, chained end-to-end over a crawl run's
``run_asset`` rows.

Why this file does NOT do what its originating task brief
(``.superpowers/sdd/task-12-brief.md``) asked for — "crawl the fixture-site
end-to-end" — is infeasible as an automated test on this host, for two
independent reasons:

1. The fixture site (docker-compose's ``fixture-site`` service, see
   ``docker-compose.yml`` lines 67-71) has no published port and sits on a
   private Docker IP. ``egress.validate_target``/``egress.is_public_ip``
   correctly reject RFC1918 addresses (that guard is exactly what
   ``crawl_integration_test.py``'s ``test_egress_drops_internal_and_out_of_scope_urls``
   verifies), so a real ``discover_run`` against it yields an EMPTY manifest —
   proving the egress guard, not a multi-asset pipeline.
2. This host has no ``katana`` binary on PATH, and the containers currently up
   (see ``docker compose ps``) run a stale pre-Slice-Y image with no source
   mount, so the real worker code can't be driven through them either.

So Part A stubs ONLY the network boundary (``fetch._fetch_hops`` — the shared
hop-core ``fetch_url`` itself now wraps, Task 6) and drives the REAL
``fetch_run`` -> ``analyze_run`` -> ``coordinator.advance`` code over
seeded ``run_asset`` rows — proving cross-asset dedup+attribution and the
DONE/PARTIAL split against genuine per-asset DB state (not hand-set flags, as
``coordinator_completeness_test.py`` sets them, nor a single stage in
isolation, as ``fetch_multi_test.py``/``analyze_multi_test.py`` each do). Part B
is the discovery-layer half (real katana against the fixture site), modeled on
``crawl_integration_test.py``'s skip-guard: it SKIPS on this host (no katana)
and only goes green in CI/the container, where ``RECON_REQUIRE_ENGINES=1``
turns a missing binary into a failure instead of a silent skip (REQ-T4).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from recon.config import get_settings
from recon.db import models
from recon.db.base import tenant_session
from recon.discover import harness, katana
from recon.domain import RunStage, RunState
from recon.events.log import record_event
from recon.fetch import fetch
from recon.findings import analyze
from recon.findings import queries as findings_queries
from recon.queue import retry
from recon.runs import assets, coordinator, service

pytestmark = pytest.mark.integration

# Per-asset heartbeats write to a real `job` row keyed by this id (UUID column) --
# it must be well-formed even though no Job row exists for it in these tests
# (heartbeat.beat's UPDATE then just matches zero rows, which is not an error).
_JOB_ID = "11111111-1111-1111-1111-111111111111"

_URL_A = "https://acme.io/a.js"
_URL_B = "https://acme.io/b.js"


def _seed_crawl_run(redis, tenant, session_id, urls):
    """Create a run and make it look, to the coordinator, like one that just
    finished a real crawl: pending ``run_asset`` rows plus the ``discover.assets``
    event that ``coordinator._finalize_state`` uses to tell a crawl run apart
    from a legacy single-upload run (the discriminator is that event, NOT the
    row count -- see its docstring)."""
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=view.id, urls=urls)
        record_event(
            s,
            tenant_id=tenant,
            run_id=view.id,
            event_type="discover.assets",
            payload={"count": len(urls), "assets_ref": "x", "status": "ok"},
        )
    return view.id


def _walk_to_correlating(redis, tenant, run_id) -> None:
    """Advance the run's persisted ``state`` column through every active stage so
    it legally lands on CORRELATING -- the only state ``coordinator.advance`` may
    finalize from (``state_machine.ALLOWED`` only permits DONE/PARTIAL out of
    CORRELATING). Mirrors ``coordinator_completeness_test.py``'s
    ``_crawl_run_at_correlating``; kept as a separate step here since the real
    fetch/analyze work above does not itself depend on ``run.state``."""
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


def _run_state(tenant, run_id) -> tuple[str, dict]:
    """Read the run's terminal state + completeness flags straight off the row
    (no read-model surfaces ``completeness`` today -- ``queries.get_run_flags``
    stops at ``state``/``stage``/pause/cancel)."""
    with tenant_session(tenant) as s:
        run = s.get(models.Run, run_id)
        return run.state, dict(run.completeness or {})


# ---------------------------------------------------------------------------
# Part A -- primary proof: real fetch + real analyze + real finalize, network
# stubbed. Must be GREEN on every host (no external engines required).
# ---------------------------------------------------------------------------


def test_pipeline_done_with_cross_asset_dedup_and_attribution(
    redis, authorized_session, monkeypatch
):
    """Two assets, one shared endpoint. Real ``fetch_run`` (network stubbed)
    writes two DISTINCT blobs; real ``analyze_run`` reads each asset's own blob
    and tags every occurrence with that asset's ``run_asset_id``/``asset_url`` --
    so the SAME endpoint (``/api/shared``) sighted on both assets dedupes to ONE
    finding with TWO occurrences, each attributed to a different asset, while an
    endpoint unique to one asset (``/api/a-only``) stays attributed to only that
    one. Real ``coordinator.advance`` then reads the real per-asset statuses back
    off the DB (not a hand-set flag, unlike ``coordinator_completeness_test.py``)
    to finalize DONE."""
    tenant, session_id = authorized_session
    run_id = _seed_crawl_run(redis, tenant, session_id, [_URL_A, _URL_B])

    blobs = {
        _URL_A: b'fetch("/api/shared"); fetch("/api/a-only");',
        _URL_B: b'fetch("/api/shared");',
    }

    def fake_fetch(url, scope, **kw):
        return fetch._FetchedResponse(body=blobs[url], status=200, headers={}, set_cookie=[])

    monkeypatch.setattr(fetch, "_fetch_hops", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    rows = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    assert rows[_URL_A].fetch_status == "ok"
    assert rows[_URL_B].fetch_status == "ok"
    assert rows[_URL_A].analyze_status == "ok"
    assert rows[_URL_B].analyze_status == "ok"

    _walk_to_correlating(redis, tenant, run_id)
    coordinator.advance(redis, tenant_id=tenant, run_id=run_id, completed=RunStage.CORRELATING)

    state, completeness = _run_state(tenant, run_id)
    assert state == "done"
    assert completeness == {"fetch_ok": True, "analyze_ok": True}

    view = findings_queries.list_findings(tenant, run_id)
    endpoints = {f.value: f for f in view.findings if f.type == "endpoint"}
    assert set(endpoints) == {"GET /api/shared", "GET /api/a-only"}  # deduped, not doubled

    shared = endpoints["GET /api/shared"]
    assert len(shared.occurrences) == 2
    assert {o.asset_url for o in shared.occurrences} == {_URL_A, _URL_B}

    a_only = endpoints["GET /api/a-only"]
    assert {o.asset_url for o in a_only.occurrences} == {_URL_A}


def test_pipeline_partial_when_one_asset_fetch_fails(redis, authorized_session, monkeypatch):
    """Same two-asset shape, but asset B's fetch is a deterministic (non-retryable)
    failure. Real ``fetch_run`` records it ``failed`` and leaves it ``pending``
    for analyze (its per-asset loop only ever processes fetch-``ok`` rows) -- so
    the real ``coordinator.advance`` must see an incomplete crawl and finalize
    PARTIAL with ``completeness.fetch_ok`` False, never DONE."""
    tenant, session_id = authorized_session
    run_id = _seed_crawl_run(redis, tenant, session_id, [_URL_A, _URL_B])

    def fake_fetch(url, scope, **kw):
        if url == _URL_B:
            raise retry.FatalError("HTTP 404")
        return fetch._FetchedResponse(
            body=b'fetch("/api/shared");', status=200, headers={}, set_cookie=[]
        )

    monkeypatch.setattr(fetch, "_fetch_hops", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    rows = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    assert rows[_URL_A].fetch_status == "ok"
    assert rows[_URL_B].fetch_status == "failed"
    assert rows[_URL_B].analyze_status == "pending"  # never fetched -> never analyzed

    _walk_to_correlating(redis, tenant, run_id)
    coordinator.advance(redis, tenant_id=tenant, run_id=run_id, completed=RunStage.CORRELATING)

    state, completeness = _run_state(tenant, run_id)
    assert state == "partial"
    assert completeness["fetch_ok"] is False


# ---------------------------------------------------------------------------
# Part B -- discovery-layer half of the multi-asset proof: real katana against
# the fixture site. Engine-gated: SKIPS cleanly on a host with no katana binary
# (this one); RECON_REQUIRE_ENGINES=1 (CI/container) turns that into a failure.
# ---------------------------------------------------------------------------

FIXTURE_URL = "http://recon.test/"
SCOPE = ["recon.test"]


def test_real_katana_discovers_multiple_js_assets(engines_required):
    """A real katana crawl of the fixture site yields >= 2 distinct ``.js`` asset
    URLs -- the discovery-layer proof that a crawl surfaces multiple assets for
    Part A's fetch/analyze/finalize chain to run over. Modeled exactly on
    ``crawl_integration_test.py::test_real_katana_crawl_discovers_in_scope_js``
    (same skip-guard, same fixture, same duration/settings)."""
    settings = get_settings()
    argv = katana.build_argv(
        katana_bin=settings.katana_bin,
        domain=FIXTURE_URL,
        scope_hosts=SCOPE,
        depth=settings.crawl_depth,
        crawl_duration_seconds=30.0,
    )
    try:
        with patch("recon.discover.harness.progress.beat"):
            result = harness.run_crawl(
                None,
                argv,
                tenant_id="t",
                run_id="r",
                job_id="j",
                duration_seconds=30.0,
                kill_grace_seconds=5.0,
                heartbeat_interval_seconds=5.0,
                max_output_bytes=settings.crawl_max_output_bytes,
            )
    except FileNotFoundError:
        if engines_required:
            raise
        pytest.skip("katana binary not available on this host")
    urls = katana.parse_assets(result.stdout)
    js_urls = {u for u in urls if u.endswith(".js")}
    assert len(js_urls) >= 2, urls
