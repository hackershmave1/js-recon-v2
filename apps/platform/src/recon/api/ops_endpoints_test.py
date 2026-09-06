"""Tests for /healthz and /metrics ops endpoints (D53)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


class TestHealthz:
    def test_healthz_ok_shape(self, client: TestClient):
        with (
            patch("recon.api.app._check_redis", return_value=True),
            patch("recon.api.app._check_postgres", return_value=True),
            patch("recon.api.app._check_s3", return_value=True),
            patch("recon.queue.streams.pending_count", return_value=0),
            patch("recon.api.app.get_redis", return_value=MagicMock(xlen=lambda _k: 0)),
        ):
            resp = client.get("/healthz", headers={"X-Tenant-Id": "t1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["redis"] is True
        assert body["checks"]["postgres"] is True
        assert body["checks"]["s3"] is True
        assert "queues" in body

    def test_healthz_degraded_when_s3_down(self, client: TestClient):
        with (
            patch("recon.api.app._check_redis", return_value=True),
            patch("recon.api.app._check_postgres", return_value=True),
            patch("recon.api.app._check_s3", return_value=False),
            patch("recon.queue.streams.pending_count", return_value=0),
            patch("recon.api.app.get_redis", return_value=MagicMock(xlen=lambda _k: 0)),
        ):
            resp = client.get("/healthz", headers={"X-Tenant-Id": "t1"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "degraded"

    def test_healthz_queues_omitted_when_redis_down(self, client: TestClient):
        with (
            patch("recon.api.app._check_redis", return_value=False),
            patch("recon.api.app._check_postgres", return_value=True),
            patch("recon.api.app._check_s3", return_value=True),
            patch(
                "recon.api.app.get_redis",
                return_value=MagicMock(ping=MagicMock(side_effect=Exception)),
            ),
        ):
            resp = client.get("/healthz", headers={"X-Tenant-Id": "t1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["checks"]["redis"] is False
        assert body["queues"] == {}


class TestMetricsEndpoint:
    def test_metrics_returns_prometheus_text(self, client: TestClient):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        # Verify our custom metrics are present in the output
        assert "recon_jobs_total" in resp.text
        assert "recon_http_requests_total" in resp.text
