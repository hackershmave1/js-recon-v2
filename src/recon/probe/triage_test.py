import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store
from recon.probe import triage
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _run(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        return str(run.id)


def _run_with_finding(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="GET /ping", path="input.js",
            occurrence=store.Occurrence(host="api.acme.io", raw_url="/ping"),
            attributes={"method": "GET", "kind": "fetch"}, first_stage="analyzing",
        )
        return run_id, result.finding_hash


def test_set_triage_upserts_and_emits_event():
    tenant = sessions_service.create_tenant("tri-1")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id, finding_hash = _run_with_finding(tenant, session_view.id)

    state = triage.set_triage_for_run(
        tenant, run_id, finding_hash, status="confirmed", note="n1", actor="a1"
    )
    assert state.status == "confirmed"

    # A second verdict updates the same row (upsert), not a new one.
    triage.set_triage_for_run(tenant, run_id, finding_hash, status="dismissed")
    with tenant_session(tenant) as session:
        rows = session.query(models.FindingTriage).filter_by(finding_hash=finding_hash).all()
        assert len(rows) == 1 and rows[0].status == "dismissed"
        # The status-only second call must NOT clobber the note/actor set by the first.
        assert rows[0].note == "n1"
        assert rows[0].actor == "a1"
        events = session.query(models.RunEvent).filter_by(type="triage.updated").all()
        assert len(events) == 2


def test_set_triage_unknown_run_returns_none():
    tenant = sessions_service.create_tenant("tri-2")
    assert triage.set_triage_for_run(
        tenant, "00000000-0000-0000-0000-000000000000", "b" * 64, status="confirmed"
    ) is None


def test_set_triage_invalid_status_raises():
    tenant = sessions_service.create_tenant("tri-3")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _run(tenant, session_view.id)
    with pytest.raises(ValueError):
        triage.set_triage_for_run(tenant, run_id, "c" * 64, status="bogus")


def test_set_triage_unknown_finding_returns_none():
    tenant = sessions_service.create_tenant("tri-4")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _run(tenant, session_view.id)
    assert triage.set_triage_for_run(
        tenant, run_id, "d" * 64, status="confirmed"
    ) is None
