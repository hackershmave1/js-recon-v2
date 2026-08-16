import pytest
from sqlalchemy.exc import IntegrityError

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _make_run(tenant: str, session_id: str) -> str:
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id)
        session.add(run)
        session.flush()
        return str(run.id)


def test_fingerprint_signal_is_a_known_blob_kind():
    assert "fingerprint-signal" in storage.BLOB_KINDS


def test_run_technology_is_tenant_isolated_by_rls():
    tenant_a = sessions_service.create_tenant("tech-a")
    tenant_b = sessions_service.create_tenant("tech-b")
    sv = sessions_service.create_session(
        tenant_a, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _make_run(tenant_a, sv.id)
    with tenant_session(tenant_a) as session:
        session.add(
            models.RunTechnology(
                tenant_id=tenant_a,
                run_id=run_id,
                host="acme.io",
                name="nginx",
                categories=["Web servers"],
                version="1.25.3",
                confidence=100,
                evidence=["server: nginx/1.25.3"],
            )
        )
    with tenant_session(tenant_a) as session:
        row = session.query(models.RunTechnology).one()
        assert row.name == "nginx" and row.version == "1.25.3" and row.confidence == 100
    with tenant_session(tenant_b) as session:
        assert session.query(models.RunTechnology).count() == 0


def test_run_technology_unique_on_run_host_name():
    tenant = sessions_service.create_tenant("tech-uq")
    sv = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _make_run(tenant, sv.id)
    with tenant_session(tenant) as session:
        for _ in range(2):
            session.add(
                models.RunTechnology(
                    tenant_id=tenant,
                    run_id=run_id,
                    host="acme.io",
                    name="nginx",
                    categories=[],
                    version=None,
                    confidence=50,
                    evidence=[],
                )
            )
        with pytest.raises(IntegrityError):  # (run_id, host, name) unique violation
            session.flush()
