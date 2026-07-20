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
