"""Hermetic: the malformed-id DataError mapping (no DB — the DB error is synthesized)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg2 import errorcodes
from sqlalchemy.exc import DataError

from recon.api.errors import register_error_handlers


class _PgError(Exception):
    def __init__(self, pgcode: str) -> None:
        super().__init__(pgcode)
        self.pgcode = pgcode


def _app_raising(pgcode: str) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/runs/{run_id}/status")
    def status(run_id: str) -> dict:
        raise DataError("SELECT ...", {"id": run_id}, _PgError(pgcode))

    return app


def test_malformed_id_is_404_not_500():
    client = TestClient(_app_raising(errorcodes.INVALID_TEXT_REPRESENTATION))
    resp = client.get("/runs/not-a-uuid/status")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "not found (malformed id)"}


def test_other_data_errors_stay_server_errors():
    # A numeric overflow is a server-side bug, not client input — it must not be
    # disguised as a 404.
    client = TestClient(_app_raising(errorcodes.NUMERIC_VALUE_OUT_OF_RANGE))
    with pytest.raises(DataError):
        client.get("/runs/x/status")
