import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def test_finding_triage_is_tenant_isolated_by_rls():
    tenant_a = sessions_service.create_tenant("triage-a")
    tenant_b = sessions_service.create_tenant("triage-b")
    session_view = sessions_service.create_session(
        tenant_a, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    with tenant_session(tenant_a) as session:
        session.add(models.FindingTriage(
            tenant_id=tenant_a, session_id=session_view.id,
            finding_hash="h" * 64, status="confirmed",
        ))
    with tenant_session(tenant_a) as session:
        assert session.query(models.FindingTriage).count() == 1
    with tenant_session(tenant_b) as session:
        assert session.query(models.FindingTriage).count() == 0
