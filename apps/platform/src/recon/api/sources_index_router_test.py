"""GET /runs/{id}/sources/index-status: the Sources page's cheap poll. Opening it starts
background indexing of an older run (at most one job per run); it never downloads a source."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from recon.api import sources_router
from recon.api.app import create_app
from recon.api.deps import get_redis

TENANT = {"X-Tenant-Id": str(uuid.uuid4())}


def _client(monkeypatch, *, exists=True):
    calls: list[str] = []
    monkeypatch.setattr(
        sources_router.sources, "list_sources", lambda _t, _r: [] if exists else None
    )
    monkeypatch.setattr(
        sources_router.source_index, "request_index", lambda *_a: calls.append("request") or True
    )
    monkeypatch.setattr(
        sources_router.source_index,
        "status",
        lambda *_a: {"indexed": 120, "total": 211, "indexing": True},
    )
    app = create_app()
    app.dependency_overrides[get_redis] = lambda: object()
    return TestClient(app), calls


def test_reports_progress_and_starts_indexing(monkeypatch):
    client, calls = _client(monkeypatch)
    r = client.get("/runs/r1/sources/index-status", headers=TENANT)
    assert r.status_code == 200
    assert r.json() == {"indexed": 120, "total": 211, "indexing": True}
    assert calls == ["request"]


def test_absent_run_is_404_and_starts_nothing(monkeypatch):
    client, calls = _client(monkeypatch, exists=False)
    assert client.get("/runs/r1/sources/index-status", headers=TENANT).status_code == 404
    assert calls == []
