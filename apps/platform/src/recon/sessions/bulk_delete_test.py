"""Bulk session delete: one request deletes a batch through the single-delete path and reports
what it could not delete, instead of all-or-nothing (the S3 purge isn't transactional anyway)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.sessions import bulk_delete

TENANT = {"X-Tenant-Id": str(uuid.uuid4())}


def _client(monkeypatch, outcome):
    calls: list[str] = []

    def fake_delete(tenant_id, session_id, actor=None):
        calls.append(session_id)
        result = outcome[session_id]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(bulk_delete.service, "delete_session", fake_delete)
    return TestClient(create_app()), calls


def test_reports_deleted_not_found_and_failed_separately(monkeypatch):
    a, b, c = (str(uuid.uuid4()) for _ in range(3))
    client, calls = _client(monkeypatch, {a: True, b: False, c: RuntimeError("boom")})
    r = client.post("/sessions/bulk-delete", headers=TENANT, json={"session_ids": [a, b, c]})
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == [a]
    assert body["not_found"] == [b]
    assert body["failed"] == [{"id": c, "error": "RuntimeError"}]  # class only: no internals leak
    assert calls == [a, b, c]  # one failure never stops the rest


def test_duplicate_ids_are_deleted_once(monkeypatch):
    a = str(uuid.uuid4())
    client, calls = _client(monkeypatch, {a: True})
    r = client.post("/sessions/bulk-delete", headers=TENANT, json={"session_ids": [a, a.upper()]})
    assert r.json()["deleted"] == [a]
    assert calls == [a]


def test_a_malformed_id_rejects_the_batch_before_anything_is_deleted(monkeypatch):
    a = str(uuid.uuid4())
    client, calls = _client(monkeypatch, {a: True})
    r = client.post("/sessions/bulk-delete", headers=TENANT, json={"session_ids": [a, "nope"]})
    assert r.status_code == 422
    assert calls == []


def test_exactly_the_cap_is_accepted(monkeypatch):
    ids = [str(uuid.uuid4()) for _ in range(bulk_delete.MAX_BATCH)]
    client, calls = _client(monkeypatch, dict.fromkeys(ids, True))
    r = client.post("/sessions/bulk-delete", headers=TENANT, json={"session_ids": ids})
    assert r.status_code == 200
    assert len(r.json()["deleted"]) == bulk_delete.MAX_BATCH


def test_batch_size_is_bounded(monkeypatch):
    # One request stays well under a proxy timeout; the UI sends larger selections in batches.
    client, calls = _client(monkeypatch, {})
    too_many = [str(uuid.uuid4()) for _ in range(bulk_delete.MAX_BATCH + 1)]
    assert (
        client.post(
            "/sessions/bulk-delete", headers=TENANT, json={"session_ids": too_many}
        ).status_code
        == 422
    )
    assert (
        client.post("/sessions/bulk-delete", headers=TENANT, json={"session_ids": []}).status_code
        == 422
    )
    assert calls == []
