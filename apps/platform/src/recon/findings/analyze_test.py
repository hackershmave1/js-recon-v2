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

_JS_RISK = """
axios.get("/api/session", {params:{token:"x"}});
fetch("/api/orders", {method:"POST", body:JSON.stringify({userId:1, name:"n"})});
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
            session.execute(select(models.Finding).where(models.Finding.run_id == run_id)).scalars()
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


def test_param_findings_carry_risk_tags(redis, authorized_session):
    """Enrichment A: a risk-relevant param name is tagged in the finding's attributes, and an
    untagged param carries no risk_tags key (honest silence, kept clean)."""
    tenant, session_id = authorized_session
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=_JS_RISK, target="acme.io"
    )
    assert _drive(redis, view.id, tenant) == RunState.DONE.value

    params = {f.value: f for f in _findings(tenant, view.id) if f.type == "param"}
    assert params["GET /api/session query:token"].attributes["risk_tags"] == ["auth"]
    assert params["POST /api/orders body:userId"].attributes["risk_tags"] == ["idor"]
    assert "risk_tags" not in params["POST /api/orders body:name"].attributes


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


def _seed_capture_asset(redis, tenant, session_id, *, js: bytes, map_blob: bytes) -> str:
    """A capture-ingested asset (run_asset row, fetch_ok) carrying its own source
    map — the shape the extension->platform ingest produces (Phase 3)."""
    from recon import storage
    from recon.db.base import tenant_session
    from recon.domain import AssetStatus
    from recon.runs import service

    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    input_key = storage.put_blob(tenant, view.id, "input", js)
    map_key = storage.put_blob(tenant, view.id, "source_map", map_blob)
    with tenant_session(tenant) as session:
        session.add(
            models.RunAsset(
                tenant_id=tenant,
                run_id=view.id,
                url="https://acme.io/app.js",
                input_ref=input_key,
                source_map_ref=map_key,
                fetch_status=AssetStatus.OK.value,
                analyze_status=AssetStatus.PENDING.value,
            )
        )
    return view.id


def test_capture_asset_recovers_sources_from_its_map(redis, authorized_session, monkeypatch):
    # A capture-ingested asset carries its OWN source map (run_asset.source_map_ref).
    # Analyze recovers the real per-source path from it (origin "capture"), exactly
    # like the legacy run-level map — recover_sources is faked (no Go binary).
    from recon.findings import analyze, sourcemapper

    tenant, session_id = authorized_session

    def fake_recover(map_bytes, **_kwargs):
        return sourcemapper.RecoveredSources(
            files=[sourcemapper.RecoveredFile("app/src/api.js", b'fetch("/api/widgets/7");')],
            status="ok",
            origin="capture",
        )

    monkeypatch.setattr(sourcemapper, "recover_sources", fake_recover)
    run_id = _seed_capture_asset(
        redis, tenant, session_id, js=b'fetch("/bundle/only");', map_blob=b'{"version":3}'
    )

    coverage = analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)

    assert coverage.source_map == "capture"
    endpoints = [f for f in _findings(tenant, run_id) if f.type == "endpoint"]
    assert [e.path for e in endpoints] == ["app/src/api.js"]  # real path, not the bundle
    assert endpoints[0].value == "GET /api/widgets/{id}"


def test_capture_asset_bad_map_falls_back_and_asset_still_ok(
    redis, authorized_session, monkeypatch
):
    # THE safety guarantee: an unparseable capture map must NOT fail the asset (which
    # would drop ALL its findings). It falls back to bundle analysis, the asset ends
    # OK (not analyze_failed), and coverage honestly reports "capture-error".
    from recon.db.base import tenant_session
    from recon.domain import AssetStatus
    from recon.findings import analyze, engines, sourcemapper
    from recon.runs import assets as run_assets

    tenant, session_id = authorized_session

    def boom(map_bytes, **_kwargs):
        raise engines.EngineError("unparseable capture map")

    monkeypatch.setattr(sourcemapper, "recover_sources", boom)
    run_id = _seed_capture_asset(
        redis, tenant, session_id, js=b'fetch("/api/health");', map_blob=b'{"version":3}'
    )

    coverage = analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)  # must NOT raise

    assert coverage.source_map == "capture-error"
    endpoint_values = {f.value for f in _findings(tenant, run_id) if f.type == "endpoint"}
    assert "GET /api/health" in endpoint_values  # bundle analyzed as the fallback
    with tenant_session(tenant):
        row = next(
            r for r in run_assets.list_for_run(tenant, run_id) if r.url == "https://acme.io/app.js"
        )
    assert row.analyze_status == AssetStatus.OK.value  # asset kept, not failed


def _endpoint_occurrences(tenant, run_id):
    with tenant_session(tenant) as session:
        return list(
            session.execute(
                select(models.FindingOccurrence)
                .join(models.Finding, models.FindingOccurrence.finding)
                .where(models.Finding.run_id == run_id, models.Finding.type == "endpoint")
            ).scalars()
        )


def test_no_map_bundle_beautified_gives_distinct_finding_lines(redis, authorized_session):
    # A minified no-map bundle is ~one line, so without beautify every endpoint
    # collapses to line 1 and cannot be located. Beautifying BEFORE extraction
    # (recon.findings.deobfuscate) puts each statement on its own line, so the three
    # endpoints get DISTINCT occurrence lines — the net-new value of Phase 1.
    from sqlalchemy import update

    from recon import storage
    from recon.db.base import tenant_session
    from recon.findings import analyze
    from recon.runs import service

    tenant, session_id = authorized_session
    minified = (
        b'const alpha=fetch("/api/alpha");'
        b'const bravo=fetch("/api/bravo");'
        b'const charlie=fetch("/api/charlie");'
    )
    assert len(minified.splitlines()) == 1  # genuinely one line before beautify

    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    input_key = storage.put_blob(tenant, view.id, "input", minified)
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run).where(models.Run.id == view.id).values(input_ref=input_key)
        )

    coverage = analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)
    assert coverage.source_map == "none"  # the no-map branch we beautify

    occurrences = _endpoint_occurrences(tenant, view.id)
    lines = [o.line for o in occurrences]
    assert len(occurrences) == 3
    assert len(set(lines)) == 3  # distinct lines, not all collapsed to line 1
    assert set(lines) != {1}


def test_secret_offset_stays_in_raw_space_on_beautified_no_map_bundle(
    redis, authorized_session, engines_required
):
    # T3 fence: even though the SAME bundle is beautified for endpoint extraction,
    # the secret is scanned on the RAW bytes and its occurrence offset must slice the
    # RAW decoded source back to the token (reveal would not 409). Guards that
    # beautified text never reaches the secret path in _analyze_blob.
    from sqlalchemy import update

    from recon import storage
    from recon.db.base import tenant_session
    from recon.findings import analyze, kingfisher
    from recon.runs import service

    tenant, session_id = authorized_session
    # Split literals so no secret-shaped token is committed; kingfisher reassembles.
    token = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc" + "ABCDEF0123"
    # Minified one-liner with endpoints (so beautify actually runs) AND a secret.
    raw = f'const k="{token}";const a=fetch("/api/a");const b=fetch("/api/b");'.encode()
    if kingfisher.scan(raw).status == "unavailable":
        if engines_required:
            pytest.fail("kingfisher binary required (RECON_REQUIRE_ENGINES) but unavailable")
        pytest.skip("kingfisher binary not available")

    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    input_key = storage.put_blob(tenant, view.id, "input", raw)
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run).where(models.Run.id == view.id).values(input_ref=input_key)
        )

    analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)

    with tenant_session(tenant) as session:
        secret = session.execute(
            select(models.FindingOccurrence)
            .join(models.Finding, models.FindingOccurrence.finding)
            .where(models.Finding.run_id == view.id, models.Finding.type == "secret")
        ).scalar_one()
    assert secret.offset_start is not None and secret.offset_end is not None
    # The stored offsets bound the token in the RAW byte space (== raw.decode(...)
    # re-encoded), NOT the beautified text — that is exactly what reveal.py slices.
    sliced = raw[secret.offset_start : secret.offset_end]
    assert sliced.decode("utf-8") == token


def test_legacy_uploaded_bad_map_still_raises(redis, authorized_session, monkeypatch):
    # Guards the refactor that added source_map_origin: a legacy explicit run-level
    # upload stays STRICT — an unparseable map surfaces (raises), not a silent
    # fallback. Only inline/capture maps are tolerant.
    from sqlalchemy import update

    from recon import storage
    from recon.db.base import tenant_session
    from recon.findings import analyze, engines, sourcemapper
    from recon.runs import service

    tenant, session_id = authorized_session
    monkeypatch.setattr(
        sourcemapper,
        "recover_sources",
        lambda *a, **k: (_ for _ in ()).throw(engines.EngineError("bad map")),
    )
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    input_key = storage.put_blob(tenant, view.id, "input", b'fetch("/x");')
    map_key = storage.put_blob(tenant, view.id, "source_map", b'{"version":3}')
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run)
            .where(models.Run.id == view.id)
            .values(input_ref=input_key, source_map_ref=map_key)
        )
    with pytest.raises(engines.EngineError):
        analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)
