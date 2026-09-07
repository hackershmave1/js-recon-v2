"""Unit tests for findings_router — D50 query-param wiring.

Pattern: mock ``queries.list_findings`` and verify the router threads all
filter + pagination params into the call. No DB required.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.findings import queries


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


_TENANT = "00000000-0000-0000-0000-000000000001"
_RUN = "00000000-0000-0000-0000-000000000002"
_HEADERS = {"X-Tenant-Id": _TENANT}

_EMPTY_VIEW = queries.FindingsView(
    run_id=_RUN,
    findings=[],
    coverage=None,
    total=0,
)


def test_findings_router_returns_404_when_query_returns_none(client: TestClient) -> None:
    with patch.object(queries, "list_findings", return_value=None):
        resp = client.get(f"/runs/{_RUN}/findings", headers=_HEADERS)
    assert resp.status_code == 404


def test_findings_router_passes_filter_params_to_query(client: TestClient) -> None:
    """D50: all filter + pagination query params are threaded into list_findings."""
    with patch.object(queries, "list_findings", return_value=_EMPTY_VIEW) as mock_lf:
        resp = client.get(
            f"/runs/{_RUN}/findings"
            "?types=endpoint&types=secret"
            "&triage_statuses=open&triage_statuses=confirmed"
            "&q=login"
            "&risk_tags=auth"
            "&limit=50"
            "&offset=100",
            headers=_HEADERS,
        )
    assert resp.status_code == 200
    _kw = mock_lf.call_args.kwargs
    assert list(_kw["types"]) == ["endpoint", "secret"]
    assert list(_kw["triage_statuses"]) == ["open", "confirmed"]
    assert _kw["q"] == "login"
    assert list(_kw["risk_tags"]) == ["auth"]
    assert _kw["limit"] == 50
    assert _kw["offset"] == 100


def test_findings_router_response_includes_total_offset_limit(client: TestClient) -> None:
    """D50: response envelope carries total/offset/limit so the FE can page."""
    view = queries.FindingsView(run_id=_RUN, findings=[], coverage=None, total=999)
    with patch.object(queries, "list_findings", return_value=view):
        resp = client.get(f"/runs/{_RUN}/findings?limit=100&offset=200", headers=_HEADERS)
    body = resp.json()
    assert body["total"] == 999
    assert body["offset"] == 200
    assert body["limit"] == 100


def test_findings_router_defaults_are_unfiltered_full_fetch(client: TestClient) -> None:
    """Omitting all params keeps the pre-D50 behaviour: no filter, limit=2000, offset=0."""
    with patch.object(queries, "list_findings", return_value=_EMPTY_VIEW) as mock_lf:
        resp = client.get(f"/runs/{_RUN}/findings", headers=_HEADERS)
    assert resp.status_code == 200
    _kw = mock_lf.call_args.kwargs
    assert list(_kw["types"]) == []
    assert list(_kw["triage_statuses"]) == []
    assert _kw["q"] is None
    assert list(_kw["risk_tags"]) == []
    assert _kw["limit"] == 2000
    assert _kw["offset"] == 0
