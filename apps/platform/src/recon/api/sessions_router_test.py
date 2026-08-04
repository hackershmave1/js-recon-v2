"""Unknown-tenant mapping: a valid-format but unprovisioned tenant → 400."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from recon.api import sessions_router
from recon.api.app import create_app


def test_unknown_tenant_returns_400(monkeypatch):
    def _raise(*args, **kwargs):
        raise IntegrityError("insert", {}, Exception("FK violation"))

    monkeypatch.setattr(sessions_router.service, "create_session", _raise)
    client = TestClient(create_app())
    r = client.post(
        "/sessions",
        headers={"X-Tenant-Id": str(uuid.uuid4())},
        json={"scope_hosts": ["example.com"], "authorized_by": "tester"},
    )
    assert r.status_code == 400
    assert "tenant" in r.json()["detail"].lower()
