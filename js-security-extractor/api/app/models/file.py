import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from ..db import Base


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint('session_id', 'content_hash', name='files_session_content_unique'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False)
    url = Column(Text, nullable=False)
    content_hash = Column(String, nullable=False)
    content_type = Column(String, nullable=True)
    content_encoding = Column(String, nullable=True)
    content_length = Column(Integer, nullable=False)
    captured_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    file_metadata = Column(JSONB, nullable=True)
    stored_path = Column(Text, nullable=False)
    map_path = Column(Text, nullable=True)
    content_purged = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    content_purged_at = Column(DateTime, nullable=True)
    purge_reason = Column(Text, nullable=True)

    session = relationship("Session", back_populates="files")
    dependencies = relationship("Dependency", back_populates="file", cascade="all, delete-orphan")
    source_map = relationship("SourceMap", back_populates="file", uselist=False, cascade="all, delete-orphan")
    analysis_result = relationship("FileAnalysis", back_populates="file", uselist=False, cascade="all, delete-orphan")
    asset_node = relationship("AssetNode", back_populates="file", uselist=False)
