"""Hermetic (fast-lane, no live infra) contract tests for the extension -> platform
capture INGEST wire shape (DEBT D8a).

These pin the response envelopes the shipped Chrome extension depends on — the
health handshake + its ``contractVersion``, the save-files envelope, analyze/start,
progress, and the ``GET /api/projects`` BARE-ARRAY invariant — so a future refactor
that drifts the wire shape fails in the FAST lane (Hyrum's law), not silently in
production. The router's DB/Redis/S3 seams are patched out; live-infra behavior is
covered separately in ``capture_router_test.py`` (integration).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from recon.api import capture_router
from recon.api.app import create_app
from recon.config import get_settings

TENANT = "11111111-1111-1111-1111-111111111111"


@pytest.fixture()
def capture_client(monkeypatch):
    """A TestClient with capture ingest flag-mounted, built WITHOUT touching any
    store (``create_app`` opens no eager connections). ``monkeypatch`` auto-restores
    the env; ``cache_clear`` before + after keeps the flag-on settings from leaking
    into other fast-lane tests via the ``get_settings`` lru_cache."""
    monkeypatch.setenv("RECON_ENABLE_CAPTURE_INGEST", "true")
    get_settings.cache_clear()
    client = TestClient(create_app())
    yield client
    get_settings.cache_clear()


def test_health_reports_contract_version(capture_client):
    res = capture_client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {
        "status": "ok",
        "mode": "platform",
        "contractVersion": capture_router.CAPTURE_CONTRACT_VERSION,
    }
    assert isinstance(capture_router.CAPTURE_CONTRACT_VERSION, str)


def test_save_files_response_envelope(capture_client):
    with (
        patch.object(capture_router, "_get_or_create_tenant", return_value=TENANT),
        patch.object(capture_router, "get_redis", return_value=object()),
        patch.object(capture_router, "_get_or_create_session", return_value="sess-1"),
        patch.object(capture_router, "_accumulating_run_id", return_value="run-1"),
        patch.object(capture_router.storage, "put_blob", return_value="blob-key"),
        patch.object(capture_router, "_seed_fetched_assets", return_value=1),
        patch.object(
            capture_router, "emit", return_value=None
        ),  # slice-2 side-channel: keep hermetic
    ):
        res = capture_client.post(
            "/api/save-files",
            json={
                "metadata": {"sessionId": "ext-1", "version": "3.0.0"},
                "files": [{"url": "https://acme.io/a.js", "content": "x=1", "contentHash": "h1"}],
            },
        )
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"success", "paired", "sessionId", "runId", "stored", "failed", "files"}
    assert body["success"] is True
    assert body["paired"] is False  # additive pairing field; no Bearer sent -> unpaired
    assert body["sessionId"] == "sess-1"
    assert body["runId"] == "run-1"
    assert body["stored"] == 1
    assert body["failed"] == 0
    assert isinstance(body["files"], list) and len(body["files"]) == 1
    file_result = body["files"][0]
    assert {"url", "contentHash", "runId", "stored"} <= set(file_result)
    assert file_result["stored"] is True


def test_save_files_survives_emit_failure(capture_client):
    # The slice-2 capture.received event is a BEST-EFFORT side-channel: if emit raises
    # (event bus down, or a malformed url in host parsing), the already-durable batch
    # must still ack 200 — never a 5xx the extension would retry forever, nor a 4xx that
    # drops un-recapturable JS.
    with (
        patch.object(capture_router, "_get_or_create_tenant", return_value=TENANT),
        patch.object(capture_router, "get_redis", return_value=object()),
        patch.object(capture_router, "_get_or_create_session", return_value="sess-1"),
        patch.object(capture_router, "_accumulating_run_id", return_value="run-1"),
        patch.object(capture_router.storage, "put_blob", return_value="blob-key"),
        patch.object(capture_router, "_seed_fetched_assets", return_value=1),
        patch.object(capture_router, "emit", side_effect=RuntimeError("event bus down")),
    ):
        res = capture_client.post(
            "/api/save-files",
            json={
                "metadata": {"sessionId": "ext-1"},
                "files": [{"url": "https://acme.io/a.js", "content": "x=1", "contentHash": "h1"}],
            },
        )
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True and body["stored"] == 1


def test_save_files_without_session_returns_empty_envelope(capture_client):
    with (
        patch.object(capture_router, "_get_or_create_tenant", return_value=TENANT),
        patch.object(capture_router, "get_redis", return_value=object()),
    ):
        res = capture_client.post("/api/save-files", json={"metadata": {}, "files": []})
    assert res.status_code == 200
    assert res.json() == {
        "success": True,
        "paired": False,
        "sessionId": None,
        "runId": None,
        "stored": 0,
        "failed": 0,
        "files": [],
    }


def test_analyze_start_unknown_session_is_404(capture_client):
    with (
        patch.object(capture_router, "_get_or_create_tenant", return_value=TENANT),
        patch.object(capture_router, "get_redis", return_value=object()),
        patch.object(capture_router, "_find_session_by_external_id", return_value=None),
    ):
        res = capture_client.post("/api/sessions/nope/analyze/start")
    assert res.status_code == 404


def test_analyze_start_idempotent_envelope(capture_client):
    # The extension hard-depends only on ``started`` being a bool (workspace-client.js);
    # pin that invariant rather than over-asserting this endpoint's polymorphic keys.
    row = SimpleNamespace(url="https://acme.io/a.js")
    with (
        patch.object(capture_router, "_get_or_create_tenant", return_value=TENANT),
        patch.object(capture_router, "get_redis", return_value=object()),
        patch.object(capture_router, "_find_session_by_external_id", return_value="sess-1"),
        patch.object(capture_router, "_latest_run_id", return_value="run-1"),
        patch.object(capture_router.assets, "list_for_run", return_value=[row]),
        patch.object(capture_router, "_run_has_job", return_value=True),
    ):
        res = capture_client.post("/api/sessions/ext-1/analyze/start")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["started"], bool) and body["started"] is True
    assert body["runId"] == "run-1"
    assert "message" in body


def test_progress_idle_envelope(capture_client):
    with (
        patch.object(capture_router, "_get_or_create_tenant", return_value=TENANT),
        patch.object(capture_router, "_find_session_by_external_id", return_value="sess-1"),
        patch.object(capture_router, "_latest_analyzed_run", return_value=None),
    ):
        res = capture_client.get("/api/sessions/ext-1/analyze/progress")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["sessionId"] == "sess-1"
    assert set(body["job"]) == {"counts", "files"}
    assert set(body["job"]["counts"]) == {
        "queued",
        "analyzing",
        "completed",
        "failed",
        "cancelled",
        "total",
    }
    assert body["job"]["files"] == []


def test_projects_is_a_bare_array(capture_client):
    # Wire invariant: GET /projects MUST be a bare array (the extension does
    # ``Array.isArray(body) ? body : []``). A dict-wrapped body would silently break it.
    view = SimpleNamespace(
        id="e-1",
        name="Acme",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        in_scope_domains=["acme.io"],
    )
    with (
        patch.object(capture_router, "_get_or_create_tenant", return_value=TENANT),
        patch.object(capture_router.engagements_service, "list_engagements", return_value=[view]),
    ):
        res = capture_client.get("/api/projects")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["id"] == "e-1"
    assert set(body[0]["defaults"]) == {"scope", "capture", "denylist", "analysis"}
    assert body[0]["defaults"]["scope"]["rootDomains"] == ["acme.io"]
