"""App-wide error mapping for client input that only Postgres can reject.

Every resource id in this API is a UUID, but routes take ids as plain ``str`` and
hand them straight to the DB. A malformed id (``""``, ``"abc"``) therefore surfaces
as a psycopg2 ``InvalidTextRepresentation`` (SQLSTATE 22P02) wrapped in SQLAlchemy's
``DataError`` — an unhandled 500 that also leaked as "server broke" to the operator.
A malformed id names no resource, exactly like a well-formed unknown id, so it gets
the same 404 the unknown-id path already returns. Mapping it once here covers every
route (``POST /runs`` body ids and ``/runs/{run_id}/...`` path ids alike) instead of
re-validating in each handler.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg2 import errorcodes
from sqlalchemy.exc import DataError

from recon.observability import get_logger

log = get_logger("recon.api.errors")


async def _data_error(request: Request, exc: DataError) -> JSONResponse:
    # Only the malformed-literal class is client input; any other DataError (numeric
    # overflow, string truncation, ...) is a real server-side bug and must stay a 500.
    if getattr(exc.orig, "pgcode", None) != errorcodes.INVALID_TEXT_REPRESENTATION:
        raise exc
    log.info("api.malformed_identifier", method=request.method, path=request.url.path)
    return JSONResponse(status_code=404, content={"detail": "not found (malformed id)"})


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DataError, _data_error)  # type: ignore[arg-type]
