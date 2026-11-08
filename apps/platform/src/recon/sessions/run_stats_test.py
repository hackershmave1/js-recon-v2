"""Per-run counts for the workspace run switcher: the same "files" and "endpoints" numbers the
Sessions card shows, so a big earlier capture stays comparable to a smaller later one."""

import pytest

from recon.db.base import tenant_session
from recon.db.models import Run
from recon.domain import FindingType
from recon.findings import store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _run(tenant, session_id) -> str:
    with tenant_session(tenant) as s:
        run = Run(tenant_id=tenant, session_id=session_id, state="done", input_ref="x")
        s.add(run)
        s.flush()
        return str(run.id)


def _endpoint(tenant, run_id, value, host):
    with tenant_session(tenant) as s:
        store.record_finding(
            s,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.ENDPOINT,
            value=value,
            path="input.js",
            occurrence=store.Occurrence(host=host, raw_url="/x"),
            attributes={"method": "GET"},
            first_stage="analyzing",
        )


def test_each_run_carries_its_shown_endpoint_count(authorized_session):
    tenant, session_id = authorized_session
    small = _run(tenant, session_id)
    big = _run(tenant, session_id)
    _endpoint(tenant, small, "GET /a", "acme.io")
    for path in ("/a", "/b", "/c"):
        _endpoint(tenant, big, f"GET {path}", "acme.io")
    _endpoint(tenant, big, "POST /collect", "www.google-analytics.com")  # hidden: not counted

    runs = {r.run_id: r for r in sessions_service.list_runs_for_session(tenant, session_id)}

    assert (runs[small].files, runs[small].endpoints) == (1, 1)
    assert (runs[big].files, runs[big].endpoints) == (1, 3)
