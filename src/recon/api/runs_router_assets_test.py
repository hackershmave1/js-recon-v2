"""GET /runs/{id}/assets: 404 unknown run, pending placeholder, manifest passthrough."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from recon.api.app import create_app

client = TestClient(create_app())
TENANT = "11111111-1111-1111-1111-111111111111"


def test_assets_returns_manifest_when_present():
    manifest = {"domain": "acme.io", "status": "ok",
                "assets": [{"url": "https://acme.io/a.js", "source": "katana"}]}
    with patch("recon.api.runs_router.queries.get_status", return_value=object()), \
         patch("recon.api.runs_router.discover_queries.get_assets_manifest", return_value=manifest):
        res = client.get("/runs/r-1/assets", headers={"X-Tenant-Id": TENANT})
    assert res.status_code == 200
    assert res.json() == manifest


def test_assets_pending_before_discovery():
    with patch("recon.api.runs_router.queries.get_status", return_value=object()), \
         patch("recon.api.runs_router.discover_queries.get_assets_manifest", return_value=None):
        res = client.get("/runs/r-1/assets", headers={"X-Tenant-Id": TENANT})
    assert res.status_code == 200
    assert res.json() == {"domain": None, "status": "pending", "assets": []}


def test_assets_unknown_run_is_404():
    with patch("recon.api.runs_router.queries.get_status", return_value=None):
        res = client.get("/runs/r-1/assets", headers={"X-Tenant-Id": TENANT})
    assert res.status_code == 404
