"""PATCH /sessions/{id} {app_packages}: the per-session "treat as app code" list (#181)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from recon.api import sessions_router
from recon.api.app import create_app

TENANT = {"X-Tenant-Id": str(uuid.uuid4())}


def test_sets_the_sessions_app_packages(monkeypatch):
    seen = {}

    def fake(tenant_id, session_id, *, packages):
        seen.update(session_id=session_id, packages=packages)
        return {"session_id": session_id, "app_packages": packages}

    monkeypatch.setattr(sessions_router.service, "set_app_packages", fake)
    monkeypatch.setattr(sessions_router, "_session_view_dict", lambda view: view)
    r = TestClient(create_app()).patch(
        "/sessions/s1", headers=TENANT, json={"app_packages": ["@acme/*", " lodash-es ", "@acme/*"]}
    )
    assert r.status_code == 200
    assert seen == {"session_id": "s1", "packages": ["@acme/*", "lodash-es"]}  # trimmed, deduped


def test_rejects_entries_that_are_not_package_names(monkeypatch):
    monkeypatch.setattr(sessions_router.service, "set_app_packages", lambda *a, **k: {})
    r = TestClient(create_app()).patch(
        "/sessions/s1", headers=TENANT, json={"app_packages": ["../../etc"]}
    )
    assert r.status_code == 422
