"""Tests for finding triage persistence (UI-002 Phase 2).

Covers the GET/PUT ``/api/sessions/{id}/finding-status`` round-trip, the
unique-constraint upsert path, and status validation.

These tests run against the real database the app is configured for (Postgres in
the container) rather than the SQLite fixture in ``conftest.py``: the ORM models
use ``postgresql.UUID`` PKs, which SQLAlchemy 2.0.23 cannot compile under SQLite,
so ``Base.metadata.create_all`` fails there. Hitting the real DB also exercises
the actual migration (``0003``) and the IntegrityError-based upsert path.

Each test uses a fresh random session id, so it is isolated from any other rows.
Run inside the API container, e.g.:
    docker compose -f api/docker-compose.yml exec -T api \
        sh -lc "cd /app && uv run pytest tests/test_ui002_finding_status.py -q"
"""
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.api.routes.triage import FindingStatusUpdate, set_finding_status

client = TestClient(app)

FP = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6abcd"


def _session() -> str:
    return str(uuid.uuid4())


@pytest.mark.integration
def test_get_empty_finding_status():
    """A session with no triage rows returns an empty status map."""
    resp = client.get(f"/api/sessions/{_session()}/finding-status")
    assert resp.status_code == 200
    assert resp.json() == {"statuses": {}}


@pytest.mark.integration
def test_put_then_get_round_trips_status():
    """PUT persists a status that GET then reflects, keyed by fingerprint."""
    session = _session()
    put = client.put(
        f"/api/sessions/{session}/finding-status",
        json={"fingerprint": FP, "status": "confirmed"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["status"] == "confirmed"

    got = client.get(f"/api/sessions/{session}/finding-status")
    assert got.status_code == 200
    assert got.json()["statuses"] == {FP: "confirmed"}


@pytest.mark.integration
def test_put_upserts_existing_row():
    """A second PUT for the same (session, fingerprint) overwrites, not duplicates."""
    session = _session()
    client.put(
        f"/api/sessions/{session}/finding-status",
        json={"fingerprint": FP, "status": "reviewed"},
    )
    client.put(
        f"/api/sessions/{session}/finding-status",
        json={"fingerprint": FP, "status": "false_positive"},
    )
    got = client.get(f"/api/sessions/{session}/finding-status")
    assert got.json()["statuses"] == {FP: "false_positive"}  # single row, latest value wins


@pytest.mark.integration
def test_put_rejects_invalid_status():
    """An unknown status value is rejected with 422 and persists nothing."""
    session = _session()
    resp = client.put(
        f"/api/sessions/{session}/finding-status",
        json={"fingerprint": FP, "status": "bogus"},
    )
    assert resp.status_code == 422
    got = client.get(f"/api/sessions/{session}/finding-status")
    assert got.json()["statuses"] == {}


def test_upsert_recovers_from_concurrent_insert_collision():
    """The IntegrityError race path: initial insert collides, then we update the
    row a concurrent writer committed. Unit-tested with a mock session so the hard
    concurrency branch has coverage without real threads."""
    existing = MagicMock()  # the row the "concurrent" writer committed
    db = MagicMock()
    # Initial lookup misses, post-rollback re-query finds the concurrent row.
    db.query.return_value.filter.return_value.first.side_effect = [None, existing]
    db.commit.side_effect = [IntegrityError("dup", None, Exception()), None]

    result = set_finding_status(
        "sess-race", FindingStatusUpdate(fingerprint=FP, status="confirmed"), db
    )

    db.rollback.assert_called_once()          # rolled back the failed insert
    assert existing.status == "confirmed"     # updated the concurrent row
    assert db.commit.call_count == 2          # failed insert, then successful update
    assert result["status"] == "confirmed"
