import uuid

from sqlalchemy import Column, Boolean, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ..db import Base


class SourceMap(Base):
    __tablename__ = "source_maps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id"), nullable=False)
    map_url = Column(Text, nullable=True)
    detected_map_url = Column(Text, nullable=True)
    stored_path = Column(Text, nullable=True)
    parsed = Column(Boolean, default=False, nullable=False)
    processing_status = Column(String, nullable=False, default="pending")
    processing_error = Column(Text, nullable=True)
    reconstructed_files_count = Column(Integer, nullable=False, default=0)
    processed_at = Column(DateTime, nullable=True)
    validation_state = Column(JSON, nullable=True)
    content_purged = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    content_purged_at = Column(DateTime, nullable=True)
    purge_reason = Column(Text, nullable=True)

    file = relationship("File", back_populates="source_map")
