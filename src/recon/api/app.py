"""FastAPI application — the thin accept/validate/enqueue/read tier (REQ-A1).

No route does crawl/fetch/parse/LLM/probe work; they only touch Postgres and
Redis and return. Heavy work happens in the worker process.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from recon.api import findings_router, probe_router, runs_router, sessions_router
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
    app.include_router(runs_router.router)
    app.include_router(findings_router.router)
    app.include_router(probe_router.router)

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
    dist = Path(settings.spa_dist_dir) if settings.spa_dist_dir else _default_dist()
    if not dist.is_dir():
        return  # API-only; StaticFiles(check_dir=True) would otherwise raise here
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    index = dist / "index.html"

    # Registered last → real API routes match first. Browser navigations (Accept
    # includes text/html) get the SPA shell so client-side routes like /runs/:id
    # deep-link; anything else (e.g. a typo'd API path from fetch) stays JSON 404.
    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str, accept: str = Header(default="")) -> FileResponse:
        if "text/html" in accept:
            return FileResponse(index)
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
