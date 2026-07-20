"""Shared domain vocabulary — the enums every layer agrees on.

Kept dependency-free so both the persistence layer (db.models) and the feature
logic (runs.state_machine, queue.streams) can import it without cycles.
"""

from __future__ import annotations

from enum import StrEnum


class QueueName(StrEnum):
    """One queue per work class (REQ-Q1)."""

    DISCOVER = "discover"
    FETCH = "fetch"
    ANALYZE = "analyze"
    LLM = "llm"
    PROBE = "probe"
    REPORT = "report"


class RunStage(StrEnum):
    """The ordered active stages of a run. The threat-model (MODEL) pass is
    on-demand and tracked separately, so it is not in this core sequence."""

    DISCOVERING = "discovering"
    FETCHING = "fetching"
    INGESTING = "ingesting"
    ANALYZING = "analyzing"
    CORRELATING = "correlating"


class RunState(StrEnum):
    """Persisted run state machine (REQ-A2), plus the control/terminal states.

    While a run is active its state equals the current stage. ``PAUSED`` is the
    slice-1 addition agreed for run-level pause (resumable); ``CANCELLED`` is the
    terminal outcome of REQ-A4 cancellation.
    """

    QUEUED = "queued"
    DISCOVERING = "discovering"
    FETCHING = "fetching"
    INGESTING = "ingesting"
    ANALYZING = "analyzing"
    CORRELATING = "correlating"
    PAUSED = "paused"
    DONE = "done"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"  # exhausted retries -> DLQ (REQ-Q2)
    CANCELLED = "cancelled"


# The stages in execution order — used to know what comes next and to resume.
STAGE_ORDER: tuple[RunStage, ...] = (
    RunStage.DISCOVERING,
    RunStage.FETCHING,
    RunStage.INGESTING,
    RunStage.ANALYZING,
    RunStage.CORRELATING,
)

ACTIVE_STATES: frozenset[RunState] = frozenset(
    {
        RunState.DISCOVERING,
        RunState.FETCHING,
        RunState.INGESTING,
        RunState.ANALYZING,
        RunState.CORRELATING,
    }
)

TERMINAL_STATES: frozenset[RunState] = frozenset(
    {RunState.DONE, RunState.PARTIAL, RunState.FAILED, RunState.CANCELLED}
)
