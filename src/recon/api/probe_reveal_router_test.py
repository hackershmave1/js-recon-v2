import pytest
from fastapi.testclient import TestClient

from recon import storage
from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import kingfisher, normalize, store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _seed_revealable(tenant, session_id, token):
    source = f'const k = "{token}";\n'
    value = normalize.normalize_secret_value(token, "stripe")
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
    input_ref = storage.put_blob(tenant, run_id, "input", source.encode("utf-8"))
    offset = kingfisher.byte_offset(source, 1, source.index(token))
    with tenant_session(tenant) as session:
        session.get(models.Run, run_id).input_ref = input_ref
    with tenant_session(tenant) as session:
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.SECRET,
            value=value, path="input.js",
            occurrence=store.Occurrence(
                source_path="input.js", line=1, col=0,
                offset_start=offset, offset_end=offset + len(token.encode("utf-8")),
                engine="kingfisher",
            ),
            attributes={"rule": "stripe", "name": "Stripe"},
        )
        return run_id, result.finding_hash


def test_reveal_route_returns_value(client, authorized_session):
    tenant, session_id = authorized_session
    token = "sk_" + "live_" + "ROUTEVALUE0"
    run_id, finding_hash = _seed_revealable(tenant, session_id, token)
    resp = client.post(
        f"/runs/{run_id}/findings/{finding_hash}/reveal",
        json={"actor": "tester", "reason": "validate"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == token


def test_reveal_route_offsetless_is_422(client, authorized_session):
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done",
                         input_ref="t/r/input/x")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.SECRET,
            value=normalize.normalize_secret_value("sk_live_NOPE", "stripe"), path="input.js",
            occurrence=store.Occurrence(source_path="input.js", line=1, col=0, engine="kingfisher"),
            attributes={"rule": "stripe", "name": "Stripe"},
        )
        finding_hash = result.finding_hash
    resp = client.post(
        f"/runs/{run_id}/findings/{finding_hash}/reveal", json={}, headers=_headers(tenant)
    )
    assert resp.status_code == 422


def test_reveal_route_unknown_run_is_404(client, tenant):
    resp = client.post(
        "/runs/00000000-0000-0000-0000-000000000000/findings/" + "a" * 64 + "/reveal",
        json={}, headers=_headers(tenant),
    )
    assert resp.status_code == 404
