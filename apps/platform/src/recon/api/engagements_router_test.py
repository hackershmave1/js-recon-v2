"""DELETE /engagements/{id} (issue #262): the route's own mapping, no infra.

The detach + RLS behaviour against a live database is pinned in
``engagements_delete_integration_test.py``.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from recon.api import engagements_router
from recon.api.app import create_app
from recon.engagements import service


def _hdr() -> dict:
    return {"X-Tenant-Id": str(uuid.uuid4())}


def test_delete_returns_the_detached_session_count(monkeypatch):
    calls = []

    def _delete(tenant_id, engagement_id, *, actor=None):
        calls.append((tenant_id, engagement_id))
        return 3

    monkeypatch.setattr(engagements_router.service, "delete_engagement", _delete)
    engagement_id = str(uuid.uuid4())
    r = TestClient(create_app()).delete(f"/engagements/{engagement_id}", headers=_hdr())
    assert r.status_code == 200
    assert r.json() == {"engagement_id": engagement_id, "detached_sessions": 3}
    assert calls and calls[0][1] == engagement_id


def test_delete_unknown_engagement_is_404(monkeypatch):
    monkeypatch.setattr(engagements_router.service, "delete_engagement", lambda *a, **k: None)
    r = TestClient(create_app()).delete(f"/engagements/{uuid.uuid4()}", headers=_hdr())
    assert r.status_code == 404
    assert r.json()["detail"] == "engagement not found"


def test_service_treats_a_malformed_id_as_not_found_without_touching_the_db(monkeypatch):
    # A non-UUID id would make Postgres raise on the cast (a 500); it must read as a 404.
    def _no_db(*args, **kwargs):
        raise AssertionError("a malformed id must not open a session")

    monkeypatch.setattr(service, "tenant_session", _no_db)
    assert service.delete_engagement(str(uuid.uuid4()), "not-a-uuid") is None
