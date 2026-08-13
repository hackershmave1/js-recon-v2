# src/recon/api/export_router_test.py
import json

import pytest
import yaml
from fastapi.testclient import TestClient
from openapi_spec_validator import validate

from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _seed(tenant, session_id):
    """A run with one endpoint finding: GET /location/address/search on acme.io."""
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        store.record_finding(
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.ENDPOINT,
            value="GET /location/address/search",
            path="input.js",
            occurrence=store.Occurrence(
                host="acme.io", raw_url="https://acme.io/location/address/search"
            ),
            attributes={"method": "GET", "kind": "fetch"},
            first_stage="analyzing",
        )
        return run_id


def _seed_graphql(tenant, run_id, entries):
    """Persist a run-level GraphQL export artifact the way analyze does (enrichment C):
    a content-addressed `graphql` blob + the `analyze.graphql` event that indexes it."""
    from recon import storage

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


def test_export_carries_graphql_operations(client, authorized_session):
    # Enrichment C: the OpenAPI export surfaces the run's GraphQL operations as a root
    # x-recon-graphql-operations annotation, alongside (never inside) the HTTP paths.
    tenant, session_id = authorized_session
    run_id = _seed(tenant, session_id)
    ops = [
        {
            "op_type": "query",
            "name": "Me",
            "fields": ["me"],
            "source_path": "https://acme.io/app.js",
        }
    ]
    _seed_graphql(tenant, run_id, ops)

    resp = client.get(f"/runs/{run_id}/export/openapi", headers=_headers(tenant))

    assert resp.status_code == 200
    doc = json.loads(resp.content)
    validate(doc)
    assert doc["x-recon-graphql-operations"] == ops
    assert "/location/address/search" in doc["paths"]  # HTTP endpoints unaffected


def test_export_openapi_json(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed(tenant, session_id)

    resp = client.get(f"/runs/{run_id}/export/openapi", headers=_headers(tenant))

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers["content-disposition"] == f'attachment; filename="openapi-{run_id}.json"'
    doc = json.loads(resp.content)
    validate(doc)
    assert "/location/address/search" in doc["paths"]
    assert doc["servers"][0]["url"] == "https://acme.io"


def test_export_openapi_yaml(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed(tenant, session_id)

    resp = client.get(f"/runs/{run_id}/export/openapi?format=yaml", headers=_headers(tenant))

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/yaml")
    assert resp.headers["content-disposition"] == f'attachment; filename="openapi-{run_id}.yaml"'
    validate(yaml.safe_load(resp.content))


def test_export_bad_format_is_422(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed(tenant, session_id)
    resp = client.get(f"/runs/{run_id}/export/openapi?format=xml", headers=_headers(tenant))
    assert resp.status_code == 422


def test_export_unknown_run_is_404(client, tenant):
    resp = client.get(
        "/runs/00000000-0000-0000-0000-000000000000/export/openapi", headers=_headers(tenant)
    )
    assert resp.status_code == 404


def test_export_other_tenant_run_is_404(client, authorized_session):
    owner_tenant, session_id = authorized_session
    run_id = _seed(owner_tenant, session_id)
    other_tenant = sessions_service.create_tenant("export-router-other")
    resp = client.get(f"/runs/{run_id}/export/openapi", headers=_headers(other_tenant))
    assert resp.status_code == 404


def test_export_build_failure_is_500(client, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    run_id = _seed(tenant, session_id)

    def _boom(*args, **kwargs):
        raise RuntimeError("forced build failure")

    monkeypatch.setattr("recon.probe.openapi.build_openapi", _boom)
    resp = client.get(f"/runs/{run_id}/export/openapi", headers=_headers(tenant))
    assert resp.status_code == 500
