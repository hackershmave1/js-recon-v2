"""HTTP-level smoke of the API surface (REQ-A1 enqueue, REQ-R4 ETag, REQ-R2 SSE).

Marked integration: exercises the app against live Postgres + Redis.
"""

from __future__ import annotations

import statistics
import time

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.worker import main as worker

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant: str) -> dict:
    return {"X-Tenant-Id": tenant}


def test_enqueue_returns_run_id_under_200ms_median(client, authorized_session):
    tenant, session_id = authorized_session
    # Warm up connection pools so the measurement reflects steady state.
    client.post("/runs", json={"session_id": session_id}, headers=_headers(tenant))

    samples = []
    for _ in range(15):
        start = time.perf_counter()
        resp = client.post("/runs", json={"session_id": session_id}, headers=_headers(tenant))
        samples.append(time.perf_counter() - start)
        assert resp.status_code == 202
        assert resp.json()["run_id"]
        assert resp.json()["state"] == "queued"

    median = statistics.median(samples)
    assert median < 0.2, f"enqueue median {median * 1000:.1f}ms exceeds 200ms (REQ-A1)"


def test_missing_tenant_header_is_401(client, authorized_session):
    _tenant, session_id = authorized_session
    resp = client.post("/runs", json={"session_id": session_id})
    assert resp.status_code == 401


def test_unknown_session_is_404(client, tenant):
    resp = client.post(
        "/runs",
        json={"session_id": "00000000-0000-0000-0000-000000000000"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 404


def test_session_creation_requires_scope(client, tenant):
    resp = client.post(
        "/sessions",
        json={"scope_hosts": [], "authorized_by": "tester"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 400


def test_status_etag_returns_304_when_unchanged(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = client.post(
        "/runs", json={"session_id": session_id}, headers=_headers(tenant)
    ).json()["run_id"]

    first = client.get(f"/runs/{run_id}/status", headers=_headers(tenant))
    assert first.status_code == 200
    etag = first.headers["ETag"]

    second = client.get(
        f"/runs/{run_id}/status",
        headers={**_headers(tenant), "If-None-Match": etag},
    )
    assert second.status_code == 304


def test_sse_replays_events_including_terminal(client, authorized_session, redis):
    tenant, session_id = authorized_session
    run_id = client.post(
        "/runs", json={"session_id": session_id}, headers=_headers(tenant)
    ).json()["run_id"]

    for _ in range(30):
        worker.run_once(redis, "sse-test-worker", block_ms=50)
        status = client.get(f"/runs/{run_id}/status", headers=_headers(tenant)).json()
        if status["state"] == "done":
            break
    assert status["state"] == "done"

    # A finished run's stream replays from the start and closes at the terminal event.
    resp = client.get(f"/runs/{run_id}/events", headers=_headers(tenant))
    assert resp.status_code == 200
    body = resp.text
    assert "event: run.created" in body
    assert '"to":"done"' in body
