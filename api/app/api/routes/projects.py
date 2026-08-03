import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Project as DbProject
from ...project_config import system_defaults, deep_merge, validate_config

router = APIRouter()


class ProjectCreateRequest(BaseModel):
    name: str
    defaults: dict | None = None


class ProjectUpdateRequest(BaseModel):
    name: str | None = None
    defaults: dict | None = None


def _safe_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project id")


def project_payload(project: DbProject) -> dict:
    return {
        "id": str(project.id),
        "name": project.name,
        "createdAt": project.created_at.isoformat(),
        "updatedAt": project.updated_at.isoformat(),
        "defaults": project.defaults or {},
    }


@router.get("/api/projects")
def list_projects(db: Session = Depends(get_db)):
    rows = db.query(DbProject).order_by(DbProject.created_at.desc()).all()
    return [project_payload(p) for p in rows]


@router.post("/api/projects")
def create_project(request: ProjectCreateRequest, db: Session = Depends(get_db)):
    name = (request.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name cannot be empty")
    filled = deep_merge(system_defaults(), request.defaults or {})
    try:
        validate_config(filled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    project = DbProject(name=name, defaults=filled)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project_payload(project)


@router.get("/api/projects/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)):
    project = db.query(DbProject).filter(DbProject.id == _safe_uuid(project_id)).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_payload(project)


@router.patch("/api/projects/{project_id}")
def update_project(project_id: str, request: ProjectUpdateRequest, db: Session = Depends(get_db)):
    project = db.query(DbProject).filter(DbProject.id == _safe_uuid(project_id)).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if request.name is not None:
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Project name cannot be empty")
        project.name = name
    if request.defaults is not None:
        try:
            validate_config(request.defaults, partial=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        merged = deep_merge(project.defaults or system_defaults(), request.defaults)
        try:
            validate_config(merged)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        project.defaults = merged
    project.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(project)
    return project_payload(project)


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    project = db.query(DbProject).filter(DbProject.id == _safe_uuid(project_id)).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(project)
    db.commit()
    return {"success": True, "id": project_id}
