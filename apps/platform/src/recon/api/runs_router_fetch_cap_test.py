"""Fast-lane (no live infra) tests for the per-run fetch-size cap on POST /runs.

New Recon's Advanced fold sends ``max_fetch_bytes`` on a crawl. It must follow the same
rules as edit-&-re-run: > 0 (a non-positive cap would fail open, REQ-Q5), <= the
ceiling (a clean 422, nothing persisted), and otherwise reach the coordinator. The
session lookup and the coordinator are patched out so no store is touched.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from recon.api import runs_router
from recon.api.app import create_app
from recon.api.deps import get_redis
from recon.config import get_settings

TENANT = "11111111-1111-1111-1111-111111111111"
HEADERS = {"X-Tenant-Id": TENANT}
MIB = 1024 * 1024


@pytest.fixture()
def client(monkeypatch):
    # Header tenancy (auth off) keeps the test about the cap, not about login.
    monkeypatch.setenv("RECON_AUTH_SECRET", "")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_redis] = lambda: object()
    yield TestClient(app)
    get_settings.cache_clear()


def _authorized_session():
    return SimpleNamespace(authorization_ack=True, scope_hosts=["acme.io"])


def _post(client: TestClient, **extra) -> tuple[int, object]:
    with (
        patch.object(
            runs_router.sessions_service, "get_session", return_value=_authorized_session()
        ),
        patch.object(
            runs_router.coordinator,
            "start_run",
            return_value=SimpleNamespace(id="run-1", state="queued"),
        ) as start_run,
    ):
        r = client.post(
            "/runs", headers=HEADERS, json={"session_id": "s1", "target": "acme.io", **extra}
        )
    return r.status_code, start_run


def test_crawl_passes_max_fetch_bytes_to_the_coordinator(client):
    status, start_run = _post(client, max_fetch_bytes=25 * MIB)
    assert status == 202
    assert start_run.call_args.kwargs["max_fetch_bytes"] == 25 * MIB


def test_crawl_without_a_cap_uses_the_server_default(client):
    status, start_run = _post(client)
    assert status == 202
    assert start_run.call_args.kwargs["max_fetch_bytes"] is None  # NULL = settings default


@pytest.mark.parametrize("bad", [0, -1])
def test_crawl_non_positive_cap_is_422(client, bad):
    status, start_run = _post(client, max_fetch_bytes=bad)
    assert status == 422
    start_run.assert_not_called()


def test_crawl_cap_above_ceiling_is_422_and_starts_nothing(client):
    ceiling = get_settings().max_fetch_bytes_ceiling
    status, start_run = _post(client, max_fetch_bytes=ceiling + 1)
    assert status == 422
    start_run.assert_not_called()
