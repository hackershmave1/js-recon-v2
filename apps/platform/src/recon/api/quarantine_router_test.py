"""The quarantine endpoints (ADR-0010) with the service stubbed: request shape, the
open-capture gate, status codes. The behaviour itself is covered in recon.quarantine."""

from __future__ import annotations

from types import SimpleNamespace

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


def test_add_of_a_referenced_host_starts_one_recrawl(client, monkeypatch):
    started = []
    monkeypatch.setattr(
        service,
        "decide",
        lambda **k: service.DecisionResult(status="ok", state="added", recrawl_run_id="run-1"),
    )
    monkeypatch.setattr(
        quarantine_router.coordinator,
        "edit_and_rerun",
        lambda redis, **k: started.append(k["run_id"]) or SimpleNamespace(id="run-2"),
    )
    body = client.post(
        "/sessions/s-1/hosts/cdn.acme-static.com/decision", json={"decision": "add"}, headers=H
    ).json()
    assert started == ["run-1"]
    assert body["recrawl"] == {"source_run_id": "run-1", "run_id": "run-2"}


def test_ignore_and_plain_add_start_no_recrawl(client, monkeypatch):
    monkeypatch.setattr(
        quarantine_router.coordinator,
        "edit_and_rerun",
        lambda *a, **k: pytest.fail("no re-crawl expected"),
    )
    monkeypatch.setattr(
        service, "decide", lambda **k: service.DecisionResult(status="ok", state="ignored")
    )
    url = "/sessions/s-1/hosts/cdn.acme-static.com/decision"
    assert client.post(url, json={"decision": "ignore"}, headers=H).json()["recrawl"] is None
