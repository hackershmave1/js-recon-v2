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

_JS_AUTH = """
fetch("/api/me", {headers:{Authorization:"Bearer " + token}});
"""

_JS_GRAPHQL = """
const Me = gql`query Me { me { id email } }`;
const Me2 = gql`query Me { me { id email } }`;
const Card = gql`fragment UserCard on User { id name }`;
fetch("/api/health");
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


def test_unresolved_sink_surfaced_as_unconfirmed_and_excluded_from_endpoints(
    redis, authorized_session
):
    """Tier 4 (unconfirmed lane): a sink whose URL is a variable is surfaced as a distinct
    ENDPOINT_UNRESOLVED finding — not silently dropped — while (a) the REQ-C2 coverage
    counters are unchanged (it stays counted as `unattributed`, and the unconfirmed row never
    inflates `attributed`), and (b) it is excluded from the endpoint read model + the
    OpenAPI/probe reconstruction, both of which key on ``type == 'endpoint'``."""
    from recon.findings import queries as findings_queries
    from recon.probe.reconstruct import reconstruct_run

    tenant, session_id = authorized_session
    js = 'fetch("/api/real"); fetch(dynamicUrl); axios.post(u, {a:1});'
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=js, target="acme.io"
    )
    assert _drive(redis, view.id, tenant) == RunState.DONE.value

    findings = _findings(tenant, view.id)
    # The resolvable call is a confirmed endpoint; the two variable-URL calls are unconfirmed.
    assert {f.value for f in findings if f.type == "endpoint"} == {"GET /api/real"}
    unresolved = {f.value for f in findings if f.type == "endpoint_unresolved"}
    assert "GET :dynamicUrl" in unresolved  # fetch(dynamicUrl) -> value-holder token
    assert "POST EXPR" in unresolved  # axios.post(u, ...)

    # REQ-C2 honesty: 1 resolved endpoint + 2 unresolved sinks; the unconfirmed rows do NOT
    # inflate `attributed`, and the unresolved sinks are still counted as `unattributed`.
    coverage = findings_queries.list_findings(tenant, view.id).coverage
    assert coverage is not None
    assert (coverage.attributed, coverage.unattributed) == (1, 2)

    # Excluded from OpenAPI/probe reconstruction (keys on type == 'endpoint'): only the one
    # confirmed endpoint yields a probeable request — never an EXPR skeleton.
    reqs = reconstruct_run(tenant, view.id)
    assert reqs is not None and len(reqs) == 1


def test_generic_call_promoted_to_endpoint_suspected_and_excluded_from_confirmed(
    redis, authorized_session
):
    """A verb call on an unrecognised HTTP-client-shaped receiver whose path has a static segment
    (`/api/generic`) is PROMOTED to a distinct ENDPOINT_SUSPECTED finding — normalized like a
    confirmed endpoint so it unions into the total-endpoints consumers — while (a) the REQ-C2
    coverage counters stay UNTOUCHED (a suspected call is neither attributed nor unattributed),
    and (b) it stays OUT of the confirmed ``type == 'endpoint'`` read model. ENDPOINT_GENERIC
    production is retired (a no-static-path junk call would instead fall to ENDPOINT_UNRESOLVED)."""
    from recon.findings import queries as findings_queries
    from recon.probe.reconstruct import reconstruct_run

    tenant, session_id = authorized_session
    js = 'fetch("/api/real"); apiClient.get("/api/generic");'
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=js, target="acme.io"
    )
    assert _drive(redis, view.id, tenant) == RunState.DONE.value

    findings = _findings(tenant, view.id)
    assert {f.value for f in findings if f.type == "endpoint"} == {"GET /api/real"}
    # Promoted to the suspected lane (a distinct type), normalized like a real endpoint.
    assert {f.value for f in findings if f.type == "endpoint_suspected"} == {"GET /api/generic"}
    assert not [f for f in findings if f.type == "endpoint_generic"]  # generic production retired

    # REQ-C2 honesty: the promoted call moves NEITHER counter (1 real fetch attributed, nothing
    # unattributed — coverage% stays confirmed-only).
    coverage = findings_queries.list_findings(tenant, view.id).coverage
    assert coverage is not None
    assert (coverage.attributed, coverage.unattributed) == (1, 0)

    # Total-endpoints reconstruction (the OpenAPI/probe/threat-model feed): BOTH the confirmed
    # endpoint AND the promoted suspected endpoint reconstruct into a probeable request.
    reqs = reconstruct_run(tenant, view.id)
    assert reqs is not None and len(reqs) == 2


def test_page_route_surfaced_as_distinct_type_excluded_from_endpoints(redis, authorized_session):
    """Phase 2: a client-side navigation target (here an `href` built via .concat() off
    window.location.origin) is surfaced as a distinct PAGE_ROUTE finding — blank method, so its
    value is the bare route — while (a) it is excluded from the endpoint read model + the
    OpenAPI/probe reconstruction (both key on ``type == 'endpoint'``), and (b) it never moves the
    REQ-C2 coverage counters (a referenced route is not a detected backend sink)."""
    from recon.findings import queries as findings_queries
    from recon.probe.reconstruct import reconstruct_run

    tenant, session_id = authorized_session
    js = 'var link = {href:"".concat(window.location.origin, "/player/").concat(id)}; fetch("/api/real");'
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=js, target="acme.io"
    )
    assert _drive(redis, view.id, tenant) == RunState.DONE.value

    findings = _findings(tenant, view.id)
    # the confirmed fetch is an endpoint; the href route is a DISTINCT page_route, not an endpoint.
    assert {f.value for f in findings if f.type == "endpoint"} == {"GET /api/real"}
    routes = [f for f in findings if f.type == "page_route"]
    assert len(routes) == 1
    assert routes[0].value == "/player/:id"  # blank method -> value is the bare route
    assert routes[0].attributes["confidence"] == "low"  # an href key also appears in bodies/config

    # REQ-C2 coverage is untouched by the route: 1 real fetch attributed, nothing unattributed.
    coverage = findings_queries.list_findings(tenant, view.id).coverage
    assert coverage is not None
    assert (coverage.attributed, coverage.unattributed) == (1, 0)

    # Excluded from OpenAPI/probe reconstruction (keys on type == 'endpoint'): only the fetch.
    reqs = reconstruct_run(tenant, view.id)
    assert reqs is not None and len(reqs) == 1


def test_suspected_lane_splits_host_and_path_from_absolute_url(redis, authorized_session):
    """A promoted ENDPOINT_SUSPECTED finding whose sink was an absolute URL SPLITS the host off
    onto the occurrence and keeps the normalized PATH as its value (item #5) — so the host facet
    can pivot on the backend host while the value is a clean endpoint; a relative literal in the
    SAME lane stays ``host=None``. page_route (still an unconfirmed lane) instead keeps its raw
    URL in value + lifts the host (DEBT D24), so the two behaviours are contrasted here."""
    from recon.findings import queries as findings_queries

    tenant, session_id = authorized_session
    js = (
        'apiClient.get("https://api.nhle.com/stats/rest/en");'  # generic, absolute -> host lifted
        'apiClient.get("/api/relative");'  # generic, relative -> stays host-less
        'var nav = {href:"https://assets.nhle.com/mugs/latest"};'  # page_route, absolute -> host
        'fetch("/api/real");'  # a confirmed endpoint so the run has real coverage too
    )
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=js, target="acme.io"
    )
    assert _drive(redis, view.id, tenant) == RunState.DONE.value

    by_value = {f.value: f for f in findings_queries.list_findings(tenant, view.id).findings}

    suspected_abs = by_value["GET /stats/rest/en"]  # promoted: host SPLIT out of the value
    assert suspected_abs.type == "endpoint_suspected"
    assert {o.host for o in suspected_abs.occurrences} == {"api.nhle.com"}  # host on the occurrence
    assert "api.nhle.com" not in suspected_abs.value  # value IS host-stripped now (#5 split)

    suspected_rel = by_value["GET /api/relative"]
    assert suspected_rel.type == "endpoint_suspected"
    assert {o.host for o in suspected_rel.occurrences} == {None}  # relative literal -> host-less

    # page_route stays an unconfirmed lane (raw URL in value + host lifted, DEBT D24) — unlike the
    # promoted suspected lane above which normalizes + splits — so its host is lifted the same way.
    route_abs = by_value["https://assets.nhle.com/mugs/latest"]
    assert route_abs.type == "page_route"
    assert {o.host for o in route_abs.occurrences} == {"assets.nhle.com"}


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


def test_endpoint_finding_carries_auth_headers(redis, authorized_session):
    """Enrichment B: a captured auth request header lands in the endpoint finding's
    attributes as name + scheme (never a credential value)."""
    tenant, session_id = authorized_session
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=_JS_AUTH, target="acme.io"
    )
    assert _drive(redis, view.id, tenant) == RunState.DONE.value

    endpoints = {f.value: f for f in _findings(tenant, view.id) if f.type == "endpoint"}
    assert endpoints["GET /api/me"].attributes["auth"] == [
        {"name": "Authorization", "scheme": "bearer"}
    ]


def test_graphql_definitions_are_located_findings_and_still_exported(redis, authorized_session):
    """GraphQL documents are now first-class ``FindingType.GRAPHQL`` findings — LOCATED to their
    bundle call-site, with fragments surfaced and the same op at two sites kept as two occurrences
    — AND still carried by the OpenAPI export, but NEVER an endpoint/param finding or an HTTP path
    (a GraphQL op is not an HTTP call). The real fetch in the same bundle is still extracted
    normally. Exercises the analyze finding-write (location + fragment + multi-occurrence) plus the
    unchanged export chain (queries.graphql_operations union -> build_openapi emit)."""
    from openapi_spec_validator import validate

    from recon.findings import queries as findings_queries
    from recon.probe import openapi
    from recon.probe.reconstruct import reconstruct_run

    tenant, session_id = authorized_session
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=_JS_GRAPHQL, target="acme.io"
    )
    assert _drive(redis, view.id, tenant) == RunState.DONE.value

    findings = _findings(tenant, view.id)
    # No endpoint/param pollution — only the real fetch is an endpoint.
    assert {f.value for f in findings if f.type == "endpoint"} == {"GET /api/health"}
    assert [f for f in findings if f.type == "param"] == []
    # A query operation AND a fragment both became first-class graphql findings.
    assert {f.value for f in findings if f.type == "graphql"} == {
        "query Me",
        "fragment UserCard on User",
    }

    # Location survives persistence, and the op seen at two call sites is ONE finding with TWO
    # occurrences at distinct byte offsets — asserted inside a session so occurrences load.
    with tenant_session(tenant) as session:
        rows = (
            session.execute(
                select(models.Finding).where(
                    models.Finding.run_id == view.id, models.Finding.type == "graphql"
                )
            )
            .scalars()
            .all()
        )
        by_value = {f.value: f for f in rows}
        me = by_value["query Me"]
        assert me.attributes["kind"] == "query"
        assert me.attributes["fields"] == ["me"]
        assert len(me.occurrences) == 2  # two call sites -> two occurrences (REQ-C2)
        assert all(o.line is not None and o.offset_start is not None for o in me.occurrences)
        assert len({o.offset_start for o in me.occurrences}) == 2  # distinct call-site offsets
        fragment = by_value["fragment UserCard on User"]
        assert fragment.attributes["kind"] == "fragment"
        assert fragment.attributes["on_type"] == "User"
        assert fragment.attributes["fields"] == ["id", "name"]

    # ...and operations are still carried by the export, byte-for-byte (op_type key, fragment
    # excluded, the two identical ops deduped to one).
    ops = findings_queries.graphql_operations(tenant, view.id)
    assert ops == [{"op_type": "query", "name": "Me", "fields": ["me"], "source_path": "input.js"}]

    doc = openapi.build_openapi(
        reconstruct_run(tenant, view.id), run_id=view.id, graphql_operations=ops
    )
    validate(doc)  # the extension must keep the exported document valid
    assert doc["x-recon-graphql-operations"] == ops
    assert list(doc["paths"]) == ["/api/health"]  # the GraphQL op is NEVER an HTTP path


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
    # not the minified bundle. iter_recovered_files is faked so no Go binary is needed
    # (D37-L2 slice 3: analyze streams recovery through it); the analyze stage is exercised
    # directly. The run is created WITHOUT enqueuing a stage, so the test leaves no stray
    # message in the shared-Redis queues (the full worker pipeline is covered by other tests).
    from sqlalchemy import update

    from recon import storage
    from recon.db.base import tenant_session
    from recon.findings import analyze, sourcemapper
    from recon.runs import service

    tenant, session_id = authorized_session

    def fake_iter(_map_path, **_kwargs):
        yield "app/src/api.js", b'fetch("/api/widgets/7");'

    monkeypatch.setattr(sourcemapper, "iter_recovered_files", fake_iter)

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


def test_recovered_source_secret_recorded_at_its_path(
    redis, authorized_session, monkeypatch, engines_required
):
    # D32-B1: a secret living ONLY in a source-map-recovered original (here in a COMMENT,
    # which a minifier strips) is caught by scanning the recovered sources and attributed
    # to the real per-source path — invisible to the raw-bundle-only scan, and counted so
    # it is never silently under-reported (the exact D32 honesty gap).
    from sqlalchemy import update

    from recon import storage
    from recon.db.base import tenant_session
    from recon.findings import analyze, kingfisher, sourcemapper
    from recon.runs import service

    tenant, session_id = authorized_session
    token = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc" + "ABCDEF0123"
    recovered = (
        f'// prod config (never shipped minified)\nconst KEY = "{token}";\n'
        'export const pay = () => fetch("/api/pay");\n'
    ).encode()
    if kingfisher.scan(recovered).status == "unavailable":
        if engines_required:
            pytest.fail("kingfisher binary required (RECON_REQUIRE_ENGINES) but unavailable")
        pytest.skip("kingfisher binary not available")

    def fake_iter(_map_path, **_kwargs):
        yield "app/src/config.js", recovered

    monkeypatch.setattr(sourcemapper, "iter_recovered_files", fake_iter)

    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    # The minified bundle carries NO secret — only an endpoint — so the token is
    # recovered-only: the bundle scan can't see it.
    input_key = storage.put_blob(tenant, view.id, "input", b'fetch("/api/pay");')
    map_key = storage.put_blob(tenant, view.id, "source_map", b'{"version":3}')
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run)
            .where(models.Run.id == view.id)
            .values(input_ref=input_key, source_map_ref=map_key)
        )

    coverage = analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)

    secrets = [f for f in _findings(tenant, view.id) if f.type == "secret"]
    assert len(secrets) == 1  # the recovered-only secret is surfaced
    assert coverage.secrets == 1  # and counted (REQ-C2 honesty), not silently dropped
    # Attributed to the recovered path (occurrence.source_path), NOT the "input.js" bundle
    # — so reveal re-derives the map, and the Sources tab lists it under the real file.
    assert [o.source_path for o in _secret_occurrences(tenant, view.id)] == ["app/src/config.js"]


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

    def boom(*_a, **_k):
        raise engines.EngineError("unparseable source map")

    monkeypatch.setattr(sourcemapper, "iter_recovered_files", boom)

    # Inline map is base64 of {"version":3} — passes the JSON sanity check, so it
    # reaches recovery (stubbed to fail as the real tool would).
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

    def fake_iter(_map_path, **_kwargs):
        yield "app/clean.js", b'fetch("/api/a");'
        yield "app/dynamic.js", b"fetch(runtimeUrl);"

    monkeypatch.setattr(sourcemapper, "iter_recovered_files", fake_iter)

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

    def fake_iter(_map_path, **_kwargs):
        yield "app/src/api.js", b'fetch("/api/widgets/7");'

    monkeypatch.setattr(sourcemapper, "iter_recovered_files", fake_iter)
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

    def boom(*_a, **_k):
        raise engines.EngineError("unparseable capture map")

    monkeypatch.setattr(sourcemapper, "iter_recovered_files", boom)
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


def test_skipped_source_map_reports_skipped_coverage(redis, authorized_session):
    # D32: an asset whose referenced .map the fetch stage couldn't retrieve
    # (source_map_skipped=True, no source_map_ref) reports coverage source_map:"skipped"
    # — NOT the "none" a genuinely map-less bundle gets — while STILL analyzing the
    # minified bundle so its findings are never dropped.
    from recon import storage
    from recon.db.base import tenant_session
    from recon.domain import AssetStatus
    from recon.findings import analyze
    from recon.runs import assets as run_assets
    from recon.runs import service

    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    input_key = storage.put_blob(tenant, view.id, "input", b'fetch("/api/health");')
    with tenant_session(tenant) as session:
        session.add(
            models.RunAsset(
                tenant_id=tenant,
                run_id=view.id,
                url="https://acme.io/app.js",
                input_ref=input_key,
                source_map_skipped=True,  # a referenced map the fetch stage soft-missed
                fetch_status=AssetStatus.OK.value,
                analyze_status=AssetStatus.PENDING.value,
            )
        )

    coverage = analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)

    assert coverage.source_map == "skipped"  # honest gap, not silent "none"
    endpoint_values = {f.value for f in _findings(tenant, view.id) if f.type == "endpoint"}
    assert "GET /api/health" in endpoint_values  # bundle still analyzed, finding kept
    with tenant_session(tenant):
        rows = run_assets.list_for_run(tenant, view.id)
        row = next(r for r in rows if r.url == "https://acme.io/app.js")
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


def _secret_occurrences(tenant, run_id):
    with tenant_session(tenant) as session:
        return list(
            session.execute(
                select(models.FindingOccurrence)
                .join(models.Finding, models.FindingOccurrence.finding)
                .where(models.Finding.run_id == run_id, models.Finding.type == "secret")
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
        "iter_recovered_files",
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
