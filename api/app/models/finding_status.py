from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from ..db import Base


class FindingStatus(Base):
    """Analyst triage status for a single finding within a session.

    Findings themselves are not stored as rows — they are derived on demand from
    ``FileAnalysis.analysis`` (secrets/endpoints). To persist triage state across
    reloads we key a status row by ``(session_id, fingerprint)``.

    NOTE: ``fingerprint`` is a stable identity computed CLIENT-SIDE from the
    canonical string ``"<kind>|<value>|<file>|<line>"`` (a synchronous non-crypto
    hash, hex-encoded) in ``web/src/transforms.js`` (``fingerprintOf``). It is an
    opaque identity key, not a security token. The backend stores it verbatim and
    never recomputes it — if the canonical format in ``transforms.js`` ever
    changes, previously stored fingerprints orphan silently, so the two must stay
    in lockstep.

    ``session_id`` mirrors ``Job.session_id``: a 36-char UUID string with an index
    but no foreign key, so a status can be recorded for any session id.
    """

    __tablename__ = "finding_status"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(String(36), nullable=False, index=True)
    fingerprint = Column(String(64), nullable=False, index=True)
    # new | reviewed | confirmed | false_positive
    status = Column(String(32), nullable=False, default="new")
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("session_id", "fingerprint", name="uq_finding_status_session_fingerprint"),
        Index("idx_finding_status_session_id", "session_id"),
    )

    def __repr__(self) -> str:
        return f"<FindingStatus {self.session_id}:{self.fingerprint[:8]} {self.status}>"
