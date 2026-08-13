"""Read model for a run's findings — backs ``GET /runs/{run_id}/findings``.

Distinguishes three cases the HTTP layer maps to status codes: run absent / other
tenant (``None`` -> 404), run present with findings, and run present with none
(empty list -> 200, never conflated with 404). Marked integration: needs the full
compose stack (Postgres RLS, Redis, MinIO).
"""

from __future__ import annotations

import json

import pytest

from recon.domain import RunState
from recon.findings import queries as findings_queries
from recon.runs import coordinator, queries
from recon.sessions import service as sessions_service
from recon.spec import service as spec_service
from recon.worker import main as worker

pytestmark = pytest.mark.integration

_JS = 'fetch("/api/health"); axios.post("/api/login", {u:1});'

_TERMINAL = {
    s.value for s in (RunState.DONE, RunState.PARTIAL, RunState.FAILED, RunState.CANCELLED)
}

# A minimal, schema-valid OpenAPI 3.0 doc documenting exactly `_JS`'s "GET
# /api/health" call and nothing else, so its sibling "POST /api/login" finding
# comes back `shadow` -- mirrors `spec/service_test.py`'s OPENAPI_WITH_LOCATION.
_OPENAPI_WITH_HEALTH = b"""openapi: 3.0.0
info: {title: t, version: '1'}
paths: {/api/health: {get: {responses: {'200': {description: ok}}}}}
"""


def _drive(redis, tenant: str, run_id: str, *, max_passes: int = 30) -> None:
    for _ in range(max_passes):
        worker.run_once(redis, "fq-test-worker", block_ms=50)
        status = queries.get_status(tenant, run_id)
        if status and status.state in _TERMINAL:
            return


def test_list_findings_returns_findings_with_occurrences(redis, authorized_session):
    tenant, session_id = authorized_session
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=_JS
    )
    _drive(redis, tenant, view.id)

    result = findings_queries.list_findings(tenant, view.id)
    assert result is not None
    values = {f.value for f in result.findings}
    assert "GET /api/health" in values
    assert "POST /api/login" in values

    health = next(f for f in result.findings if f.value == "GET /api/health")
    assert health.type == "endpoint"
    assert health.occurrences and health.occurrences[0].raw_url == "/api/health"


def test_list_findings_includes_coverage(redis, authorized_session):
    # REQ-C2: the read model surfaces the analyze coverage counters next to the
    # findings they qualify — read from the durable event log, not recomputed.
    tenant, session_id = authorized_session
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=_JS
    )
    _drive(redis, tenant, view.id)

    result = findings_queries.list_findings(tenant, view.id)
    assert result is not None and result.coverage is not None
    # _JS has two attributable calls and no dynamic/unresolvable one.
    assert result.coverage.attributed == 2
    assert result.coverage.unattributed == 0
    assert result.coverage.source_map == "none"
    assert [f.attributed for f in result.coverage.files] == [2]


def test_list_findings_coverage_is_none_before_analyze(redis, authorized_session):
    # A no-input run reaches done but analyze is a no-op that emits no coverage
    # event — coverage is null, distinct from "analyzed, found nothing".
    tenant, session_id = authorized_session
    view = coordinator.start_run(redis, tenant_id=tenant, session_id=session_id)
    _drive(redis, tenant, view.id)

    result = findings_queries.list_findings(tenant, view.id)
    assert result is not None
    assert result.coverage is None


def test_list_findings_unknown_run_is_none(tenant):
    missing = "00000000-0000-0000-0000-000000000000"
    assert findings_queries.list_findings(tenant, missing) is None


def test_list_findings_empty_when_run_has_no_findings(redis, authorized_session):
    # A no-input run reaches done but analyze is a no-op -> exists, zero findings.
    tenant, session_id = authorized_session
    view = coordinator.start_run(redis, tenant_id=tenant, session_id=session_id)
    _drive(redis, tenant, view.id)

    result = findings_queries.list_findings(tenant, view.id)
    assert result is not None
    assert result.findings == []


def test_list_findings_is_tenant_isolated(redis, authorized_session):
    tenant, session_id = authorized_session
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=_JS
    )
    _drive(redis, tenant, view.id)

    # A different tenant cannot even see the run (RLS) -> None, not an empty list.
    intruder = sessions_service.create_tenant("intruder")
    assert findings_queries.list_findings(intruder, view.id) is None


def test_list_findings_spec_status_and_summary_none_without_session_spec(redis, authorized_session):
    # Design §6.4: no spec was ever attached to the run's session -> every
    # finding is unclassified (spec_status is None, the API renders
    # "unclassified") and the run-scoped summary itself is None -- NOT an
    # all-zero SpecSummary, which would misrepresent "never attached" as
    # "attached, all shadow/unresolved".
    tenant, session_id = authorized_session
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=_JS
    )
    _drive(redis, tenant, view.id)

    result = findings_queries.list_findings(tenant, view.id)
    assert result is not None
    assert any(f.type == "endpoint" for f in result.findings)  # sanity: something to classify
    assert all(f.spec_status is None for f in result.findings)
    assert result.spec_summary is None


def test_list_findings_includes_spec_status_and_run_scoped_summary(redis, authorized_session):
    # Design §6.4: once a spec is attached to the run's session and its endpoint
    # findings classified, each finding carries the verdict and the run gets a
    # bucket-count summary scoped to its OWN endpoint findings.
    tenant, session_id = authorized_session
    view = coordinator.start_run_with_input(
        redis, tenant_id=tenant, session_id=session_id, js_source=_JS
    )
    _drive(redis, tenant, view.id)

    classify_summary = spec_service.attach_and_classify(tenant, view.id, _OPENAPI_WITH_HEALTH)
    assert classify_summary is not None

    result = findings_queries.list_findings(tenant, view.id)
    assert result is not None

    health = next(f for f in result.findings if f.value == "GET /api/health")
    assert health.spec_status is not None
    assert health.spec_status.status == "documented"
    assert health.spec_status.matched_operation == "GET /api/health"

    login = next(f for f in result.findings if f.value == "POST /api/login")
    assert login.spec_status is not None
    assert login.spec_status.status == "shadow"
    assert login.spec_status.matched_operation is None

    assert result.spec_summary is not None
    assert result.spec_summary.documented == 1
    assert result.spec_summary.shadow == 1
    assert result.spec_summary.unresolved == 0
    assert result.spec_summary.suffix_verify == 0
    assert result.spec_summary.base_url_incompleteness_ratio == 0.0


def test_graphql_operations_unions_dedups_and_sorts_across_assets(authorized_session):
    """M4: a crawl analyzes once PER asset, each emitting its OWN ``analyze.graphql`` event/blob.
    ``graphql_operations`` must UNION every event (read-latest would drop all but the last asset),
    dedup an operation two assets share, and return a deterministic order."""
    from recon import storage
    from recon.db import models
    from recon.db.base import tenant_session

    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)

    shared = {"op_type": "query", "name": "Me", "fields": ["me"], "source_path": "https://h/a.js"}
    asset_a = [
        shared,
        {"op_type": "mutation", "name": "Go", "fields": ["go"], "source_path": "https://h/a.js"},
    ]
    asset_b = [
        shared,
        {"op_type": "query", "name": "List", "fields": ["items"], "source_path": "https://h/b.js"},
    ]
    for entries in (asset_a, asset_b):
        ref = storage.put_blob(tenant, run_id, "graphql", json.dumps(entries).encode("utf-8"))
        with tenant_session(tenant) as session:
            session.add(
                models.RunEvent(
                    tenant_id=tenant,
                    run_id=run_id,
                    type="analyze.graphql",
                    payload={"count": len(entries), "graphql_ref": ref},
                )
            )

    ops = findings_queries.graphql_operations(tenant, run_id)

    # Union across BOTH events (read-latest would drop "Go"), the shared "Me" deduped to one,
    # sorted by (op_type, name, fields, source_path): mutation < query, then List < Me.
    assert ops == [
        {"op_type": "mutation", "name": "Go", "fields": ["go"], "source_path": "https://h/a.js"},
        {"op_type": "query", "name": "List", "fields": ["items"], "source_path": "https://h/b.js"},
        {"op_type": "query", "name": "Me", "fields": ["me"], "source_path": "https://h/a.js"},
    ]
