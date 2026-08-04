from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

# Get the app directory
app_dir = Path(__file__).parent.parent.parent
templates = Jinja2Templates(directory=str(app_dir / "templates"))

router = APIRouter()


def _workspace_asset_version() -> str:
    """Cache-busting token derived from the built bundle's mtime.

    The SPA bundle is committed and served as a static file, so without a version
    query browsers serve a stale ``app.js`` after every rebuild. Keying the query
    on the file mtime invalidates the cache exactly when the bundle changes.
    """
    bundle = app_dir / "static" / "workspace" / "app.js"
    try:
        return str(int(bundle.stat().st_mtime))
    except OSError:
        return "0"


@router.get("/", response_class=HTMLResponse)
@router.get("/workspace", response_class=HTMLResponse)
def workspace(request: Request):
    """Serve the RECON Workspace SPA (Preact).

    This is the primary UI, served at both ``/`` and ``/workspace``. It replaced the
    legacy multi-page dashboard once the phased workspace rebuild (UI-002) completed.
    """
    return templates.TemplateResponse(
        "workspace.html",
        {"request": request, "asset_version": _workspace_asset_version()},
    )


@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/analysis", response_class=HTMLResponse)
@router.get("/view_files", response_class=HTMLResponse)
@router.get("/sessions", response_class=HTMLResponse)
def legacy_dashboard_redirect():
    """Redirect retired legacy dashboard URLs to the workspace SPA.

    The old dashboard was a server-rendered multi-page UI; the workspace is a
    client-routed SPA, so these paths no longer map to distinct pages. Redirecting
    (rather than 404ing) keeps any existing bookmarks working.
    """
    return RedirectResponse(url="/", status_code=307)


@router.get("/dashboard/health", tags=["dashboard"])
def dashboard_health():
    """
    Dashboard-specific health check.
    """
    return {
        "status": "healthy",
        "dashboard": "operational",
        "features": [
            "javascript_analysis",
            "file_management",
            "session_tracking",
            "real_time_results"
        ]
    }
