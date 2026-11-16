"""The quarantine endpoints (ADR-0010) with the service stubbed: request shape, the
open-capture gate, status codes. The behaviour itself is covered in recon.quarantine."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from recon.api import quarantine_router
from recon.api.app import create_app
from recon.quarantine import service


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(
        quarantine_router.capture_router, "_resolve_ingest_tenant", lambda auth: ("tenant-1", True)
    )
    monkeypatch.setattr(
        quarantine_router.capture_router, "_enforce_origin_lock", lambda *a, **k: None
    )
    return TestClient(create_app())


H = {"X-Tenant-Id": "123e4567-e89b-12d3-a456-426614174000"}


def _body(**over):
    body = {
        "sessionId": "ext-1",
        "runId": "run-1",
        "host": "cdn.acme-static.com",
        "files": [{"url": "https://cdn.acme-static.com/a.js", "content": "x=1"}],
    }
    body.update(over)
    return body


def test_ingest_for_an_ended_capture_is_a_409_and_stores_nothing(client, monkeypatch):
    monkeypatch.setattr(quarantine_router.capture_lifecycle, "find_open_run", lambda t, s: None)
    monkeypatch.setattr(service, "admit", lambda **k: pytest.fail("must not store"))
    response = client.post("/api/quarantine/files", json=_body())
    assert response.status_code == 409 and response.json()["detail"] == "capture_ended"


def test_ingest_for_another_runs_id_is_a_409(client, monkeypatch):
    monkeypatch.setattr(quarantine_router.capture_lifecycle, "find_open_run", lambda t, s: "other")
    assert client.post("/api/quarantine/files", json=_body()).status_code == 409


def test_ingest_batch_size_is_bounded(client):
    files = [{"url": f"https://h/{i}.js", "content": "x"} for i in range(21)]
    assert client.post("/api/quarantine/files", json=_body(files=files)).status_code == 413


def test_decision_status_codes(client, monkeypatch):
    def respond(status, state=None):
        monkeypatch.setattr(
            service,
            "decide",
            lambda **k: service.DecisionResult(status=status, state=state),
        )

    url = "/sessions/s-1/hosts/cdn.acme-static.com/decision"
    respond("ok", "added")
    ok = client.post(url, json={"decision": "add"}, headers=H)
    assert ok.status_code == 200 and ok.json()["state"] == "added"
    respond("conflict", "ignored")
    conflict = client.post(url, json={"decision": "add"}, headers=H)
    assert conflict.status_code == 409 and conflict.json()["detail"]["state"] == "ignored"
    respond("not_found")
    assert client.post(url, json={"decision": "add"}, headers=H).status_code == 404
    assert client.post(url, json={"decision": "maybe"}, headers=H).status_code == 422
