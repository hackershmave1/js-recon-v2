from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from sqlalchemy import inspect, text

from .db import Base, engine
from . import models  # noqa: F401
from .api.routes.ingestion import router as ingestion_router
from .api.routes.sessions import router as sessions_router
from .api.routes.files import router as files_router
from .api.routes.enhanced_analysis import router as enhanced_analysis_router
from .api.routes.dashboard import router as dashboard_router
from .api.routes.recon import router as recon_router
from .api.routes.asset_graph import router as asset_graph_router

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
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema_updates()

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
async def health():
    return {"status": "healthy"}


# Include routers
app.include_router(dashboard_router)  # This includes the root "/" route
app.include_router(ingestion_router)
app.include_router(sessions_router)
app.include_router(files_router)
app.include_router(enhanced_analysis_router)
app.include_router(recon_router)
app.include_router(asset_graph_router)


def ensure_runtime_schema_updates():
    with engine.begin() as conn:
        inspector = inspect(conn)
        dialect = conn.dialect.name
        bool_false_literal = "false" if dialect == "postgresql" else "0"
        tables = set(inspector.get_table_names())
        if "sessions" not in tables:
            return

        session_columns = {column["name"] for column in inspector.get_columns("sessions")}
        if "name" not in session_columns:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN name VARCHAR"))

        if "files" in tables:
            file_columns = {column["name"] for column in inspector.get_columns("files")}
            if "content_purged" not in file_columns:
                conn.execute(text(f"ALTER TABLE files ADD COLUMN content_purged BOOLEAN DEFAULT {bool_false_literal}"))
            if "content_purged_at" not in file_columns:
                conn.execute(text("ALTER TABLE files ADD COLUMN content_purged_at TIMESTAMP"))
            if "purge_reason" not in file_columns:
                conn.execute(text("ALTER TABLE files ADD COLUMN purge_reason TEXT"))
            conn.execute(text(f"UPDATE files SET content_purged = {bool_false_literal} WHERE content_purged IS NULL"))
            if dialect == "postgresql":
                conn.execute(text("ALTER TABLE files ALTER COLUMN content_purged SET DEFAULT false"))
                conn.execute(text("ALTER TABLE files ALTER COLUMN content_purged SET NOT NULL"))

        if "source_maps" not in tables:
            return

        source_map_columns = {column["name"] for column in inspector.get_columns("source_maps")}
        if "detected_map_url" not in source_map_columns:
            conn.execute(text("ALTER TABLE source_maps ADD COLUMN detected_map_url TEXT"))
        if "processing_status" not in source_map_columns:
            conn.execute(text("ALTER TABLE source_maps ADD COLUMN processing_status VARCHAR"))
            conn.execute(text("UPDATE source_maps SET processing_status = 'pending' WHERE processing_status IS NULL"))
        if "processing_error" not in source_map_columns:
            conn.execute(text("ALTER TABLE source_maps ADD COLUMN processing_error TEXT"))
        if "reconstructed_files_count" not in source_map_columns:
            conn.execute(text("ALTER TABLE source_maps ADD COLUMN reconstructed_files_count INTEGER"))
            conn.execute(text("UPDATE source_maps SET reconstructed_files_count = 0 WHERE reconstructed_files_count IS NULL"))
        if "processed_at" not in source_map_columns:
            conn.execute(text("ALTER TABLE source_maps ADD COLUMN processed_at TIMESTAMP"))
        if "validation_state" not in source_map_columns:
            if dialect == "postgresql":
                conn.execute(text("ALTER TABLE source_maps ADD COLUMN validation_state JSONB"))
            else:
                conn.execute(text("ALTER TABLE source_maps ADD COLUMN validation_state TEXT"))
        if "content_purged" not in source_map_columns:
            conn.execute(text(f"ALTER TABLE source_maps ADD COLUMN content_purged BOOLEAN DEFAULT {bool_false_literal}"))
        if "content_purged_at" not in source_map_columns:
            conn.execute(text("ALTER TABLE source_maps ADD COLUMN content_purged_at TIMESTAMP"))
        if "purge_reason" not in source_map_columns:
            conn.execute(text("ALTER TABLE source_maps ADD COLUMN purge_reason TEXT"))

        conn.execute(text("UPDATE source_maps SET processing_status = 'pending' WHERE processing_status IS NULL"))
        conn.execute(text("UPDATE source_maps SET reconstructed_files_count = 0 WHERE reconstructed_files_count IS NULL"))
        conn.execute(text(f"UPDATE source_maps SET content_purged = {bool_false_literal} WHERE content_purged IS NULL"))
        if dialect == "postgresql":
            conn.execute(text("ALTER TABLE source_maps ALTER COLUMN processing_status SET DEFAULT 'pending'"))
            conn.execute(text("ALTER TABLE source_maps ALTER COLUMN reconstructed_files_count SET DEFAULT 0"))
            conn.execute(text("ALTER TABLE source_maps ALTER COLUMN processing_status SET NOT NULL"))
            conn.execute(text("ALTER TABLE source_maps ALTER COLUMN reconstructed_files_count SET NOT NULL"))
            conn.execute(text("ALTER TABLE source_maps ALTER COLUMN content_purged SET DEFAULT false"))
            conn.execute(text("ALTER TABLE source_maps ALTER COLUMN content_purged SET NOT NULL"))
