"""Coordinator — the seam between the state machine and the queues.

Knows which queue each stage runs on, creates the job row + enqueues the
message, and decides what to enqueue next when a stage finishes. Keeping this in
one place means the API and the worker never hand-wire queues themselves.
"""

from __future__ import annotations

from typing import Any

from redis import Redis
from sqlalchemy import desc, select, update
from sqlalchemy.orm import Session

from recon import storage
from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import Job, Run
from recon.discover import queries as discover_queries
from recon.domain import AssetStatus, JobState, QueueName, RunStage, RunState
from recon.fetch import egress
from recon.observability import get_logger
from recon.queue import streams
from recon.runs import assets as run_assets
from recon.runs import queries as run_queries
from recon.runs import service
from recon.runs import state_machine as sm
from recon.runs.service import RunNotFound, RunView
from recon.sessions import service as sessions_service

log = get_logger("recon.runs.coordinator")

# Which queue carries each stage's work (REQ-Q1). Ingest/analyze/correlate all
# run on the CPU-bound analyze pool.
STAGE_QUEUE: dict[RunStage, QueueName] = {
    RunStage.DISCOVERING: QueueName.DISCOVER,
    RunStage.FETCHING: QueueName.FETCH,
    RunStage.INGESTING: QueueName.ANALYZE,
    RunStage.ANALYZING: QueueName.ANALYZE,
    RunStage.CORRELATING: QueueName.ANALYZE,
}


def create_stage_job(
    session: Session, *, tenant_id: str, run_id: str, stage: RunStage
) -> tuple[str, dict]:
    """Insert the stage's Job row using the CALLER's transaction and return
    ``(job_id, message)`` for a later :func:`publish_stage_job`.

    Split out of :func:`enqueue_stage` so a caller can commit the Job row in the
    SAME transaction as other run state (capture ``analyze/start`` seals the run's
    accumulator marker together with the Job insert, so the run can never be left
    sealed-but-jobless — DEBT D1). The Redis enqueue stays a separate post-commit
    step (see the outbox NOTE on :func:`publish_stage_job`)."""
    queue = STAGE_QUEUE[stage]
    max_attempts = get_settings().retry_max_attempts
    job = Job(
        tenant_id=tenant_id,
        run_id=run_id,
        queue=queue.value,
        stage=stage.value,
        state=JobState.QUEUED.value,
        max_attempts=max_attempts,
    )
    session.add(job)
    session.flush()
    message = {
        "job_id": str(job.id),
        "run_id": str(run_id),
        "tenant_id": tenant_id,
        "queue": queue.value,
        "stage": stage.value,
        "attempts": 0,
        "max_attempts": max_attempts,
    }
    return str(job.id), message


def publish_stage_job(redis: Redis, message: dict) -> None:
    """Put a job message (from :func:`create_stage_job`) on its stage's queue.

    NOTE (follow-up, REQ-A3): the job row commit and this Redis enqueue are two
    steps. A crash between them strands a QUEUED job with no stream message. The
    slice-2 transactional outbox (which REQ-A3 requires for findings) will cover
    job enqueue too; until then a QUEUED-job reconciler sweep is the stopgap.
    """
    queue = QueueName(message["queue"])
    streams.ensure_group(redis, queue)
    streams.enqueue(redis, queue, message)


def enqueue_stage(redis: Redis, *, tenant_id: str, run_id: str, stage: RunStage) -> str:
    """Create the job row (own transaction) and put its message on the queue."""
    with tenant_session(tenant_id) as session:
        job_id, message = create_stage_job(session, tenant_id=tenant_id, run_id=run_id, stage=stage)
    publish_stage_job(redis, message)
    return job_id


def start_run(
    redis: Redis,
    *,
    tenant_id: str,
    session_id: str,
    target: str | None = None,
    input_ref: str | None = None,
    crawl_mode: str | None = None,
    max_fetch_bytes: int | None = None,
    scan_suspected_secrets: bool | None = None,
) -> RunView:
    """Create a run (returns immediately) and enqueue its first stage.

    ``crawl_mode="capture"`` routes the DISCOVER stage to the CDP browser-capture
    path; default/NULL keeps the static katana crawl. ``max_fetch_bytes`` is an
    optional per-run fetch-cap override (edit-&-re-run), clamped at read time.
    ``scan_suspected_secrets`` (D33-B) opts this run into the low-confidence recall lane."""
    view = service.create_run(
        redis,
        tenant_id=tenant_id,
        session_id=session_id,
        target=target,
        input_ref=input_ref,
        crawl_mode=crawl_mode,
        max_fetch_bytes=max_fetch_bytes,
        scan_suspected_secrets=scan_suspected_secrets,
    )
    enqueue_stage(redis, tenant_id=tenant_id, run_id=view.id, stage=RunStage.DISCOVERING)
    return view


def start_run_with_input(
    redis: Redis,
    *,
    tenant_id: str,
    session_id: str,
    js_source: str | bytes,
    map_source: bytes | None = None,
    target: str | None = None,
    max_fetch_bytes: int | None = None,
    scan_suspected_secrets: bool | None = None,
) -> RunView:
    """Create a run, store its JS input (and optional source map) as blobs, point
    the run at them, then enqueue the first stage. The analyze stage reads the JS
    blob (REQ-D2) and, when present, recovers real source paths from the map.
    ``max_fetch_bytes`` is stored (inert for an upload — no fetch) for parity.
    ``scan_suspected_secrets`` (D33-B) opts this run into the low-confidence recall lane."""
    content = js_source.encode("utf-8") if isinstance(js_source, str) else js_source
    view = service.create_run(
        redis,
        tenant_id=tenant_id,
        session_id=session_id,
        target=target,
        max_fetch_bytes=max_fetch_bytes,
        scan_suspected_secrets=scan_suspected_secrets,
    )
    values: dict[str, str] = {"input_ref": storage.put_blob(tenant_id, view.id, "input", content)}
    if map_source:
        values["source_map_ref"] = storage.put_blob(tenant_id, view.id, "source_map", map_source)
    # Set the refs before enqueue so the analyze stage always sees them.
    with tenant_session(tenant_id) as session:
        session.execute(update(Run).where(Run.id == view.id).values(**values))
    enqueue_stage(redis, tenant_id=tenant_id, run_id=view.id, stage=RunStage.DISCOVERING)
    return view


# "Inherit this field from the source run" sentinel — distinct from a real None
# (crawl_mode None = static, target None = target-less), so edit-&-re-run can tell
# "not edited" from "edited to None".
_UNSET: Any = object()


class NoRunToRerun(Exception):
    """The run/session has nothing reproducible — no stored input blob and no target."""


class CaptureModeUnavailable(Exception):
    """A capture re-run needs RECON_ENABLE_CAPTURE_MODE + a target to open (→ 400)."""


def edit_and_rerun(
    redis: Redis,
    *,
    tenant_id: str,
    run_id: str,
    target: Any = _UNSET,
    capture: Any = _UNSET,
    scope_hosts: Any = _UNSET,
    max_fetch_bytes: Any = _UNSET,
    scan_suspected_secrets: Any = _UNSET,
    authorized_by: str | None = None,
) -> RunView:
    """Clone a SPECIFIC run's config into a NEW run, applying the operator's edits
    (edit-&-re-run). Any ``_UNSET`` field is inherited from the source run.

    The source run is NEVER mutated — runs are immutable snapshots, so a re-run is a
    fresh run (D1). The source config is read under tenant RLS; a miss is
    :class:`RunNotFound` (→ 404), the gate that closes the cross-tenant IDOR (MF4).

    ``capture`` (bool) resolves to ``crawl_mode``; ``_UNSET`` inherits it, which also
    fixes the old re-run's silent crawl_mode drop (D7). Session handling is delegated
    to :func:`_resolve_rerun_session` (fork-on-scope-change with a fresh ack)."""
    cfg = run_queries.get_run_config(tenant_id, run_id)
    if cfg is None:
        raise RunNotFound(f"run {run_id} not found")

    is_upload = cfg.is_upload
    eff_target = cfg.target if target is _UNSET else target
    eff_crawl_mode = cfg.crawl_mode if capture is _UNSET else ("capture" if capture else None)
    eff_cap = cfg.max_fetch_bytes if max_fetch_bytes is _UNSET else max_fetch_bytes
    eff_scan_suspected = (
        cfg.scan_suspected_secrets if scan_suspected_secrets is _UNSET else scan_suspected_secrets
    )

    # MF6: a capture re-run re-checks the kill-switch + target (either may have changed
    # since the source run) and fails with a clean 400, not a worker DLQ. Only for a
    # crawl source — an upload re-analyzes stored bytes and ignores crawl_mode.
    if not is_upload and eff_crawl_mode == "capture":
        settings = get_settings()
        if not settings.enable_capture_mode:
            raise CaptureModeUnavailable("runtime capture mode is disabled")
        if not eff_target:
            raise CaptureModeUnavailable("runtime capture requires a target URL to open")

    session_id = _resolve_rerun_session(
        tenant_id,
        cfg,
        eff_target=eff_target,
        scope_hosts=scope_hosts,
        is_upload=is_upload,
        authorized_by=authorized_by,
    )

    if is_upload:
        # Re-analyze the SAME stored bytes (fresh blob copy + any source map) so a new
        # extraction is picked up. target is the REQ-C2 base-URL hint; crawl_mode is
        # inert (no fetch). cap is stored for parity.
        js_source = storage.get_blob(cfg.input_ref) if cfg.input_ref else b""
        map_source = storage.get_blob(cfg.source_map_ref) if cfg.source_map_ref else None
        return start_run_with_input(
            redis,
            tenant_id=tenant_id,
            session_id=session_id,
            js_source=js_source,
            map_source=map_source,
            target=eff_target,
            max_fetch_bytes=eff_cap,
            scan_suspected_secrets=eff_scan_suspected,
        )
    if eff_target:
        return start_run(
            redis,
            tenant_id=tenant_id,
            session_id=session_id,
            target=eff_target,
            crawl_mode=eff_crawl_mode,
            max_fetch_bytes=eff_cap,
            scan_suspected_secrets=eff_scan_suspected,
        )
    raise NoRunToRerun("source run has neither stored input nor a target to re-run")


def _scope_set(hosts: list[str]) -> set[str]:
    """Normalized set for a change-comparison — ignores order + case/trailing-dot/blank
    and a leading ``*.`` wildcard (mirroring egress.normalize_scope_entry), so
    re-submitting the PREFILLED scope unchanged is not seen as a change (which would
    force an unnecessary re-attestation)."""
    return {h.strip().lower().removeprefix("*.").rstrip(".") for h in hosts if h.strip()}


def _resolve_rerun_session(
    tenant_id: str,
    cfg: run_queries.RunConfigView,
    *,
    eff_target: str | None,
    scope_hosts: Any,
    is_upload: bool,
    authorized_by: str | None,
) -> str:
    """The session the re-run lands in: reuse the source session, or fork a fresh one.

    Fork (MF1/MF3) when the scope actually CHANGED (by value, not mere presence — the UI
    re-sends the prefilled scope, which must NOT force a fork), or the edited CRAWL target
    left the source scope (an upload never fetches its target, so its target never forces
    a fork). A fork mints a NEW session via the normal create path — a FRESH
    ``authorized_by`` (never the source ack) + the same fail-closed scope validation — so
    a widened scope is re-attested for authorization (REQ-P2/P3). The reuse path is
    READ-ONLY w.r.t. the session: scope is write-once, which keeps reuse safe (MF5)."""
    allow_local = get_settings().allow_local_egress
    # `scope_hosts is not None` tolerates an explicit JSON ``null`` (not FE-sent, but a
    # crafted body would otherwise reach _scope_set(None) and 500) — treat it as "unset".
    scope_changed = (
        scope_hosts is not _UNSET
        and scope_hosts is not None
        and _scope_set(scope_hosts) != _scope_set(cfg.scope_hosts)
    )
    target_left_scope = (
        not is_upload
        and bool(eff_target)
        and not egress.host_in_scope(
            egress.host_of(eff_target), cfg.scope_hosts, allow_local=allow_local
        )
    )
    if not (scope_changed or target_left_scope):
        return cfg.session_id
    # When only the target moved out of scope (scope unchanged), seed the new session's
    # scope from the new target (scope_hosts=[]), exactly as a New Recon does.
    fork_scope = scope_hosts if scope_changed else []
    new_session = sessions_service.create_session(
        tenant_id,
        name=cfg.session_name,
        scope_hosts=fork_scope,
        authorized_by=authorized_by or "",  # blank → AuthorizationRequired (fresh ack, MF1)
        engagement_id=cfg.engagement_id,
        target=eff_target,
    )
    return new_session.id


def rerun(redis: Redis, *, tenant_id: str, session_id: str) -> RunView:
    """Re-run a session's most recent run VERBATIM (R6 re-run): a new run inheriting the
    latest run's config, delegated to :func:`edit_and_rerun` with no edits so it now
    carries ``crawl_mode`` (D7 — the old path silently dropped it) via the one clone
    path. Raises :class:`NoRunToRerun` when the session has no reproducible run."""
    with tenant_session(tenant_id) as session:
        latest = session.scalars(
            select(Run)
            .where(Run.session_id == str(session_id))
            .order_by(desc(Run.created_at))
            .limit(1)
        ).first()
        latest_id = str(latest.id) if latest is not None else None
    if latest_id is None:
        raise NoRunToRerun("session has no run to re-run")
    return edit_and_rerun(redis, tenant_id=tenant_id, run_id=latest_id)


def advance(redis: Redis, *, tenant_id: str, run_id: str, completed: RunStage) -> None:
    """A stage finished — enqueue the next one, or finalize the run."""
    nxt = sm.next_stage(completed)
    if nxt is not None:
        enqueue_stage(redis, tenant_id=tenant_id, run_id=run_id, stage=nxt)
        return
    try:
        to_state, completeness = _finalize_state(tenant_id, run_id)
        service.transition(
            redis,
            tenant_id=tenant_id,
            run_id=run_id,
            to_state=to_state,
            extra_values={"completeness": completeness},
        )

        # Fresh finalize only: a duplicate/concurrent delivery raises out of
        # `transition` above and lands in the `except` below, so a re-delivered
        # "stage finished" message never reaches this point and can't re-run
        # classification. Imported lazily (not at module load) to avoid a
        # recon.spec <-> recon.runs import cycle. Best-effort: a continuous
        # rescan (REQ-D5 gate N3) should keep new findings classified against
        # an already-attached spec without a manual re-POST, but classification
        # must never fail or roll back a run's finalize, so any error here is
        # logged and swallowed rather than raised.
        from recon.spec import service as spec_service

        try:
            spec_service.reclassify_run(tenant_id, run_id)
        except Exception:  # noqa: BLE001 - best-effort, must never fail finalize
            log.exception("runs.reclassify_failed", run_id=run_id, tenant_id=tenant_id)
    except (service.TransitionConflict, sm.InvalidTransition):
        # Already finalized by a concurrent/duplicate delivery — idempotent.
        pass


def _finalize_state(tenant_id: str, run_id: str) -> tuple[RunState, dict]:
    """DONE vs PARTIAL from per-asset status (REQ-D5).

    The discriminator for "is this a crawl run" is the presence of a
    discover.assets event, NOT the run_asset row count — a crawl that timed out
    before finding any assets has zero rows but must still finalize PARTIAL.
    Legacy single-asset runs (no discover.assets event) keep the historical
    hardcoded DONE.
    """
    event = discover_queries.latest_assets_event(tenant_id, run_id)
    if event is None:
        return RunState.DONE, {"fetch_ok": True, "analyze_ok": True}
    rows = run_assets.list_for_run(tenant_id, run_id)
    crawl_ok = event.get("status") == "ok"
    fetch_ok = crawl_ok and all(a.fetch_status == AssetStatus.OK.value for a in rows)
    analyze_ok = fetch_ok and all(a.analyze_status == AssetStatus.OK.value for a in rows)
    to_state = RunState.DONE if (fetch_ok and analyze_ok) else RunState.PARTIAL
    return to_state, {"fetch_ok": fetch_ok, "analyze_ok": analyze_ok}


def resume_run(redis: Redis, *, tenant_id: str, run_id: str) -> RunView:
    """Resume a paused run and re-enqueue the stage it left off at."""
    view, stage = service.resume(redis, tenant_id=tenant_id, run_id=run_id)
    enqueue_stage(redis, tenant_id=tenant_id, run_id=run_id, stage=stage)
    return view
