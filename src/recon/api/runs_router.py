"""Run endpoints: enqueue (REQ-A1), status polling (REQ-R4), SSE stream (REQ-R2),
and pause/cancel/resume (REQ-A4)."""

from __future__ import annotations

import json
import time
from typing import Iterator

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from redis import Redis

from recon.api.deps import get_redis, get_tenant_id
from recon.config import get_settings
from recon.domain import TERMINAL_STATES
from recon.events import stream
from recon.runs import coordinator, queries, service
from recon.sessions import service as sessions_service

router = APIRouter(tags=["runs"])

# Bound an SSE connection so a client that never reads doesn't pin a thread.
_SSE_MAX_SECONDS = 300
_SSE_BLOCK_MS = 1000


class StartRunBody(BaseModel):
    session_id: str
    target: str | None = None


@router.post("/runs", status_code=202)
def start_run(
    body: StartRunBody,
    tenant_id: str = Depends(get_tenant_id),
    redis: Redis = Depends(get_redis),
) -> dict:
    session = sessions_service.get_session(tenant_id, body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not session.authorization_ack:
        raise HTTPException(
            status_code=403, detail="session is not authorized for recon"
        )
    view = coordinator.start_run(
        redis, tenant_id=tenant_id, session_id=body.session_id, target=body.target
    )
    return {"run_id": view.id, "state": view.state}


@router.post("/runs/upload", status_code=202)
def start_run_from_upload(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    target: str | None = Form(default=None),
    tenant_id: str = Depends(get_tenant_id),
    redis: Redis = Depends(get_redis),
) -> dict:
    """Start a run from an uploaded JS bundle (``multipart/form-data``), the
    HTTP driver for the "one JS file -> findings" slice (REQ-A1, REQ-D2).

    Unlike the pure-enqueue ``POST /runs``, this writes the bundle to object
    storage before returning, so it carries its own latency budget (a blob PUT) —
    not REQ-A1's thin-tier 200ms.

    NOTE (follow-up, DoS hardening): ``max_upload_bytes`` bounds what we read into
    memory and store, but Starlette has already received and spooled the multipart
    body by the time this runs. A hard request-body limit that rejects an
    upload-flood *before* buffering belongs at the ingress (reverse-proxy
    ``client_max_body_size`` or an ASGI body-size middleware); deferred.
    """
    session = sessions_service.get_session(tenant_id, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not session.authorization_ack:
        raise HTTPException(status_code=403, detail="session is not authorized for recon")

    cap = get_settings().max_upload_bytes
    # Read at most cap+1 bytes so an oversized upload can't balloon memory here.
    content = file.file.read(cap + 1)
    if not content:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    if len(content) > cap:
        raise HTTPException(status_code=413, detail=f"uploaded file exceeds {cap} bytes")

    view = coordinator.start_run_with_input(
        redis,
        tenant_id=tenant_id,
        session_id=session_id,
        js_source=content,
        target=target,
    )
    return {"run_id": view.id, "state": view.state}


@router.get("/runs/{run_id}/status")
def get_status(
    run_id: str,
    response: Response,
    tenant_id: str = Depends(get_tenant_id),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> dict:
    status = queries.get_status(tenant_id, run_id)
    if status is None:
        raise HTTPException(status_code=404, detail="run not found")
    if if_none_match is not None and if_none_match.strip('"') == status.etag:
        return Response(status_code=304)  # unchanged (REQ-R4)
    response.headers["ETag"] = f'"{status.etag}"'
    response.headers["Cache-Control"] = "no-cache"
    return {
        "run_id": status.run_id,
        "state": status.state,
        "stage": status.stage,
        "done": status.done,
        "total": status.total,
        "pct": status.pct,
        "eta_seconds": status.eta_seconds,
        "heartbeat_at": status.heartbeat_at,
        "stalled": status.stalled,
    }


def _sse_frame(event: dict) -> str:
    return (
        f"id: {event['id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event['payload'], separators=(',', ':'))}\n\n"
    )


def _event_stream(
    redis: Redis, tenant_id: str, run_id: str, last_id: str | None
) -> Iterator[str]:
    # NOTE (follow-up, REQ-R2 hardening): replay currently reads only the Redis
    # fast-path stream. If it is trimmed past the client's Last-Event-ID (or
    # Redis restarts), the durable run_event table must backfill the gap by
    # pg_id before tailing Redis. The durable log is already written; wiring the
    # gap-replay is deferred to the slice-2 outbox work.
    # NOTE (follow-up, REQ-A1): this is a sync generator on the threadpool with
    # no client-disconnect check; convert to redis.asyncio + request.is_disconnected
    # and cap concurrent streams so many idle SSE clients can't starve the pool.
    for event in stream.replay(redis, run_id, last_id):
        last_id = event["id"]
        yield _sse_frame(event)
        if _is_terminal_event(event):
            return

    cursor = last_id or "$"
    deadline = time.monotonic() + _SSE_MAX_SECONDS
    while time.monotonic() < deadline:
        events = stream.tail(redis, run_id, cursor, block_ms=_SSE_BLOCK_MS)
        if not events:
            yield ": keep-alive\n\n"
            status = queries.get_status(tenant_id, run_id)
            if status and status.state in {s.value for s in TERMINAL_STATES}:
                return
            continue
        for event in events:
            cursor = event["id"]
            yield _sse_frame(event)
            if _is_terminal_event(event):
                return


def _is_terminal_event(event: dict) -> bool:
    return event["type"] == "run.transition" and event["payload"].get("to") in {
        s.value for s in TERMINAL_STATES
    }


@router.get("/runs/{run_id}/events")
def stream_events(
    run_id: str,
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
    redis: Redis = Depends(get_redis),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    if queries.get_status(tenant_id, run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    generator = _event_stream(redis, tenant_id, run_id, last_event_id)
    return StreamingResponse(generator, media_type="text/event-stream")


@router.post("/runs/{run_id}/pause")
def pause_run(
    run_id: str,
    tenant_id: str = Depends(get_tenant_id),
    redis: Redis = Depends(get_redis),
) -> dict:
    view = _guard(lambda: service.request_pause(redis, tenant_id=tenant_id, run_id=run_id))
    return {"run_id": view.id, "state": view.state, "pause_requested": view.pause_requested}


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    tenant_id: str = Depends(get_tenant_id),
    redis: Redis = Depends(get_redis),
) -> dict:
    view = _guard(lambda: service.request_cancel(redis, tenant_id=tenant_id, run_id=run_id))
    return {"run_id": view.id, "state": view.state, "cancel_requested": view.cancel_requested}


@router.post("/runs/{run_id}/resume")
def resume_run(
    run_id: str,
    tenant_id: str = Depends(get_tenant_id),
    redis: Redis = Depends(get_redis),
) -> dict:
    view = _guard(lambda: coordinator.resume_run(redis, tenant_id=tenant_id, run_id=run_id))
    return {"run_id": view.id, "state": view.state}


def _guard(action):
    try:
        return action()
    except service.RunNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except service.TransitionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
