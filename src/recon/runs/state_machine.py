"""The run state machine (REQ-A2), pure and side-effect-free.

This module only answers "is this transition legal?" and "what stage comes
next?". Persisting a transition atomically and emitting its event is the job of
:mod:`recon.runs.service`.
"""

from __future__ import annotations

from recon.domain import (
    ACTIVE_STATES,
    STAGE_ORDER,
    TERMINAL_STATES,
    RunStage,
    RunState,
)


class InvalidTransition(Exception):
    """Raised when a transition is not permitted by the state machine."""


def _build_allowed() -> dict[RunState, frozenset[RunState]]:
    linear: dict[RunState, set[RunState]] = {
        RunState.QUEUED: {RunState.DISCOVERING},
        RunState.DISCOVERING: {RunState.FETCHING},
        RunState.FETCHING: {RunState.INGESTING},
        RunState.INGESTING: {RunState.ANALYZING},
        RunState.ANALYZING: {RunState.CORRELATING},
        RunState.CORRELATING: {RunState.DONE, RunState.PARTIAL},
    }
    allowed: dict[RunState, set[RunState]] = {s: set() for s in RunState}
    for frm, tos in linear.items():
        allowed[frm] |= tos

    # Any active stage can pause, fail, be cancelled, or end partial.
    for state in ACTIVE_STATES:
        allowed[state] |= {
            RunState.PAUSED,
            RunState.FAILED,
            RunState.CANCELLED,
            RunState.PARTIAL,
        }

    # A queued run can be paused (held) or cancelled before it starts.
    allowed[RunState.QUEUED] |= {RunState.PAUSED, RunState.CANCELLED}

    # Resume returns to whichever active stage we paused from; a paused run may
    # also be cancelled, or fail (e.g. a queued retry exhausts while paused).
    allowed[RunState.PAUSED] |= set(ACTIVE_STATES) | {
        RunState.CANCELLED,
        RunState.FAILED,
    }

    return {s: frozenset(tos) for s, tos in allowed.items()}


ALLOWED: dict[RunState, frozenset[RunState]] = _build_allowed()

INITIAL_STATE: RunState = RunState.QUEUED


def is_terminal(state: RunState) -> bool:
    return state in TERMINAL_STATES


def can_transition(frm: RunState, to: RunState) -> bool:
    return to in ALLOWED.get(frm, frozenset())


def assert_transition(frm: RunState, to: RunState) -> None:
    if not can_transition(frm, to):
        raise InvalidTransition(f"{frm.value} -> {to.value} is not a legal transition")


def next_stage(current: RunStage | None) -> RunStage | None:
    """The stage after ``current`` in execution order, or None past the end.

    ``None`` input yields the first stage (the run has not started a stage yet).
    """
    if current is None:
        return STAGE_ORDER[0]
    idx = STAGE_ORDER.index(current)
    if idx + 1 < len(STAGE_ORDER):
        return STAGE_ORDER[idx + 1]
    return None


def state_for_stage(stage: RunStage) -> RunState:
    """The run state that corresponds to being in a given active stage."""
    return RunState(stage.value)
