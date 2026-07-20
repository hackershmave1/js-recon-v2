"""Colocated tests for the pure run state machine (REQ-A2, REQ-A4)."""

from __future__ import annotations

import pytest

from recon.domain import ACTIVE_STATES, STAGE_ORDER, RunStage, RunState
from recon.runs import state_machine as sm


def test_happy_path_is_linear():
    chain = [
        RunState.QUEUED,
        RunState.DISCOVERING,
        RunState.FETCHING,
        RunState.INGESTING,
        RunState.ANALYZING,
        RunState.CORRELATING,
        RunState.DONE,
    ]
    for frm, to in zip(chain, chain[1:]):
        assert sm.can_transition(frm, to), f"{frm} -> {to} should be allowed"


def test_cannot_skip_stages():
    assert not sm.can_transition(RunState.QUEUED, RunState.ANALYZING)
    assert not sm.can_transition(RunState.DISCOVERING, RunState.CORRELATING)


def test_any_active_stage_can_pause_and_resume():
    for state in ACTIVE_STATES:
        assert sm.can_transition(state, RunState.PAUSED)
    # Resume goes back to any active stage.
    for state in ACTIVE_STATES:
        assert sm.can_transition(RunState.PAUSED, state)


def test_any_active_stage_can_fail_or_cancel():
    for state in ACTIVE_STATES:
        assert sm.can_transition(state, RunState.FAILED)
        assert sm.can_transition(state, RunState.CANCELLED)


def test_queued_can_pause_or_cancel_before_starting():
    assert sm.can_transition(RunState.QUEUED, RunState.PAUSED)
    assert sm.can_transition(RunState.QUEUED, RunState.CANCELLED)


def test_paused_run_can_fail_or_cancel_or_resume():
    # A retry can exhaust while paused, so PAUSED->FAILED must be legal.
    assert sm.can_transition(RunState.PAUSED, RunState.FAILED)
    assert sm.can_transition(RunState.PAUSED, RunState.CANCELLED)
    assert sm.can_transition(RunState.PAUSED, RunState.DISCOVERING)


def test_terminal_states_have_no_exits():
    for state in (RunState.DONE, RunState.PARTIAL, RunState.FAILED, RunState.CANCELLED):
        assert sm.is_terminal(state)
        assert sm.ALLOWED[state] == frozenset()


def test_assert_transition_raises_on_illegal():
    with pytest.raises(sm.InvalidTransition):
        sm.assert_transition(RunState.DONE, RunState.DISCOVERING)


def test_next_stage_walks_the_pipeline():
    assert sm.next_stage(None) is RunStage.DISCOVERING
    assert sm.next_stage(RunStage.DISCOVERING) is RunStage.FETCHING
    assert sm.next_stage(RunStage.CORRELATING) is None


def test_next_stage_covers_every_stage_exactly_once():
    seen = []
    stage = sm.next_stage(None)
    while stage is not None:
        seen.append(stage)
        stage = sm.next_stage(stage)
    assert seen == list(STAGE_ORDER)


def test_state_for_stage_maps_cleanly():
    for stage in STAGE_ORDER:
        assert sm.state_for_stage(stage) == RunState(stage.value)
