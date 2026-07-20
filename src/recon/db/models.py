"""Relational model for slice 1 (REQ-D1).

tenant -> app_user
tenant -> session (engagement, holds the scope lock) -> run -> {job, run_event}

Blobs are referenced by key (REQ-D2); no artifact bytes live in a row. Every
tenant-scoped table gets a row-level-security policy in the migration (REQ-S1);
findings/endpoints/params attach under ``run`` in slice 2.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from recon.db.base import Base
from recon.domain import JobState, QueueName, RunStage, RunState

_UUID_PK = {
    "primary_key": True,
    "server_default": text("gen_random_uuid()"),
}


def _enum_check(column: str, enum_cls) -> str:
    values = ", ".join(f"'{m.value}'" for m in enum_cls)
    return f"{column} IN ({values})"


def _now_col(**kwargs) -> Mapped[dt.datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=text("now()"), **kwargs
    )


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (UniqueConstraint("tenant_id", "email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="analyst")
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)


class EngagementSession(Base):
    """An engagement grouping runs; owns the scope lock (REQ-P3, REQ-C1).

    Table name ``session`` matches the spec's "sessions" data store; the class is
    named to avoid confusion with a SQLAlchemy ``Session``."""

    __tablename__ = "session"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    name: Mapped[str | None] = mapped_column(Text)
    # Declared in-scope hosts; egress scope is never derived from crawled URLs (REQ-P2).
    scope_hosts: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    authorization_ack: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    authorized_by: Mapped[str | None] = mapped_column(Text)
    authorized_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)

    runs: Mapped[list["Run"]] = relationship(back_populates="session")


class Run(Base):
    """An immutable recon-run snapshot and its state machine (REQ-A2, REQ-D5)."""

    __tablename__ = "run"
    __table_args__ = (
        CheckConstraint(_enum_check("state", RunState), name="ck_run_state"),
        CheckConstraint(
            _enum_check("stage", RunStage) + " OR stage IS NULL", name="ck_run_stage"
        ),
        Index("ix_run_tenant_session", "tenant_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=RunState.QUEUED.value
    )
    stage: Mapped[str | None] = mapped_column(String(20))
    pause_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    resumed_from_stage: Mapped[str | None] = mapped_column(String(20))
    # REQ-D5: only a run complete on both axes may assert removals in a diff.
    completeness: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{\"fetch_ok\": false, \"analyze_ok\": false}'::jsonb"),
    )
    input_ref: Mapped[str | None] = mapped_column(Text)  # object-storage key (REQ-D2)
    target: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped["EngagementSession"] = relationship(back_populates="runs")


class Job(Base):
    """One unit of work per stage; carries the REQ-R1 progress record."""

    __tablename__ = "job"
    __table_args__ = (
        CheckConstraint(_enum_check("queue", QueueName), name="ck_job_queue"),
        CheckConstraint(_enum_check("state", JobState), name="ck_job_state"),
        Index("ix_job_run", "run_id"),
        Index("ix_job_lease", "state", "lease_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="CASCADE"), nullable=False
    )
    queue: Mapped[str] = mapped_column(String(20), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=JobState.QUEUED.value
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5")
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Progress record (REQ-R1).
    done: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    eta_seconds: Mapped[int | None] = mapped_column(Integer)
    heartbeat_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col(nullable=False)


class RunEvent(Base):
    """Durable append-only mirror of the per-run Redis event stream (REQ-R2).

    ``id`` orders events globally and per run and is the durable source of truth
    for replay if the Redis fast-path stream is trimmed or lost."""

    __tablename__ = "run_event"
    __table_args__ = (Index("ix_run_event_run", "run_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)


# Tables carrying a tenant_id get FORCE RLS in the migration.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "app_user",
    "session",
    "run",
    "job",
    "run_event",
)
