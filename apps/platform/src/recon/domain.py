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
    """The content-addressed finding kinds (REQ-D3): the API-surface lanes
    (``endpoint`` + the suspected ``endpoint_unresolved`` / ``endpoint_generic``),
    the client-navigation ``page_route`` lane, the ``graphql`` operation lane, plus
    ``secret`` and ``param``."""

    ENDPOINT = "endpoint"
    SECRET = "secret"
    PARAM = "param"
    # An OPT-IN, low-confidence secret sighting (D33-B) — the recall lane to SECRET's
    # precision lane. Emitted only when a run opts into the `--confidence low` Kingfisher
    # sweep (~50% FP by design), so it is a DISTINCT type, mirroring the endpoint
    # `*_unresolved`/`*_generic` lanes: it reuses SECRET's reveal/redaction machinery
    # (offsets, provider:sha256 value — REQ-S2), but MUST stay OUT of the precision-first
    # `secret` headline/coverage COUNT, and — because `finding_hash` includes the type, so
    # a hash-set diff would otherwise fabricate add/remove when the opt-in toggles — OUT of
    # the REQ-D5 removal diff (that diff is scoped to `secret` + confirmed endpoint lanes;
    # the medium/high→`secret` set is byte-identical opted-in-or-not, so a secret-scoped
    # diff is provably toggle-safe). See DEBT D33.
    SECRET_SUSPECTED = "secret_suspected"
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
    # A cleartext internal-IP literal (info-disclosure) — the first member of a NON-secret
    # info-disclosure family. Unlike SECRET, the value is plainly visible in the bundle, so
    # it is stored + shown in CLEARTEXT (its raw dotted-quad is `finding.value`, never
    # sha256-hashed into identity, never server-redacted, never reveal-gated) and it is
    # counted SEPARATELY from secrets (it never inflates the `secrets` count). Like every
    # non-`secret`/non-confirmed-endpoint lane, it is kept OUT of the future REQ-D5 removal
    # diff (that diff is scoped to `secret` + confirmed endpoint lanes). A DISTINCT type, for
    # the same auto-exclusion + provenance-in-identity rationale as the other lanes above.
    INTERNAL_IP = "internal_ip"
    # A GraphQL operation (query/mutation/subscription) or fragment definition located in a
    # bundle. A DISTINCT type: a GraphQL op is not an HTTP endpoint (it rides one POST to a
    # `/graphql` route), so it is excluded from every `type == 'endpoint'` read model and the
    # REQ-C2 coverage counters automatically, and never collides with an endpoint on one
    # `finding_hash`. Surfaced as first-class located findings + a dedicated workspace tab.
    GRAPHQL = "graphql"
    # A SUSPECTED endpoint promoted from a generic-client call, or an unresolved sink whose
    # collapsed URL still carries a real path (>=1 static path segment) — normalized like a
    # confirmed ENDPOINT (host stripped to the occurrence, path templated, `${...}`/`:holder`
    # placeholders kept). A DISTINCT type, NOT a `confidence` attribute on ENDPOINT, precisely
    # so the fail-closed default holds: an unaudited `type == 'endpoint'` read model still
    # EXCLUDES it. The "total endpoints found" headline, the OpenAPI export, probe, the
    # threat-model feed, and classify/correlate opt IN by unioning {endpoint, endpoint_suspected};
    # the confirmed-only consumers (the `hosts` confirmed inventory, the REQ-C2 `coverage_pct`
    # attribution recall, and the future REQ-D5 removal diff) deliberately stay endpoint-only.
    # Same distinct-type-per-confidence-tier pattern as SECRET_SUSPECTED.
    ENDPOINT_SUSPECTED = "endpoint_suspected"


# The finding types that roll up into the "total endpoints found" headline — the confirmed
# `endpoint` (API) lane PLUS the promoted valid-path `endpoint_suspected` lane. The union is
# opted INTO explicitly at the total consumers (the run's endpoint count, the OpenAPI/probe
# reconstruction, and capture correlation); the confirmed-only consumers (the `hosts` confirmed
# inventory, the REQ-C2 `coverage_pct` attribution recall, and the future REQ-D5 removal diff)
# deliberately use FindingType.ENDPOINT alone so the fail-closed default holds.
TOTAL_ENDPOINT_TYPES: tuple[FindingType, ...] = (
    FindingType.ENDPOINT,
    FindingType.ENDPOINT_SUSPECTED,
)
TOTAL_ENDPOINT_TYPE_VALUES: frozenset[str] = frozenset(t.value for t in TOTAL_ENDPOINT_TYPES)


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
