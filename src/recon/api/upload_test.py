"""HTTP multipart upload path for the "one JS file -> findings" slice (REQ-A1, D2).

``POST /runs/upload`` accepts a JS bundle as ``multipart/form-data``, stores it as
the run's input blob, and enqueues the run; ``GET /runs/{run_id}/findings`` then
reads back what the analyze stage produced. Marked integration: exercises the app
against live Postgres + Redis + MinIO.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.config import get_settings
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import RunState
from recon.runs import queries
from recon.worker import main as worker

pytestmark = pytest.mark.integration

_JS = b'const r = await fetch("/api/users/42", {method:"POST", body:JSON.stringify({name:"n"})});\n'

_TERMINAL = {s.value for s in (RunState.DONE, RunState.PARTIAL, RunState.FAILED, RunState.CANCELLED)}


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant: str) -> dict:
    return {"X-Tenant-Id": tenant}


def _drive(redis, tenant: str, run_id: str, *, max_passes: int = 30) -> str:
    for _ in range(max_passes):
        worker.run_once(redis, "upload-test-worker", block_ms=50)
        status = queries.get_status(tenant, run_id)
        if status and status.state in _TERMINAL:
            return status.state
    return queries.get_status(tenant, run_id).state


def test_upload_starts_run_and_findings_are_retrievable(client, redis, authorized_session):
    tenant, session_id = authorized_session
    resp = client.post(
        "/runs/upload",
        files={"file": ("bundle.js", _JS, "application/javascript")},
        data={"session_id": session_id, "target": "acme.io"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    assert resp.json()["state"] == "queued"

    assert _drive(redis, tenant, run_id) == RunState.DONE.value

    findings = client.get(f"/runs/{run_id}/findings", headers=_headers(tenant))
    assert findings.status_code == 200
    body = findings.json()
    assert body["run_id"] == run_id
    # The POST endpoint plus its body param, at least — and count matches the list.
    assert body["count"] == len(body["findings"]) >= 2

    endpoint = next(f for f in body["findings"] if f["value"] == "POST /api/users/{id}")
    assert endpoint["type"] == "endpoint"
    # The finding carries its occurrence(s) so a normalization merge is visible (REQ-C2).
    assert endpoint["occurrences"][0]["raw_url"] == "/api/users/42"


def test_upload_missing_tenant_header_is_401(client, authorized_session):
    _tenant, session_id = authorized_session
    resp = client.post(
        "/runs/upload",
        files={"file": ("bundle.js", _JS, "application/javascript")},
        data={"session_id": session_id},
    )
    assert resp.status_code == 401


def test_upload_unknown_session_is_404(client, tenant):
    resp = client.post(
        "/runs/upload",
        files={"file": ("bundle.js", _JS, "application/javascript")},
        data={"session_id": "00000000-0000-0000-0000-000000000000"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 404


def test_upload_empty_file_is_400(client, authorized_session):
    tenant, session_id = authorized_session
    resp = client.post(
        "/runs/upload",
        files={"file": ("bundle.js", b"", "application/javascript")},
        data={"session_id": session_id},
        headers=_headers(tenant),
    )
    assert resp.status_code == 400


def test_upload_oversize_file_is_413(client, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    # Shrink the cap so the test stays small; the endpoint reads it per request.
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 16)
    resp = client.post(
        "/runs/upload",
        files={"file": ("big.js", b"x" * 64, "application/javascript")},
        data={"session_id": session_id},
        headers=_headers(tenant),
    )
    assert resp.status_code == 413


def test_upload_unauthorized_session_is_403(client, tenant):
    # A session that exists but carries no authorization ack must be refused a run.
    with tenant_session(tenant) as session:
        row = models.EngagementSession(
            tenant_id=tenant, name="unauth", scope_hosts=["acme.io"], authorization_ack=False
        )
        session.add(row)
        session.flush()
        session_id = str(row.id)
    resp = client.post(
        "/runs/upload",
        files={"file": ("bundle.js", _JS, "application/javascript")},
        data={"session_id": session_id},
        headers=_headers(tenant),
    )
    assert resp.status_code == 403


def test_get_findings_missing_tenant_header_is_401(client):
    resp = client.get("/runs/00000000-0000-0000-0000-000000000000/findings")
    assert resp.status_code == 401


def test_get_findings_unknown_run_is_404(client, tenant):
    resp = client.get(
        "/runs/00000000-0000-0000-0000-000000000000/findings", headers=_headers(tenant)
    )
    assert resp.status_code == 404


def test_upload_with_source_map_stores_and_references_it(client, authorized_session):
    # The optional .map field is stored as a blob and referenced on the run so the
    # analyze stage can recover real source paths (exercises migration 0003).
    tenant, session_id = authorized_session
    smap = b'{"version":3,"sources":["app/api.js"],"sourcesContent":["fetch(1)"],"mappings":""}'
    resp = client.post(
        "/runs/upload",
        files={
            "file": ("bundle.js", _JS, "application/javascript"),
            "map": ("bundle.js.map", smap, "application/json"),
        },
        data={"session_id": session_id},
        headers=_headers(tenant),
    )
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    with tenant_session(tenant) as session:
        run = session.get(models.Run, run_id)
        assert run.source_map_ref is not None
