"""Unit tests for GET /sessions/{session_id}/findings/summary (D46).

Fast-lane: no Postgres or Redis required. Both the query function and the HTTP
layer are monkeypatched so these pass in the host-tests CI lane.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.findings import queries as findings_queries
from recon.findings.queries import (
    FindingCounts,
    SessionFindingsSummary,
    TopFinding,
)
from recon.sessions import service as sessions_service

TENANT = "22222222-2222-2222-2222-222222222222"
SESSION_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())


def _client() -> TestClient:
    return TestClient(create_app())


def _headers() -> dict:
    return {"X-Tenant-Id": TENANT}


def _make_summary(
    *,
    total: int = 10,
    endpoints: int = 5,
    secrets: int = 2,
    internal_ips: int = 1,
    graphql: int = 1,
    other: int = 1,
    top_findings: list[TopFinding] | None = None,
) -> SessionFindingsSummary:
    if top_findings is None:
        top_findings = [
            TopFinding(type="secret", value="[redacted]", priority=90),
            TopFinding(type="endpoint", value="GET /api/users", priority=45),
        ]
    return SessionFindingsSummary(
        session_id=SESSION_ID,
        run_id=RUN_ID,
        counts=FindingCounts(
            total=total,
            endpoints=endpoints,
            secrets=secrets,
            internal_ips=internal_ips,
            graphql=graphql,
            other=other,
        ),
        top_findings=top_findings,
    )


def test_summary_returns_complete_when_run_exists(monkeypatch):
    """Happy path: a session with a completed run returns the full summary."""
    monkeypatch.setattr(
        findings_queries, "get_session_findings_summary", lambda *_: _make_summary()
    )
    client = _client()
    resp = client.get(
        f"/sessions/{SESSION_ID}/findings/summary", headers=_headers()
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "complete"
    assert body["session_id"] == SESSION_ID
    assert body["run_id"] == RUN_ID
    counts = body["counts"]
    assert counts["total"] == 10
    assert counts["endpoints"] == 5
    assert counts["secrets"] == 2
    assert counts["internal_ips"] == 1
    assert counts["graphql"] == 1
    assert counts["other"] == 1
    assert len(body["top_findings"]) == 2
    top = body["top_findings"][0]
    assert top["type"] == "secret"
    assert top["value"] == "[redacted]"
    assert top["priority"] == 90


def test_summary_returns_no_run_when_none_found(monkeypatch):
    """No terminal run for the session: the endpoint returns status=no_run (not 404)."""
    monkeypatch.setattr(
        findings_queries, "get_session_findings_summary", lambda *_: None
    )
    client = _client()
    resp = client.get(
        f"/sessions/{SESSION_ID}/findings/summary", headers=_headers()
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "no_run"}


def test_summary_requires_tenant_header():
    """No X-Tenant-Id header and auth disabled: the dep rejects with 401."""
    client = _client()
    resp = client.get(f"/sessions/{SESSION_ID}/findings/summary")
    # In auth-off dev mode get_tenant_id requires the header; absent = 401.
    assert resp.status_code == 401


def test_summary_resolves_ext_session_id_fallback(monkeypatch):
    """Extension sends its own UUID (external_id); the endpoint falls back to an
    external_id lookup so the popup card works without the platform UUID."""
    ext_uuid = str(uuid.uuid4())
    platform_uuid = str(uuid.uuid4())
    # First query (by platform UUID) misses; fallback (by external_id) finds it.
    call_count = {"n": 0}

    def fake_summary(tenant_id, sid):
        call_count["n"] += 1
        return _make_summary() if sid == platform_uuid else None

    monkeypatch.setattr(findings_queries, "get_session_findings_summary", fake_summary)
    monkeypatch.setattr(
        sessions_service, "find_session_id_by_external_id", lambda t, eid: platform_uuid
    )
    client = _client()
    resp = client.get(f"/sessions/{ext_uuid}/findings/summary", headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "complete"
    assert call_count["n"] == 2  # once for ext_uuid (miss), once for platform_uuid (hit)


def test_summary_top_findings_are_secrets_redacted(monkeypatch):
    """Secret values must be [redacted] — the query layer enforces REQ-S2."""
    top = [
        TopFinding(type="secret", value="[redacted]", priority=90),
        TopFinding(type="secret_suspected", value="[redacted]", priority=55),
        TopFinding(type="internal_ip", value="10.0.0.1", priority=60),
    ]
    monkeypatch.setattr(
        findings_queries,
        "get_session_findings_summary",
        lambda *_: _make_summary(top_findings=top),
    )
    client = _client()
    resp = client.get(
        f"/sessions/{SESSION_ID}/findings/summary", headers=_headers()
    )
    body = resp.json()
    assert body["status"] == "complete"
    findings = body["top_findings"]
    # Secrets must never surface raw values in the popup card.
    for f in findings:
        if f["type"] in ("secret", "secret_suspected"):
            assert f["value"] == "[redacted]", f"Secret value not redacted: {f}"
    # Non-secret values pass through.
    ip_finding = next(f for f in findings if f["type"] == "internal_ip")
    assert ip_finding["value"] == "10.0.0.1"
