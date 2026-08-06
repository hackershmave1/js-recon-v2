import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from recon import storage
from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.probe import sources
from recon.runs import assets
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _seed_legacy(tenant, session_id, body):
    """A legacy single-bundle run: bytes at run.input_ref, no run_asset rows."""
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
    input_ref = storage.put_blob(tenant, run_id, "input", body.encode("utf-8"))
    with tenant_session(tenant) as session:
        session.get(models.Run, run_id).input_ref = input_ref
    return run_id


def _seed_crawl(tenant, session_id, url_ok, body_ok, url_pending):
    """A crawl run: url_ok is fetched (blob stored), url_pending stays pending."""
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        assets.seed_pending(session, tenant_id=tenant, run_id=run_id, urls=[url_ok, url_pending])
    key = storage.put_blob(tenant, run_id, "input", body_ok.encode("utf-8"))
    with tenant_session(tenant) as session:
        rows = session.scalars(
            select(models.RunAsset).where(models.RunAsset.run_id == run_id)
        ).all()
        ok_id = next(str(r.id) for r in rows if r.url == url_ok)
        assets.set_fetch_ok(session, ok_id, key)
    return run_id


def test_lists_the_legacy_bundle_as_input_js(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_legacy(tenant, session_id, "const a = 1;\n")
    resp = client.get(f"/runs/{run_id}/sources", headers=_headers(tenant))
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["sources"] == [
        {"path": "input.js", "kind": "upload", "fetch_status": "ok", "asset_url": None}
    ]


def test_serves_the_legacy_bundle_content(client, authorized_session):
    tenant, session_id = authorized_session
    src = "const secret = 'shhh';\nfetch('/api/x');\n"
    run_id = _seed_legacy(tenant, session_id, src)
    resp = client.get(
        f"/runs/{run_id}/sources/content", params={"path": "input.js"}, headers=_headers(tenant)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == src
    assert body["truncated"] is False


def test_unknown_path_is_404(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_legacy(tenant, session_id, "x\n")
    resp = client.get(
        f"/runs/{run_id}/sources/content", params={"path": "nope.js"}, headers=_headers(tenant)
    )
    assert resp.status_code == 404


def test_unknown_run_is_404(client, tenant):
    resp = client.get(
        "/runs/00000000-0000-0000-0000-000000000000/sources", headers=_headers(tenant)
    )
    assert resp.status_code == 404


def test_crawl_lists_assets_with_status(client, authorized_session):
    tenant, session_id = authorized_session
    ok, pending = "https://acme.io/app.js", "https://acme.io/vendor.js"
    run_id = _seed_crawl(tenant, session_id, ok, "console.log(1)\n", pending)
    resp = client.get(f"/runs/{run_id}/sources", headers=_headers(tenant))
    assert resp.status_code == 200
    by_path = {s["path"]: s for s in resp.json()["sources"]}
    assert by_path[ok] == {"path": ok, "kind": "asset", "fetch_status": "ok", "asset_url": None}
    assert by_path[pending]["fetch_status"] == "pending"


def test_crawl_serves_fetched_asset_but_404s_a_pending_one(client, authorized_session):
    tenant, session_id = authorized_session
    ok, pending = "https://acme.io/app.js", "https://acme.io/vendor.js"
    body = "console.log('fetched')\n"
    run_id = _seed_crawl(tenant, session_id, ok, body, pending)
    got = client.get(
        f"/runs/{run_id}/sources/content", params={"path": ok}, headers=_headers(tenant)
    )
    assert got.status_code == 200
    assert got.json()["content"] == body
    # a discovered-but-not-fetched asset has no bytes -> 404, not a 500
    miss = client.get(
        f"/runs/{run_id}/sources/content", params={"path": pending}, headers=_headers(tenant)
    )
    assert miss.status_code == 404


def test_same_url_in_two_runs_serves_each_runs_own_bytes(client, authorized_session):
    """Run-scoping guard (design-review MUST-FIX): run_asset.url is unique only
    per (run_id, url), so a bare url==path match could cross runs. Two runs of one
    tenant sharing an asset URL must each serve their OWN bytes."""
    tenant, session_id = authorized_session
    url, other = "https://cdn.acme.io/app.js", "https://acme.io/other.js"
    run_a = _seed_crawl(tenant, session_id, url, "AAA = 1\n", other)
    run_b = _seed_crawl(tenant, session_id, url, "BBB = 2\n", other)
    got_a = client.get(f"/runs/{run_a}/sources/content", params={"path": url}, headers=_headers(tenant))
    got_b = client.get(f"/runs/{run_b}/sources/content", params={"path": url}, headers=_headers(tenant))
    assert got_a.json()["content"] == "AAA = 1\n"
    assert got_b.json()["content"] == "BBB = 2\n"


def test_another_tenant_cannot_see_a_runs_sources(client, authorized_session):
    """Cross-tenant isolation: RLS hides run A from tenant B -> 404 on both routes."""
    tenant, session_id = authorized_session
    run_id = _seed_legacy(tenant, session_id, "const a = 1;\n")
    intruder = sessions_service.create_tenant(f"intruder-{uuid.uuid4().hex[:8]}")
    listing = client.get(f"/runs/{run_id}/sources", headers=_headers(intruder))
    assert listing.status_code == 404
    content = client.get(
        f"/runs/{run_id}/sources/content", params={"path": "input.js"}, headers=_headers(intruder)
    )
    assert content.status_code == 404


def test_content_is_truncated_past_the_cap(client, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    monkeypatch.setattr(sources, "_MAX_CONTENT_BYTES", 8)
    run_id = _seed_legacy(tenant, session_id, "0123456789abcdef")  # 16 bytes > 8
    resp = client.get(
        f"/runs/{run_id}/sources/content", params={"path": "input.js"}, headers=_headers(tenant)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "01234567"
    assert body["truncated"] is True
