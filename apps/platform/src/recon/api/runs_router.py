"""Run endpoints: enqueue (REQ-A1), status polling (REQ-R4), SSE stream (REQ-R2),
and pause/cancel/resume (REQ-A4)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

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
from recon.discover import queries as discover_queries
from recon.domain import TERMINAL_STATES
from recon.events import stream
from recon.fetch import egress
from recon.runs import coordinator, queries, service
from recon.sessions import service as sessions_service

router = APIRouter(tags=["runs"])

# Bound an SSE connection so a client that never reads doesn't pin a thread.
_SSE_MAX_SECONDS = 300
_SSE_BLOCK_MS = 1000


class StartRunBody(BaseModel):
    session_id: str
    target: str | None = None
    # Runtime-capture opt-in: when true, DISCOVER drives a headless Chromium (CDP)
    # to capture EXECUTED scripts instead of the static katana crawl. Requires a
    # target (the URL to open) and RECON_ENABLE_CAPTURE_MODE. See recon.capture.
    capture: bool = False


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
        raise HTTPException(status_code=403, detail="session is not authorized for recon")
    # Fail fast: a crawl target outside the session scope (or under a scopeless
    # upload session) is refused here with a clear 400 instead of dying later in
    # the worker's seed guard. This is the cheap name-only check; the worker still
    # does the full DNS/public-IP SSRF check on the seed (S2).
    if body.target and not egress.host_in_scope(
        egress.host_of(body.target),
        session.scope_hosts,
        allow_local=get_settings().allow_local_egress,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"crawl target {body.target!r} is not in the session scope",
        )
    if body.capture:
        # Kill-switch + precondition: capture drives a real browser (SSRF residual),
        # so it must be explicitly enabled and needs a URL to open.
        if not get_settings().enable_capture_mode:
            raise HTTPException(status_code=400, detail="runtime capture mode is disabled")
        if not body.target:
            raise HTTPException(
                status_code=400, detail="runtime capture requires a target URL to open"
            )
    view = coordinator.start_run(
        redis,
        tenant_id=tenant_id,
        session_id=body.session_id,
        target=body.target,
        crawl_mode="capture" if body.capture else None,
    )
    return {"run_id": view.id, "state": view.state}


@router.post("/runs/upload", status_code=202)
def start_run_from_upload(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    target: str | None = Form(default=None),
    map: UploadFile | None = File(default=None),
    tenant_id: str = Depends(get_tenant_id),
    redis: Redis = Depends(get_redis),
) -> dict:
    """Start a run from an uploaded JS bundle (``multipart/form-data``), the
    HTTP driver for the "one JS file -> findings" slice (REQ-A1, REQ-D2).

    An optional ``map`` field carries the bundle's source map; when present the
    analyze stage recovers real per-source paths from it (Sourcemapper).

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
    content = _read_capped(file, cap)
    if not content:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    if len(content) > cap:
        raise HTTPException(status_code=413, detail=f"uploaded file exceeds {cap} bytes")

    # The source map is optional; a present-but-empty map is treated as absent.
    map_source = _read_capped(map, cap) if map is not None else b""
    if len(map_source) > cap:
        raise HTTPException(status_code=413, detail=f"uploaded map exceeds {cap} bytes")

    view = coordinator.start_run_with_input(
        redis,
        tenant_id=tenant_id,
        session_id=session_id,
        js_source=content,
        map_source=map_source or None,
        target=target,
    )
    return {"run_id": view.id, "state": view.state}


def _read_capped(upload: UploadFile, cap: int) -> bytes:
    # Read at most cap+1 bytes so an oversized upload can't balloon memory here.
    return upload.file.read(cap + 1)


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
        "pause_requested": status.pause_requested,
        "cancel_requested": status.cancel_requested,
    }


@router.get("/runs/{run_id}/assets")
def get_run_assets(
    run_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    """The discovered in-scope .js assets manifest for a crawl run (REQ-C2).

    Returns a `pending` placeholder until the DISCOVERING stage records one.
    Each asset includes fetch_status and analyze_status from the run_assets
    table (missing rows default to "pending").
    """
    if queries.get_status(tenant_id, run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    manifest = discover_queries.get_assets_with_status(tenant_id, run_id)
    if manifest is None:
        return {"domain": None, "status": "pending", "assets": []}
    return manifest


def _sse_frame(event: dict) -> str:
    return (
        f"id: {event['id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event['payload'], separators=(',', ':'))}\n\n"
    )


def _event_stream(redis: Redis, tenant_id: str, run_id: str, last_id: str | None) -> Iterator[str]:
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
