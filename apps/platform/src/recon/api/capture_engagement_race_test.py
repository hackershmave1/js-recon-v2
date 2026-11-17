"""A capture that races an engagement delete gets an unbound session, never a 500 (#262).

create_session checks the engagement exists, then INSERTs. If the engagement is deleted in
between, the INSERT fails its FK check (an IntegrityError, not SessionInvalid); the ingest
must then bind nothing, exactly as for an unknown projectId.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from psycopg2 import errorcodes
from sqlalchemy.exc import IntegrityError

from recon.api import capture_router


def _integrity_error(pgcode: str) -> IntegrityError:
    return IntegrityError("INSERT INTO session", {}, SimpleNamespace(pgcode=pgcode))


def _fake_create(first_error: Exception, calls: list):
    def _create(tenant_id, **kwargs):
        calls.append(kwargs["engagement_id"])
        if len(calls) == 1:
            raise first_error
        return SimpleNamespace(id="session-1")

    return _create


def test_engagement_deleted_mid_create_falls_back_to_unbound(monkeypatch):
    calls: list = []
    error = _integrity_error(errorcodes.FOREIGN_KEY_VIOLATION)
    monkeypatch.setattr(
        capture_router.sessions_service, "create_session", _fake_create(error, calls)
    )
    session_id = capture_router._create_capture_session("tenant", "ext-1", "eng-gone", ["acme.io"])
    assert session_id == "session-1"
    assert calls == ["eng-gone", None]


def test_the_external_id_unique_race_is_not_swallowed(monkeypatch):
    # _get_or_create_session resolves this one by re-selecting the winner; retrying unbound
    # here would hide it.
    calls: list = []
    error = _integrity_error(errorcodes.UNIQUE_VIOLATION)
    monkeypatch.setattr(
        capture_router.sessions_service, "create_session", _fake_create(error, calls)
    )
    with pytest.raises(IntegrityError):
        capture_router._create_capture_session("tenant", "ext-1", "eng-1", ["acme.io"])
    assert calls == ["eng-1"]
