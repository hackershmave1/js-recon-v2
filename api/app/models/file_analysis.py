import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from ..db import Base


class FileAnalysis(Base):
    __tablename__ = "file_analyses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id"), nullable=False, unique=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False)
    status = Column(String, nullable=False, default="completed")
    analysis = Column(JSONB, nullable=False, default=dict)
    stats = Column(JSONB, nullable=False, default=dict)
    extractors_used = Column(JSONB, nullable=False, default=list)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    file = relationship("File", back_populates="analysis_result")
