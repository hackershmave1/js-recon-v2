"""The search route's contract (host lane; the engine is faked — source_search_test covers it)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from recon.api import sources_router
from recon.api.app import create_app
from recon.probe.source_search import SearchOutcome, SourceMatch


def test_reports_honest_totals_and_scopes_cancellation_to_the_client(monkeypatch):
    seen: dict = {}

    def fake(tenant_id, run_id, q, *, client=""):
        seen.update(run_id=run_id, q=q, client=client)
        m = SourceMatch(
            path="a.js", kind="asset", asset_url=None, line=None, col=7, snippet="x", exact=False
        )
        return SearchOutcome(
            matches=[m], total=9, files_total=4, files_scanned=3, unindexed=2, partial=True
        )

    monkeypatch.setattr(sources_router.source_search, "search", fake)
    r = TestClient(create_app()).get(
        "/runs/r1/sources/search",
        params={"q": "token", "client": "page-1"},
        headers={"X-Tenant-Id": str(uuid.uuid4())},
    )
    assert r.status_code == 200
    body = r.json()
    assert {
        k: body[k]
        for k in ("count", "total", "files_scanned", "files_total", "unindexed", "partial")
    } == {
        "count": 1,
        "total": 9,
        "files_scanned": 3,
        "files_total": 4,
        "unindexed": 2,
        "partial": True,
    }
    assert body["matches"][0] | {} == {
        "path": "a.js",
        "kind": "asset",
        "asset_url": None,
        "line": None,
        "col": 7,
        "snippet": "x",
        "exact": False,
    }
    assert seen == {"run_id": "r1", "q": "token", "client": "page-1"}


def test_absent_run_is_404(monkeypatch):
    monkeypatch.setattr(sources_router.source_search, "search", lambda *a, **k: None)
    r = TestClient(create_app()).get(
        "/runs/r1/sources/search", params={"q": "token"}, headers={"X-Tenant-Id": str(uuid.uuid4())}
    )
    assert r.status_code == 404
