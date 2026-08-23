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
    """The ordered active stages of a run. The threat-model pass is planned
    (on-demand, tracked separately — see the SOON workspace nav), so it is not in
    this core sequence."""

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


class FindingType(StrEnum):
    """The content-addressed finding kinds (REQ-D3). Extended in later slices
    (source_map, post_message, storage) as new extractors land."""

    ENDPOINT = "endpoint"
    SECRET = "secret"
    PARAM = "param"
    # A network sink we detected but whose URL isn't statically resolvable — the
    # "unconfirmed" lane (Tier 4). A DISTINCT type (not an attribute on ENDPOINT) so
    # every `type == 'endpoint'` read model — OpenAPI export, shadow classification,
    # headline counts — excludes it automatically, with no per-consumer filter.
    ENDPOINT_UNRESOLVED = "endpoint_unresolved"
    # A verb-method call (`.get/.post/…`) on an unrecognised but HTTP-client-shaped
    # receiver — a SUSPECTED custom/untaught client (Tier 5, "generic call"). Also a
    # DISTINCT type, for the same auto-exclusion, and — because it is only SUSPECTED,
    # not a detected sink — it never moves the REQ-C2 coverage counters. A separate
    # type (not a shared one with an attribute) keeps its provenance in finding
    # identity, so it stays filterable and never collides with a Tier-4 skeleton.
    ENDPOINT_GENERIC = "endpoint_generic"
    # A client-side navigation target (Phase 2) — an `href`/`src`/`action` value, a nav
    # sink (`location.assign`, `history.pushState`, `router.push`), or an off-sink
    # absolute-URL literal — as opposed to a backend API call. A DISTINCT type so it is
    # its own category: excluded from every `type == 'endpoint'` read model automatically,
    # and never counted in the API-surface coverage numbers.
    PAGE_ROUTE = "page_route"


class AssetStatus(StrEnum):
    """Per-asset fetch/analyze outcome on a run_asset row (Slice Y)."""

    PENDING = "pending"
    OK = "ok"
    FAILED = "failed"


class BaseUrlRuleKind(StrEnum):
    """How a manual base-URL rule selects the findings it re-resolves (REQ-C2)."""

    PREFIX = "prefix"  # matches ops whose path starts with path_prefix (segment-wise)
    SELECTION = "selection"  # matches ops whose endpoint finding_hash is in finding_hashes


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
