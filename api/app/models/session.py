import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ..db import Base


class Session(Base):
    __tablename__ = "sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    name = Column(String, nullable=True)
    source = Column(String, default="extension", nullable=False)
    version = Column(String, default="3.0.0", nullable=False)
    # Scope definition: the root hostname(s) this session targets plus whether their
    # subdomains count as in-scope. Drives in-scope / subdomain / third-party
    # classification of findings and assets. JSON (not JSONB) for SQLite-test parity.
    root_domains = Column(JSON, default=list, nullable=False)
    include_subdomains = Column(Boolean, default=True, nullable=False)
    # Project membership (optional): the engagement this session belongs to. Nullable
    # so sessions can run standalone; SET NULL so deleting a project leaves its
    # sessions loose (their snapshot below is self-contained). See project_config.py.
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    # Snapshot (at create) of the non-scope config groups (capture/denylist/analysis)
    # the session captured under. Scope stays in root_domains/include_subdomains above.
    capture_config = Column(JSON, nullable=True)
    # Dotted config paths the session overrode vs its project (provenance for the UI).
    override_keys = Column(JSON, default=list, nullable=False)

    files = relationship("File", back_populates="session", cascade="all, delete-orphan")
    asset_nodes = relationship("AssetNode", back_populates="session", cascade="all, delete-orphan")
    asset_edges = relationship("AssetEdge", back_populates="session", cascade="all, delete-orphan")
