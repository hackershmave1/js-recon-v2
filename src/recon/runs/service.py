"""Run lifecycle service — atomic, event-emitting state changes (REQ-A2, REQ-A4).

Every state change is a single transaction: a *guarded* UPDATE (it only fires if
the row is still in the state we expect, so two racing transitions can't both
win) plus the durable event insert, committed together. The Redis fast-path
publish happens right after the commit.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from redis import Redis
from sqlalchemy import update
from sqlalchemy.orm import Session

from recon.db.base import tenant_session
from recon.db.models import Run
from recon.domain import TERMINAL_STATES, RunStage, RunState
from recon.events.log import RecordedEvent, publish, record_event
from recon.runs import state_machine as sm


class RunNotFound(Exception):
    pass


class TransitionConflict(Exception):
    """The run was not in the expected state — another actor moved it first."""


@dataclass(frozen=True)
class RunView:
    id: str
    tenant_id: str
    session_id: str
    state: str
    stage: str | None
    pause_requested: bool
    cancel_requested: bool
    resumed_from_stage: str | None
    completeness: dict
    target: str | None
    input_ref: str | None


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _snapshot(run: Run) -> RunView:
    return RunView(
        id=str(run.id),
        tenant_id=str(run.tenant_id),
        session_id=str(run.session_id),
        state=run.state,
        stage=run.stage,
        pause_requested=run.pause_requested,
        cancel_requested=run.cancel_requested,
        resumed_from_stage=run.resumed_from_stage,
        completeness=dict(run.completeness or {}),
        target=run.target,
        input_ref=run.input_ref,
    )


def _apply_transition(
    session: Session,
    run: Run,
    to_state: RunState,
    *,
    tenant_id: str,
    stage: RunStage | None = None,
    extra_values: dict[str, Any] | None = None,
    event_type: str = "run.transition",
    event_payload_extra: dict[str, Any] | None = None,
) -> RecordedEvent:
    frm = RunState(run.state)
    sm.assert_transition(frm, to_state)

    values: dict[str, Any] = {"state": to_state.value}
    if stage is not None:
        values["stage"] = stage.value
    if to_state in TERMINAL_STATES:
        values["stage"] = None
        values["ended_at"] = _utcnow()
    if run.started_at is None and to_state in sm.ACTIVE_STATES:
        values["started_at"] = _utcnow()
    if extra_values:
        values.update(extra_values)

    # Guarded update: only transition if still in the state we read (REQ-A2).
    result = session.execute(
        update(Run)
        .where(Run.id == run.id, Run.state == frm.value)
        .values(**values)
    )
    if result.rowcount != 1:
        raise TransitionConflict(
            f"run {run.id} was not in expected state {frm.value}"
        )

    payload: dict[str, Any] = {"from": frm.value, "to": to_state.value}
    if event_payload_extra:
        payload.update(event_payload_extra)
    event = record_event(
        session,
        tenant_id=tenant_id,
        run_id=str(run.id),
        event_type=event_type,
        payload=payload,
    )
    session.refresh(run)
    return event


def create_run(
    redis: Redis,
    *,
    tenant_id: str,
    session_id: str,
    target: str | None = None,
    input_ref: str | None = None,
) -> RunView:
    with tenant_session(tenant_id) as session:
        run = Run(
            tenant_id=tenant_id,
            session_id=session_id,
            state=RunState.QUEUED.value,
            target=target,
            input_ref=input_ref,
        )
        session.add(run)
        session.flush()
        event = record_event(
            session,
            tenant_id=tenant_id,
            run_id=str(run.id),
            event_type="run.created",
            payload={"target": target},
        )
        snapshot = _snapshot(run)
    publish(redis, event)
    return snapshot


def transition(
    redis: Redis,
    *,
    tenant_id: str,
    run_id: str,
    to_state: RunState,
    stage: RunStage | None = None,
    extra_values: dict[str, Any] | None = None,
) -> RunView:
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            raise RunNotFound(run_id)
        event = _apply_transition(
            session,
            run,
            to_state,
            tenant_id=tenant_id,
            stage=stage,
            extra_values=extra_values,
        )
        snapshot = _snapshot(run)
    publish(redis, event)
    return snapshot


def request_pause(redis: Redis, *, tenant_id: str, run_id: str) -> RunView:
    """Signal a pause. Runs mid-stage flip when the worker next checkpoints; a
    still-queued run is paused immediately."""
    events: list[RecordedEvent] = []
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            raise RunNotFound(run_id)
        if sm.is_terminal(RunState(run.state)):
            raise TransitionConflict("cannot pause a terminal run")
        session.execute(
            update(Run).where(Run.id == run.id).values(pause_requested=True)
        )
        session.refresh(run)
        events.append(
            record_event(
                session,
                tenant_id=tenant_id,
                run_id=str(run.id),
                event_type="run.pause_requested",
                payload={},
            )
        )
        if RunState(run.state) == RunState.QUEUED:
            events.append(
                _apply_transition(session, run, RunState.PAUSED, tenant_id=tenant_id)
            )
        snapshot = _snapshot(run)
    for event in events:
        publish(redis, event)
    return snapshot


def request_cancel(redis: Redis, *, tenant_id: str, run_id: str) -> RunView:
    """Signal a cancel (REQ-A4). Active runs cancel at the worker's next
    checkpoint; queued/paused runs cancel immediately."""
    events: list[RecordedEvent] = []
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            raise RunNotFound(run_id)
        if sm.is_terminal(RunState(run.state)):
            return _snapshot(run)
        session.execute(
            update(Run).where(Run.id == run.id).values(cancel_requested=True)
        )
        session.refresh(run)
        events.append(
            record_event(
                session,
                tenant_id=tenant_id,
                run_id=str(run.id),
                event_type="run.cancel_requested",
                payload={},
            )
        )
        if RunState(run.state) in (RunState.QUEUED, RunState.PAUSED):
            events.append(
                _apply_transition(session, run, RunState.CANCELLED, tenant_id=tenant_id)
            )
        snapshot = _snapshot(run)
    for event in events:
        publish(redis, event)
    return snapshot


def resume(redis: Redis, *, tenant_id: str, run_id: str) -> tuple[RunView, RunStage]:
    """Resume a paused run to the stage it left (or the first stage). Returns the
    new view and the stage the caller should re-enqueue work for."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            raise RunNotFound(run_id)
        if RunState(run.state) != RunState.PAUSED:
            raise TransitionConflict("only a paused run can be resumed")
        target_stage = (
            RunStage(run.resumed_from_stage)
            if run.resumed_from_stage
            else RunStage.DISCOVERING
        )
        event = _apply_transition(
            session,
            run,
            sm.state_for_stage(target_stage),
            tenant_id=tenant_id,
            stage=target_stage,
            extra_values={"pause_requested": False},
        )
        snapshot = _snapshot(run)
    publish(redis, event)
    return snapshot, target_stage
