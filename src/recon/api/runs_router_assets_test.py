"""GET /runs/{id}/assets: 404 unknown run, pending placeholder, manifest
passthrough, per-asset status."""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from recon import storage
from recon.api.app import create_app
from recon.db.base import tenant_session
from recon.events.log import record_event
from recon.runs import assets, service as runs_service

client = TestClient(create_app())
TENANT = "11111111-1111-1111-1111-111111111111"


def test_assets_returns_manifest_when_present():
    manifest = {
        "domain": "acme.io",
        "status": "ok",
        "assets": [
            {
                "url": "https://acme.io/a.js",
                "source": "katana",
                "fetch_status": "ok",
                "analyze_status": "ok",
            }
        ],
    }
    with patch("recon.api.runs_router.queries.get_status", return_value=object()), \
         patch(
             "recon.api.runs_router.discover_queries.get_assets_with_status",
             return_value=manifest,
         ):
        res = client.get("/runs/r-1/assets", headers={"X-Tenant-Id": TENANT})
    assert res.status_code == 200
    assert res.json() == manifest


def test_assets_pending_before_discovery():
    with patch("recon.api.runs_router.queries.get_status", return_value=object()), \
         patch(
             "recon.api.runs_router.discover_queries.get_assets_with_status",
             return_value=None,
         ):
        res = client.get("/runs/r-1/assets", headers={"X-Tenant-Id": TENANT})
    assert res.status_code == 200
    assert res.json() == {"domain": None, "status": "pending", "assets": []}


def test_assets_unknown_run_is_404():
    with patch("recon.api.runs_router.queries.get_status", return_value=None):
        res = client.get("/runs/r-1/assets", headers={"X-Tenant-Id": TENANT})
    assert res.status_code == 404


@pytest.mark.integration
def test_assets_includes_per_asset_status(authorized_session, redis):
    """Assets endpoint returns each asset with fetch_status and
    analyze_status merged from run_assets table. Manifest URLs without
    corresponding run_asset rows default to "pending" on both dimensions."""
    tenant_id, session_id = authorized_session
    seeded_urls = [
        "https://acme.io/a.js",
        "https://acme.io/b.js",
        "https://acme.io/c.js",
    ]
    unseeded_url = "https://acme.io/unseen.js"
    all_urls = seeded_urls + [unseeded_url]
    run = runs_service.create_run(
        redis, tenant_id=tenant_id, session_id=session_id, target="acme.io"
    )
    # Seed only 3 assets; the 4th (unseeded_url) will have no run_asset row
    with tenant_session(tenant_id) as s:
        assets.seed_pending(
            s, tenant_id=tenant_id, run_id=run.id, urls=seeded_urls
        )
    manifest = {
        "domain": "acme.io",
        "status": "ok",
        "assets": [{"url": u, "source": "katana"} for u in all_urls],
    }
    manifest_ref = storage.put_blob(
        tenant_id, run.id, "assets", json.dumps(manifest).encode()
    )
    with tenant_session(tenant_id) as s:
        record_event(
            s,
            tenant_id=tenant_id,
            run_id=run.id,
            event_type="discover.assets",
            payload={"count": len(all_urls), "assets_ref": manifest_ref,
                     "status": "ok"},
        )
    # Set mixed statuses: ok, failed, pending (unchanged)
    asset_list = assets.list_for_run(tenant_id, run.id)
    with tenant_session(tenant_id) as s:
        assets.set_fetch_ok(s, asset_list[0].id, "input-ref-0")
        assets.set_fetch_ok(s, asset_list[1].id, "input-ref-1")
        assets.set_fetch_failed(s, asset_list[2].id, "network error")
        assets.set_analyze_ok(s, asset_list[0].id)
        # asset_list[1] left at pending analyze
        # asset_list[2] left at pending analyze (due to fetch failure)
    # GET /runs/{id}/assets
    res = client.get(
        f"/runs/{run.id}/assets", headers={"X-Tenant-Id": tenant_id}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["domain"] == "acme.io"
    assert body["status"] == "ok"
    assert len(body["assets"]) == 4
    # Verify each asset includes fetch_status and analyze_status
    assert body["assets"][0]["url"] == seeded_urls[0]
    assert body["assets"][0]["fetch_status"] == "ok"
    assert body["assets"][0]["analyze_status"] == "ok"
    assert body["assets"][1]["url"] == seeded_urls[1]
    assert body["assets"][1]["fetch_status"] == "ok"
    assert body["assets"][1]["analyze_status"] == "pending"
    assert body["assets"][2]["url"] == seeded_urls[2]
    assert body["assets"][2]["fetch_status"] == "failed"
    assert body["assets"][2]["analyze_status"] == "pending"
    # Verify unseeded asset defaults to pending on both dimensions
    assert body["assets"][3]["url"] == unseeded_url
    assert body["assets"][3]["fetch_status"] == "pending"
    assert body["assets"][3]["analyze_status"] == "pending"
