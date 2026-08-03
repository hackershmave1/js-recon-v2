import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, JSON, String
from sqlalchemy.dialects.postgresql import UUID

from ..db import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    # The defaults document a session inherits (scope/capture/denylist/analysis).
    # Shape + validation live in app/project_config.py. JSON (not JSONB) per repo convention.
    defaults = Column(JSON, default=dict, nullable=False)
