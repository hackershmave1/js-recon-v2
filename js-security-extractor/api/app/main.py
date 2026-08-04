import subprocess
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from . import models  # noqa: F401
from .db import SessionLocal
from .api.routes.ingestion import router as ingestion_router
from .api.routes.sessions import router as sessions_router
from .api.routes.files import router as files_router
from .api.routes.enhanced_analysis import router as enhanced_analysis_router
from .api.routes.dashboard import router as dashboard_router
from .api.routes.recon import router as recon_router
from .api.routes.asset_graph import router as asset_graph_router
from .api.routes.triage import router as triage_router
from .api.routes.projects import router as projects_router
from .services.job_recovery import recover_orphaned_jobs

app = FastAPI(
    title="JS Security Extractor API",
    description="Backend processing API for JavaScript security reviews",
    version="3.0.0"
)

LOCAL_DASHBOARD_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=LOCAL_DASHBOARD_ORIGINS,
    allow_origin_regex=r"^chrome-extension://[a-p]{32}$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app_dir = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(app_dir / "static")), name="static")

@app.on_event("startup")
def on_startup():
    # Run pending Alembic migrations on startup.
    project_root = _find_alembic_root()
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Alembic upgrade head failed:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    db = SessionLocal()
    try:
        recover_orphaned_jobs(db)
    finally:
        db.close()


def _find_alembic_root() -> str:
    """Find the nearest parent directory containing alembic.ini."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "alembic.ini").is_file():
            return str(candidate)
    raise RuntimeError("Could not find alembic.ini in application parent directories")

@app.get("/api")
async def api_root():
    return {
        "message": "JS Security Extractor API",
        "version": "3.0.0", 
        "status": "running",
        "endpoints": {
            "dashboard": "/",
            "health": "/health",
            "comprehensive_analysis": "/api/analyze-comprehensive",
            "url_analysis": "/api/analyze-by-url",
            "jsluice_analysis": "/api/analyze-jsluice", 
            "file_storage": "/api/save-files",
            "file_retrieval": "/api/files/{file_id}",
            "file_delete": "/api/files/{file_id}",
            "session_delete": "/api/sessions/{session_id}",
            "session_rename": "/api/sessions/{session_id}",
            "session_sourcemap_validation": "/api/sessions/{session_id}/sourcemap-validation",
            "recon_start": "/api/recon/jobs/start",
            "recon_list": "/api/recon/jobs",
            "recon_status": "/api/recon/jobs/{job_id}",
            "recon_stop": "/api/recon/jobs/{job_id}/stop",
            "asset_graph": "/api/sessions/{session_id}/asset-graph",
            "asset_graph_ancestry": "/api/sessions/{session_id}/asset-graph/node/{node_id}/ancestry",
            "asset_graph_descendants": "/api/sessions/{session_id}/asset-graph/node/{node_id}/descendants",
            "asset_graph_gaps": "/api/sessions/{session_id}/asset-graph/gaps",
            "asset_graph_stats": "/api/sessions/{session_id}/asset-graph/stats"
        }
    }

@app.get("/health")
@app.get("/api/health")
async def health():
    # /api/health is the path the Chrome extension's "Test connection" check uses
    # (it derives the API origin and appends /api/health); keep it as an alias of /health.
    return {"status": "healthy"}


# Include routers
app.include_router(dashboard_router)  # This includes the root "/" route
app.include_router(ingestion_router)
app.include_router(sessions_router)
app.include_router(files_router)
app.include_router(enhanced_analysis_router)
app.include_router(recon_router)
app.include_router(asset_graph_router)
app.include_router(triage_router)
app.include_router(projects_router)
