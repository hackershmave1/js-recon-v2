"""End-to-end integration test for "one JS file -> findings".

Drives a real run from creation (with a JS input blob in object storage) through
the worker to `done`, then asserts the analyze stage extracted, normalized, and
persisted the findings. Requires the full compose stack (Postgres, Redis, MinIO).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import RunState
from recon.runs import coordinator, queries
from recon.worker import main as worker

pytestmark = pytest.mark.integration

_JS = """
const a = await fetch("/api/users/42", {method:"POST", body:JSON.stringify({name:"n", email:"e"})});
axios.get("/api/orders", {params:{page:2}});
$.post("/api/login", {user:1});
new WebSocket("wss://rt.acme.io/socket/7");
fetch(dynamicUrl);
"""


def _drive(redis, run_id, tenant, *, max_passes=30) -> str:
    terminal = {RunState.DONE, RunState.PARTIAL, RunState.FAILED, RunState.CANCELLED}
    for _ in range(max_passes):
        worker.run_once(redis, "analyze-test-worker", block_ms=50)
        status = queries.get_status(tenant, run_id)
        if status and RunState(status.state) in terminal:
            return status.state
    return queries.get_status(tenant, run_id).state


def _findings(tenant, run_id):
    with tenant_session(tenant) as session:
        return list(
            session.execute(
                select(models.Finding).where(models.Finding.run_id == run_id)
            ).scalars()
        )


def test_js_input_run_produces_findings(redis, authorized_session):
    tenant, session_id = authorized_session
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=_JS, target="acme.io"
    )

    assert _drive(redis, view.id, tenant) == RunState.DONE.value

    findings = _findings(tenant, view.id)
    endpoint_values = {f.value for f in findings if f.type == "endpoint"}
    assert "POST /api/users/{id}" in endpoint_values
    assert "GET /api/orders" in endpoint_values  # config `params` are param findings, not URL query
    assert "POST /api/login" in endpoint_values
    assert any(v.startswith("WSS /socket/") for v in endpoint_values)

    # Params were mined into their own findings (fetch body, axios query, jQuery body).
    param_values = {f.value for f in findings if f.type == "param"}
    assert "POST /api/users/{id} body:name" in param_values
    assert "GET /api/orders query:page" in param_values
    assert "POST /api/login body:user" in param_values


def test_coverage_event_counts_unattributed(redis, authorized_session):
    # The dynamic `fetch(dynamicUrl)` must be counted honestly, not invented.
    tenant, session_id = authorized_session
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=_JS
    )
    _drive(redis, view.id, tenant)

    with tenant_session(tenant) as session:
        coverage = session.execute(
            select(models.RunEvent.payload).where(
                models.RunEvent.run_id == view.id,
                models.RunEvent.type == "analyze.coverage",
            )
        ).scalar_one()
    assert coverage["unattributed"] == 1
    assert coverage["attributed"] == 4


def test_secret_in_js_produces_secret_finding(redis, authorized_session, engines_required):
    import pytest

    from recon.findings import kingfisher

    tenant, session_id = authorized_session
    # Split literals so no secret-shaped token is committed; kingfisher reassembles.
    token = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc" + "ABCDEF0123"
    js = f'const apiKey = "{token}";\nfetch("/api/ping");\n'
    if kingfisher.scan(js.encode("utf-8")).status == "unavailable":
        if engines_required:
            pytest.fail("kingfisher binary required (RECON_REQUIRE_ENGINES) but unavailable")
        pytest.skip("kingfisher binary not available")

    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=js
    )
    assert _drive(redis, view.id, tenant) == RunState.DONE.value

    findings = _findings(tenant, view.id)
    secret_values = {f.value for f in findings if f.type == "secret"}
    assert any(v.startswith("stripe:") for v in secret_values)


def test_recovered_sources_get_real_paths(redis, authorized_session, monkeypatch):
    # With a source map, endpoints come from the RECOVERED source (real path),
    # not the minified bundle. recover_sources is faked so no Go binary is needed;
    # the analyze stage is exercised directly. The run is created WITHOUT enqueuing
    # a stage, so the test leaves no stray message in the shared-Redis queues (the
    # full worker pipeline is covered by other tests).
    from sqlalchemy import update

    from recon import storage
    from recon.db.base import tenant_session
    from recon.findings import analyze, sourcemapper
    from recon.runs import service

    tenant, session_id = authorized_session

    def fake_recover(map_bytes, **_kwargs):
        return sourcemapper.RecoveredSources(
            files=[sourcemapper.RecoveredFile("app/src/api.js", b'fetch("/api/widgets/7");')],
            status="ok",
            origin="uploaded",
        )

    monkeypatch.setattr(sourcemapper, "recover_sources", fake_recover)

    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    input_key = storage.put_blob(tenant, view.id, "input", b'fetch("/bundle/only");')
    map_key = storage.put_blob(tenant, view.id, "source_map", b'{"version":3}')
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run)
            .where(models.Run.id == view.id)
            .values(input_ref=input_key, source_map_ref=map_key)
        )

    analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)

    endpoints = [f for f in _findings(tenant, view.id) if f.type == "endpoint"]
    # Attributed to the real source path, reflecting the recovered source's URL —
    # the bundle's own /bundle/only endpoint is not analyzed when a map is present.
    assert [e.path for e in endpoints] == ["app/src/api.js"]
    assert endpoints[0].value == "GET /api/widgets/{id}"


def test_malformed_inline_map_falls_back_to_bundle(redis, authorized_session, monkeypatch):
    # A malformed inline map (attacker-influenced — it rides in the analyzed JS)
    # must NOT fail the run; analyze falls back to bundle analysis and records the
    # honest "inline-error" status.
    from sqlalchemy import update

    from recon import storage
    from recon.db.base import tenant_session
    from recon.findings import analyze, engines, sourcemapper
    from recon.runs import service

    tenant, session_id = authorized_session

    def boom(map_bytes, **_kwargs):
        raise engines.EngineError("unparseable source map")

    monkeypatch.setattr(sourcemapper, "recover_sources", boom)

    # Inline map is base64 of {"version":3} — passes the JSON sanity check, so it
    # reaches recover_sources (which is stubbed to fail as the real tool would).
    js = 'fetch("/api/health");\n//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozfQ=='
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    key = storage.put_blob(tenant, view.id, "input", js.encode("utf-8"))
    with tenant_session(tenant) as session:
        session.execute(update(models.Run).where(models.Run.id == view.id).values(input_ref=key))

    coverage = analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)  # must not raise

    assert coverage.source_map == "inline-error"
    endpoint_values = {f.value for f in _findings(tenant, view.id) if f.type == "endpoint"}
    assert "GET /api/health" in endpoint_values  # bundle analyzed as the fallback


def test_coverage_is_reported_per_file(redis, authorized_session, monkeypatch):
    # With two recovered sources, the attributed/unattributed counter is reported
    # PER FILE (REQ-C2) so a reader sees WHICH file has unmapped calls — a
    # bundle-wide sum would hide that, and that per-file signal is exactly what the
    # wrapper-teaching SHOULD acts on. recover_sources is faked (no Go binary).
    from sqlalchemy import update

    from recon import storage
    from recon.db.base import tenant_session
    from recon.findings import analyze, sourcemapper
    from recon.runs import service

    tenant, session_id = authorized_session

    def fake_recover(map_bytes, **_kwargs):
        return sourcemapper.RecoveredSources(
            files=[
                sourcemapper.RecoveredFile("app/clean.js", b'fetch("/api/a");'),
                sourcemapper.RecoveredFile("app/dynamic.js", b"fetch(runtimeUrl);"),
            ],
            status="ok",
            origin="uploaded",
        )

    monkeypatch.setattr(sourcemapper, "recover_sources", fake_recover)

    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    input_key = storage.put_blob(tenant, view.id, "input", b'fetch("/bundle");')
    map_key = storage.put_blob(tenant, view.id, "source_map", b'{"version":3}')
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run)
            .where(models.Run.id == view.id)
            .values(input_ref=input_key, source_map_ref=map_key)
        )

    coverage = analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)

    # One attributed call (clean.js) + one unattributed (dynamic.js) across the two.
    assert coverage.attributed == 1
    assert coverage.unattributed == 1
    by_path = {f.path: (f.attributed, f.unattributed) for f in coverage.files}
    assert by_path == {"app/clean.js": (1, 0), "app/dynamic.js": (0, 1)}
