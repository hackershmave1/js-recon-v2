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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from recon.db.base import Base
from recon.domain import (
    AssetStatus,
    BaseUrlRuleKind,
    FindingType,
    JobState,
    QueueName,
    RunStage,
    RunState,
)

_UUID_PK = {
    "primary_key": True,
    "server_default": text("gen_random_uuid()"),
}


def _enum_check(column: str, enum_cls) -> str:
    values = ", ".join(f"'{m.value}'" for m in enum_cls)
    return f"{column} IN ({values})"


def _now_col(**kwargs) -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=text("now()"), **kwargs)


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


class Engagement(Base):
    """A named scope umbrella grouping sessions (design R6: Engagement -> Session -> Run).

    Holds the analyst-facing scope statement (in/out-of-scope domains) shown in the
    UI's engagement switcher. It is organizational metadata only: the *enforced*
    egress scope for a run still comes from its session's ``scope_hosts`` (REQ-P2),
    never derived from an engagement here. Tenant-scoped (RLS) like every table."""

    __tablename__ = "engagement"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    in_scope_domains: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    out_of_scope_domains: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col(nullable=False)

    sessions: Mapped[list[EngagementSession]] = relationship(back_populates="engagement")


class EngagementSession(Base):
    """An engagement grouping runs; owns the scope lock (REQ-P3, REQ-C1).

    Table name ``session`` matches the spec's "sessions" data store; the class is
    named to avoid confusion with a SQLAlchemy ``Session``."""

    __tablename__ = "session"
    __table_args__ = (
        # Capture-ingest idempotency key (DEBT D1): holds the Chrome extension's
        # sessionId for a capture session, NULL for every other session. Postgres
        # UNIQUE is NULLS DISTINCT, so the non-capture NULL rows never collide — the
        # constraint binds only capture rows, one per (tenant, ext sessionId), so two
        # concurrent ingest batches can't create duplicate sessions (self-healed on
        # IntegrityError in capture_router). Added in migration 0011.
        Index("uq_session_tenant_external_id", "tenant_id", "external_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    name: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
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
    # Optional grouping under an engagement (design R6). Nullable so pre-engagement
    # sessions stay valid; SET NULL on engagement delete orphans the session rather
    # than cascade-deleting its recon history.
    engagement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("engagement.id", ondelete="SET NULL")
    )
    # Soft-hide from the default Sessions list (R6 archive); recon data is retained.
    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    engagement: Mapped[Engagement | None] = relationship(back_populates="sessions")
    # passive_deletes: the run.session_id FK is ON DELETE CASCADE (see Run below),
    # so let the DB cascade a session delete to its runs (and their children) rather
    # than SQLAlchemy nullifying run.session_id first — which the NOT NULL column
    # rejects, 500ing delete_session for any session that has runs.
    runs: Mapped[list[Run]] = relationship(back_populates="session", passive_deletes=True)


class Run(Base):
    """An immutable recon-run snapshot and its state machine (REQ-A2, REQ-D5)."""

    __tablename__ = "run"
    __table_args__ = (
        CheckConstraint(_enum_check("state", RunState), name="ck_run_state"),
        CheckConstraint(_enum_check("stage", RunStage) + " OR stage IS NULL", name="ck_run_stage"),
        Index("ix_run_tenant_session", "tenant_id", "session_id"),
        # Capture-ingest "open accumulator" marker (DEBT D1): = the extension's
        # sessionId while THIS run is the session's open capture round, NULL for a
        # non-capture run and for a SEALED capture round (analyze/start nulls it in
        # the same tx that inserts the Job). NULLS DISTINCT → only open accumulators
        # bind, one per (tenant, ext sessionId), so concurrent first batches can't
        # create duplicate runs. Added in migration 0011.
        Index(
            "uq_run_tenant_capture_external_id",
            "tenant_id",
            "capture_external_id",
            unique=True,
        ),
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
        server_default=text('\'{"fetch_ok": false, "analyze_ok": false}\'::jsonb'),
    )
    input_ref: Mapped[str | None] = mapped_column(Text)  # object-storage key (REQ-D2)
    # Optional uploaded source map blob key; Sourcemapper recovers real per-source
    # paths from it (analyze stage). Added in migration 0003.
    source_map_ref: Mapped[str | None] = mapped_column(Text)
    target: Mapped[str | None] = mapped_column(Text)
    # Capture-ingest open-accumulator marker (see __table_args__). Added in 0011.
    capture_external_id: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[EngagementSession] = relationship(back_populates="runs")


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
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
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
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)


class Finding(Base):
    """A content-addressed finding — one row per (run, finding_hash) (REQ-D3, A3).

    ``finding_hash`` is the stable identity from ``recon.findings.normalize``
    (sha256 over type + normalized value + normalized source path). Mutable
    per-sighting detail lives on :class:`FindingOccurrence` so a normalization
    merge is visible and countable, never silently dropped (REQ-C2)."""

    __tablename__ = "finding"
    __table_args__ = (
        # Per-run, NOT global: a finding recurs with the same hash across runs so
        # REQ-D5 can diff hash sets; this keys the REQ-A3 exactly-once outbox.
        UniqueConstraint("run_id", "finding_hash", name="uq_finding_run_hash"),
        CheckConstraint(_enum_check("type", FindingType), name="ck_finding_type"),
        Index("ix_finding_run", "tenant_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="CASCADE"), nullable=False
    )
    finding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str | None] = mapped_column(String(16))
    # Type-specific display extras (method/provider/location/name); not identity.
    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    first_stage: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)

    occurrences: Mapped[list[FindingOccurrence]] = relationship(
        back_populates="finding", cascade="all, delete-orphan"
    )


class FindingOccurrence(Base):
    """One sighting of a finding (REQ-C2 honesty, REQ-A3 append-idempotent).

    A finding may have many occurrences — distinct call sites / raw URLs that
    normalized to the same identity, or the same secret at different offsets.
    ``occurrence_hash`` (over the identifying volatile subset) dedupes retries."""

    __tablename__ = "finding_occurrence"
    __table_args__ = (
        UniqueConstraint("finding_id", "occurrence_hash", name="uq_occurrence_finding_hash"),
        Index("ix_occurrence_finding", "tenant_id", "finding_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("finding.id", ondelete="CASCADE"), nullable=False
    )
    # Slice Y: which discovered asset this sighting came from. NULL for legacy
    # single-asset (upload / single-URL) runs. Part of occurrence identity via
    # asset_url (see recon.findings.store); the row keeps the FK for reveal routing.
    run_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run_asset.id", ondelete="SET NULL")
    )
    occurrence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    host: Mapped[str | None] = mapped_column(Text)  # occurrence-only, never hashed (C1)
    raw_url: Mapped[str | None] = mapped_column(Text)
    source_path: Mapped[str | None] = mapped_column(Text)
    line: Mapped[int | None] = mapped_column(Integer)
    col: Mapped[int | None] = mapped_column(Integer)
    offset_start: Mapped[int | None] = mapped_column(Integer)
    offset_end: Mapped[int | None] = mapped_column(Integer)
    evidence: Mapped[str | None] = mapped_column(Text)
    engine: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[str | None] = mapped_column(String(16))
    verified: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)

    finding: Mapped[Finding] = relationship(back_populates="occurrences")


class RunAsset(Base):
    """One discovered in-scope .js asset of a crawl run (Slice Y, REQ-C1/D5).

    Seeded pending by discover; fetch sets ``input_ref`` + ``fetch_status``; analyze
    sets ``analyze_status``. The per-asset blob lives at ``input_ref`` (kind="input").
    Absent for legacy single-asset runs, which keep using ``run.input_ref``."""

    __tablename__ = "run_asset"
    __table_args__ = (
        UniqueConstraint("run_id", "url", name="uq_run_asset_run_url"),
        CheckConstraint(_enum_check("fetch_status", AssetStatus), name="ck_run_asset_fetch_status"),
        CheckConstraint(
            _enum_check("analyze_status", AssetStatus), name="ck_run_asset_analyze_status"
        ),
        Index("ix_run_asset_run", "tenant_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    input_ref: Mapped[str | None] = mapped_column(Text)
    # Optional per-asset source map blob key (kind="source_map"). The Chrome
    # extension captures a bundle's map post-auth and uploads it with the JS; the
    # analyze stage recovers real per-source paths from it (tolerant "capture"
    # origin — a bad map falls back to bundle analysis, never fails the asset).
    # Mirrors the run-level ``Run.source_map_ref``. Added in migration 0010.
    source_map_ref: Mapped[str | None] = mapped_column(Text)
    fetch_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=AssetStatus.PENDING.value
    )
    fetch_error: Mapped[str | None] = mapped_column(Text)
    analyze_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=AssetStatus.PENDING.value
    )
    analyze_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)


class FindingTriage(Base):
    """A user's triage verdict on a finding (REQ-P1 mark-confirmed, REQ-D1).

    Keyed by (session_id, finding_hash) — NOT by run — so a verdict set on a
    stable finding identity (REQ-D3) survives re-runs (REQ-D5 continuous rescan).
    ``finding_hash`` is intentionally not a foreign key: triage outlives any single
    run's ``finding`` rows, so the join to a finding is logical (on the hash)."""

    __tablename__ = "finding_triage"
    __table_args__ = (
        UniqueConstraint("session_id", "finding_hash", name="uq_triage_session_finding"),
        CheckConstraint("status IN ('open', 'confirmed', 'dismissed')", name="ck_triage_status"),
        Index("ix_triage_session", "tenant_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    finding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="open")
    note: Mapped[str | None] = mapped_column(Text)
    # Best-effort supplied label until real per-user auth lands (see api.deps).
    actor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col(nullable=False)


class SessionSpec(Base):
    """The active OpenAPI/Swagger spec attached to a session (shadow-API slice).

    Keyed by ``session_id`` (one live spec per engagement, replaced on re-upload)
    rather than by run, mirroring :class:`FindingTriage`'s session-scoping so the
    spec survives re-runs the same way a triage verdict does. The parsed bytes
    live in object storage (kind ``"spec"``); this row is the pointer + summary
    metadata (REQ-D2 pattern)."""

    __tablename__ = "session_spec"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_session_spec_session"),
        CheckConstraint("spec_format IN ('openapi-3', 'swagger-2')", name="ck_session_spec_format"),
        Index("ix_session_spec_tenant", "tenant_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    spec_ref: Mapped[str] = mapped_column(Text, nullable=False)
    spec_format: Mapped[str] = mapped_column(String(16), nullable=False)
    server_bases: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    operation_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    actor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col(nullable=False)


class FindingSpecStatus(Base):
    """Whether a finding is documented in the attached spec or a shadow endpoint.

    Keyed by (session_id, finding_hash) — NOT by run — for the same reason as
    :class:`FindingTriage`: the spec-diff verdict on a stable finding identity
    (REQ-D3) must survive re-runs (REQ-D5 continuous rescan). ``finding_hash`` is
    intentionally not a foreign key, mirroring ``FindingTriage``."""

    __tablename__ = "finding_spec_status"
    __table_args__ = (
        UniqueConstraint("session_id", "finding_hash", name="uq_spec_status_session_finding"),
        CheckConstraint("status IN ('documented', 'shadow', 'unresolved')", name="ck_spec_status"),
        Index("ix_spec_status_session", "tenant_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    finding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(32))
    matched_operation: Mapped[str | None] = mapped_column(Text)
    spec_ref: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col(nullable=False)


class SessionBaseUrl(Base):
    """A manual base-URL rule for a session (REQ-C2). Read-time overlay only —
    applied by recon.findings.base_url at reconstruct/classify time; findings are
    never rewritten (identity non-churn). Session-scoped like session_spec."""

    __tablename__ = "session_base_url"
    __table_args__ = (
        # Prefix rules upsert on their prefix; selection rows have NULL path_prefix
        # and (NULLS DISTINCT) never collide here.
        UniqueConstraint("session_id", "path_prefix", name="uq_base_url_session_prefix"),
        CheckConstraint(_enum_check("kind", BaseUrlRuleKind), name="ck_base_url_kind"),
        CheckConstraint(
            "(kind = 'prefix' AND path_prefix IS NOT NULL AND finding_hashes IS NULL) "
            "OR (kind = 'selection' AND finding_hashes IS NOT NULL AND path_prefix IS NULL)",
            name="ck_base_url_match_field",
        ),
        Index("ix_base_url_session", "tenant_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    path_prefix: Mapped[str | None] = mapped_column(Text)
    finding_hashes: Mapped[list | None] = mapped_column(ARRAY(Text))
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col(nullable=False)


class SessionWrapper(Base):
    """A taught HTTP-client wrapper for a session (REQ-C2 first clause).

    Session-scoped (survives continuous rescans, REQ-D5) like ``session_spec`` /
    ``session_base_url``: the analyze stage and the out-of-band re-extract both read
    it to recognize ``<callee>.<method>(...)`` calls. ``UNIQUE(session_id, callee)``
    — one rule per callee; the POST upserts on it."""

    __tablename__ = "session_wrapper"
    __table_args__ = (
        UniqueConstraint("session_id", "callee", name="uq_session_wrapper_session_callee"),
        Index("ix_session_wrapper_session", "tenant_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    callee: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col(nullable=False)


# Tables carrying a tenant_id get FORCE RLS in the migration.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "app_user",
    "session",
    "run",
    "job",
    "run_event",
)

# Slice-2 additions, RLS-enabled by migration 0002 (kept separate so 0001's RLS
# loop stays exactly what shipped and the two migrations never double-apply).
FINDINGS_TABLES: tuple[str, ...] = (
    "finding",
    "finding_occurrence",
)

# Slice-3a addition, RLS-enabled by migration 0004.
TRIAGE_TABLES: tuple[str, ...] = ("finding_triage",)

# Slice-Y addition, RLS-enabled by migration 0005.
ASSET_TABLES: tuple[str, ...] = ("run_asset",)

# Shadow-API spec-diff addition, RLS-enabled by migration 0006.
SPEC_TABLES: tuple[str, ...] = ("session_spec", "finding_spec_status")

# REQ-C2 manual base-URL addition, RLS-enabled by migration 0007.
BASE_URL_TABLES: tuple[str, ...] = ("session_base_url",)

# REQ-C2 wrapper-teaching addition, RLS-enabled by migration 0008.
WRAPPER_TABLES: tuple[str, ...] = ("session_wrapper",)

# R6 engagement-tier addition, RLS-enabled by migration 0009.
ENGAGEMENT_TABLES: tuple[str, ...] = ("engagement",)
