from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

# Get the app directory
app_dir = Path(__file__).parent.parent.parent
templates = Jinja2Templates(directory=str(app_dir / "templates"))

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/analysis", response_class=HTMLResponse)
@router.get("/view_files", response_class=HTMLResponse)
@router.get("/sessions", response_class=HTMLResponse)
def dashboard(request: Request):
    """
    Serve the main dashboard interface.
    """
    return templates.TemplateResponse("dashboard.html", {"request": request})

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
