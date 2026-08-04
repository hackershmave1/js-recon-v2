"""FastAPI application — the thin accept/validate/enqueue/read tier (REQ-A1).

No route does crawl/fetch/parse/LLM/probe work; they only touch Postgres and
Redis and return. Heavy work happens in the worker process.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from recon.api import (
    base_url_router,
    engagements_router,
    export_router,
    findings_router,
    probe_router,
    runs_router,
    sessions_router,
    sources_router,
    spec_router,
    wrappers_router,
)
from recon.api.deps import get_redis
from recon.config import get_settings
from recon.db.base import engine
from recon.observability import configure_logging, get_logger

log = get_logger("recon.api")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.env != "local")
    app = FastAPI(title="Recon platform", version="0.1.0")
    app.include_router(sessions_router.router)
    app.include_router(engagements_router.router)
    app.include_router(runs_router.router)
    app.include_router(findings_router.router)
    app.include_router(probe_router.router)
    app.include_router(sources_router.router)
    app.include_router(spec_router.router)
    app.include_router(export_router.router)
    app.include_router(base_url_router.router)
    app.include_router(wrappers_router.router)

    # SPIKE (flag-gated, throwaway): mount the extension's save-files ingest onto
    # the platform and back blob storage with LOCAL DISK instead of S3 — a seam to
    # measure how tightly analyze couples to Redis/S3/RLS. Reassigning the module
    # attributes works because every caller does `from recon import storage;
    # storage.put_blob(...)` (a call-time attribute lookup). Off by default → zero
    # impact on the normal stack. See api/capture_router.py.
    if settings.enable_capture_ingest:
        from recon import storage, storage_local
        from recon.api import capture_router

        storage.put_blob = storage_local.put_blob  # type: ignore[assignment]
        storage.get_blob = storage_local.get_blob  # type: ignore[assignment]
        app.include_router(capture_router.router)
        log.info("api.capture_ingest_enabled", storage="local-disk")

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict:
        checks = {"redis": _check_redis(), "postgres": _check_postgres()}
        healthy = all(checks.values())
        return {"status": "ok" if healthy else "degraded", "checks": checks}

    log.info("api.started", env=settings.env)
    _mount_spa(app, settings)
    return app


def _default_dist() -> Path:
    # Editable/dev layout: src/recon/api/app.py → repo_root/web/dist.
    return Path(__file__).resolve().parents[3] / "web" / "dist"


def _mount_spa(app: FastAPI, settings) -> None:
    dist = Path(settings.spa_dist_dir).resolve() if settings.spa_dist_dir else _default_dist()
    if not (dist.is_dir() and (dist / "assets").is_dir() and (dist / "index.html").is_file()):
        # API-only, or a partial/absent build; StaticFiles(check_dir=True) would
        # otherwise raise here for a missing/partial dist directory.
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    index = dist / "index.html"

    # A few client-side routes share a path with an API GET of the same name (the
    # Sessions page vs `GET /sessions`). The catch-all below can't cover those — the
    # API route matches first — so a full-page load or refresh of /sessions would hit
    # the JSON API instead of the SPA. This guard runs before routing and serves the
    # shell for a browser *navigation* (Accept: text/html), while the app's own fetch
    # (Accept: application/json) still reaches the API. Non-colliding routes like
    # /runs/:id keep relying on the catch-all fallback.
    spa_routes = {"/sessions"}
    # The shell is served `no-store` so the browser never reuses this text/html
    # response for a later SAME-URL fetch. Without it, the SPA's first
    # `fetch('/sessions')` (Accept: application/json) right after a full-page
    # navigation can be answered from cache with the HTML shell (the nav response
    # has no `Vary: Accept`), and JSON parsing then fails. Hashed assets stay
    # cacheable; only the shell is no-store.
    shell_headers = {"Cache-Control": "no-store"}

    @app.middleware("http")
    async def spa_navigation(request: Request, call_next):
        if (
            request.method == "GET"
            and request.url.path in spa_routes
            and "text/html" in request.headers.get("accept", "")
        ):
            return FileResponse(index, headers=shell_headers)
        return await call_next(request)

    # Registered last → real API routes match first. Browser navigations (Accept
    # includes text/html) get the SPA shell so client-side routes like /runs/:id
    # deep-link; anything else (e.g. a typo'd API path from fetch) stays JSON 404.
    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str, accept: str = Header(default="")) -> FileResponse:
        if "text/html" in accept:
            return FileResponse(index, headers=shell_headers)
        raise HTTPException(status_code=404, detail="not found")


def _check_redis() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:  # pragma: no cover - health check is best-effort
        return False


def _check_postgres() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # pragma: no cover
        return False


app = create_app()
