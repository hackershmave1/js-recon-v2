import uuid

from sqlalchemy import Column, ForeignKey, Text, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ..db import Base


class Dependency(Base):
    __tablename__ = "dependencies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id"), nullable=False)
    dep_url = Column(Text, nullable=False)
    resolved_url = Column(Text, nullable=True)
    dep_type = Column(String, nullable=True)

    file = relationship("File", back_populates="dependencies")
