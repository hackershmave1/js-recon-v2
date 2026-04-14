import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String
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

    files = relationship("File", back_populates="session", cascade="all, delete-orphan")
    asset_nodes = relationship("AssetNode", back_populates="session", cascade="all, delete-orphan")
    asset_edges = relationship("AssetEdge", back_populates="session", cascade="all, delete-orphan")
