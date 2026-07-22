"""FastAPI application — the thin accept/validate/enqueue/read tier (REQ-A1).

No route does crawl/fetch/parse/LLM/probe work; they only touch Postgres and
Redis and return. Heavy work happens in the worker process.
"""

from __future__ import annotations

from fastapi import FastAPI
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
    return app


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
