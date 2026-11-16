"""The quarantine tables are tenant-isolated by the database, not just by the service's filters:
RLS is enabled and forced, and another tenant's unfiltered query sees none of the rows."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from recon.db import models
from recon.db.base import tenant_session
from recon.quarantine import service
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("table", models.QUARANTINE_TABLES)
def test_rls_is_enabled_and_forced(table):
    with tenant_session(sessions_service.create_tenant("q-rls-flags")) as session:
        enabled, forced = session.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
    assert enabled and forced


def test_another_tenant_sees_no_quarantine_rows():
    owner = sessions_service.create_tenant(f"q-a-{uuid.uuid4().hex[:6]}")
    other = sessions_service.create_tenant(f"q-b-{uuid.uuid4().hex[:6]}")
    view = sessions_service.create_session(owner, name="s", scope_hosts=[], authorized_by="t")
    with tenant_session(owner) as session:
        run = models.Run(tenant_id=owner, session_id=view.id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
    service.admit(
        tenant_id=owner,
        session_id=view.id,
        run_id=run_id,
        host="cdn.example.org",
        files=[service.FileIn("https://cdn.example.org/a.js", b"x=1")],
    )
    with tenant_session(other) as session:
        assert session.scalars(select(models.QuarantinedAsset)).all() == []
        assert session.scalars(select(models.HostDecision)).all() == []
    # And the other tenant cannot decide the owner's host: it simply does not exist for them.
    result = service.decide(
        tenant_id=other, session_id=view.id, host="cdn.example.org", decision="ignore"
    )
    assert result.status == "not_found"
