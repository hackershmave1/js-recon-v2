from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.types import JSON

from ..db import Base


class Job(Base):
    """Persisted record for background jobs (recon and session analysis).

    Replaces the in-memory RECON_JOBS and SESSION_ANALYSIS_JOBS module-level
    dicts in api/app/api/routes/recon.py and sessions.py.

    job_type values:
      "recon"            — created by POST /api/recon/jobs/start
      "session_analysis" — created by POST /api/sessions/{id}/analyze/start

    status values (shared):
      queued | running | completed | failed | cancelled | cancelling | idle
    """

    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type = Column(String(32), nullable=False)          # "recon" | "session_analysis"
    session_id = Column(String(36), nullable=True, index=True)  # UUID string; indexed for lookups
    status = Column(String(32), nullable=False, default="queued")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    cancel_requested = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    cancel_requested_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    # Full job state stored as JSON; contains targets/options/assets/coverage/summary
    # for recon jobs, and counts/summary/options/files for session analysis jobs.
    state_json = Column(JSON, nullable=False, default=dict)
