"""SPA serving: assets + Accept-based client-route fallback, no-op when absent."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.config import get_settings


def _client_with_dist(tmp_path: Path, monkeypatch) -> TestClient:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("RECON_SPA_DIST_DIR", str(dist))
    get_settings.cache_clear()
    return TestClient(create_app())


def test_browser_navigation_to_client_route_gets_index_html(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    # Deep link that shares the /runs API prefix must still serve the SPA shell.
    r = client.get("/runs/2b1c", headers={"accept": "text/html"})
    assert r.status_code == 200
    assert "<div id=root>" in r.text


def test_unknown_api_path_stays_json_404(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    r = client.get("/runs/2b1c/bogus", headers={"accept": "application/json"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_assets_are_served(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    assert client.get("/assets/app.js").status_code == 200


def test_no_dist_is_noop(monkeypatch):
    monkeypatch.setenv("RECON_SPA_DIST_DIR", "/nonexistent/dist")
    get_settings.cache_clear()
    client = TestClient(create_app())
    # Catch-all not registered → default Starlette 404 for an unknown path.
    assert client.get("/", headers={"accept": "text/html"}).status_code == 404


def test_existing_api_route_still_wins(monkeypatch):
    monkeypatch.setenv("RECON_SPA_DIST_DIR", "/nonexistent/dist")
    get_settings.cache_clear()
    client = TestClient(create_app())
    assert client.get("/healthz").status_code == 200


def test_partial_dist_missing_assets_is_noop(tmp_path, monkeypatch):
    # index.html exists but assets/ does not — StaticFiles(check_dir=True) would
    # otherwise raise at app startup; the mount must be skipped entirely instead.
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    monkeypatch.setenv("RECON_SPA_DIST_DIR", str(dist))
    get_settings.cache_clear()
    client = TestClient(create_app())
    # Catch-all not registered → default Starlette 404 for an unknown path.
    assert client.get("/", headers={"accept": "text/html"}).status_code == 404


def test_relative_setting_is_resolved(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECON_SPA_DIST_DIR", "dist")
    get_settings.cache_clear()
    client = TestClient(create_app())
    r = client.get("/runs/x", headers={"accept": "text/html"})
    assert r.status_code == 200
    assert "<div id=root>" in r.text
