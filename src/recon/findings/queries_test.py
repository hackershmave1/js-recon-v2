"""Read model for a run's findings — backs ``GET /runs/{run_id}/findings``.

Distinguishes three cases the HTTP layer maps to status codes: run absent / other
tenant (``None`` -> 404), run present with findings, and run present with none
(empty list -> 200, never conflated with 404). Marked integration: needs the full
compose stack (Postgres RLS, Redis, MinIO).
"""

from __future__ import annotations

import pytest

from recon.domain import RunState
from recon.findings import queries as findings_queries
from recon.runs import coordinator, queries
from recon.sessions import service as sessions_service
from recon.worker import main as worker

pytestmark = pytest.mark.integration

_JS = 'fetch("/api/health"); axios.post("/api/login", {u:1});'

_TERMINAL = {s.value for s in (RunState.DONE, RunState.PARTIAL, RunState.FAILED, RunState.CANCELLED)}


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
