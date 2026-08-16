import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _run_with_tech(tenant, session_id) -> str:
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        session.add(
            models.RunTechnology(
                tenant_id=tenant,
                run_id=run_id,
                host="acme.io",
                name="Nginx",
                categories=["Web servers"],
                version="1.25.3",
                confidence=100,
                evidence=["server: nginx/1.25.3"],
            )
        )
        return run_id


def test_get_technologies_groups_by_host(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_tech(tenant, session_id)
    resp = client.get(f"/runs/{run_id}/technologies", headers=_headers(tenant))
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id and body["count"] == 1
    tech = body["hosts"]["acme.io"][0]
    assert tech["name"] == "Nginx" and tech["version"] == "1.25.3"
    assert tech["categories"] == ["Web servers"] and tech["confidence"] == 100


def test_unknown_run_is_404(client, tenant):
    resp = client.get(
        "/runs/00000000-0000-0000-0000-000000000000/technologies", headers=_headers(tenant)
    )
    assert resp.status_code == 404


def test_other_tenant_sees_none(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_tech(tenant, session_id)
    other = sessions_service.create_tenant("tech-other")
    resp = client.get(f"/runs/{run_id}/technologies", headers=_headers(other))
    assert resp.status_code == 404  # RLS -> run invisible -> None -> 404
