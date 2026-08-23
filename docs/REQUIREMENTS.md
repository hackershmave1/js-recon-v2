# Requirements

The canonical, traceable requirement set the platform is built against — **41 `REQ-*`
IDs** (plus a later `REQ-CE*` crawl-enhancements addendum, below), each with a subsystem and a
**MUST / SHOULD** priority. These IDs are the grounding
the [Architecture Decision Records](adr/README.md) cite (e.g. ADR-0001 → REQ-Q1/Q2/R2/R3,
ADR-0005 → REQ-P2): an ADR names the requirement that drove the decision, and this file is
where that requirement's exact wording lives.

Requirements are stated as capabilities/invariants, not an implementation — "the shape is
the requirement," and the recommended stack is a candidate set (see the bottom section).
Bracketed `[RT-nn]` tags mark points hardened during the design's red-team pass.

## Async pipeline

| ID | Priority | Requirement |
|---|---|---|
| REQ-A1 | MUST | No request thread performs crawl, fetch, AST parse, LLM, or probe work; the API enqueues a job and returns a run id in <200ms. |
| REQ-A2 | MUST | A recon run is a persisted state machine (queued→discovering→fetching→ingesting→analyzing→correlating→done/failed/partial); transitions are atomic and event-emitting. |
| REQ-A3 | MUST | Finding writes are exactly-once via a transactional outbox: a stage stages its output in one transaction keyed by a stable finding-hash, commits atomically, and downstream fan-out reads only committed rows — so a partial commit + retry can never double-write. [RT-04] |
| REQ-A4 | SHOULD | A run can be cancelled; in-flight jobs observe a cancel flag at safe checkpoints and stop enqueuing downstream work. |

## Collect

| ID | Priority | Requirement |
|---|---|---|
| REQ-C1 | SHOULD | Archive collection (Wayback/gau) is scoped to the locked engagement hosts; archive-only hosts discovered off-scope are listed but never live-fetched or probed without extending the scope lock. [RT-14] |
| REQ-C2 | SHOULD | Extraction surfaces a per-file un-attributed-call coverage counter (honesty is a MUST); completeness is explicitly NOT guaranteed. Custom wrappers are taught by mapping a call shape; cross-file base URLs are resolved via a manual set-base-URL that re-resolves dependents. [RT-10] |
| REQ-C3 | SHOULD | When a reconstructed route's host or base URL is an unresolved runtime value (e.g. a minified `apiHost` binding), a capture run MAY recover the concrete host by correlating the browser's actually-issued request URLs (observed via CDP `Network.requestWillBeSent` during capture) against the static request template on shared constant segments; the observed URL is recorded as ground-truth runtime evidence on the finding, and its host populates the REQ-C2 base-URL host-gate so reconstruct/spec surface the real URL. Runtime-observed URLs are a data-recovery output for labeling reconstructed routes only — they never derive or widen egress scope (REQ-P2 holds), and completeness is not guaranteed (a route the interaction driver never triggers stays unresolved). |

## Queues & concurrency

| ID | Priority | Requirement |
|---|---|---|
| REQ-Q1 | MUST | Separate queues per work class (discover, fetch, analyze, llm, probe, report) each with independent concurrency and rate limits. |
| REQ-Q2 | MUST | Bounded retries with exponential backoff + jitter; exhausted messages land in a per-queue DLQ with the original payload and error. |
| REQ-Q3 | MUST | Per-host fetch rate limiting so a single target is never hammered; respects robots/backoff and a global politeness budget. |
| REQ-Q4 | SHOULD | Priority lanes: interactive actions (single-endpoint probe, re-analyze one file) preempt bulk batch runs. |
| REQ-Q5 | SHOULD | Headless crawl (Chrome) runs in a dedicated, RAM-bounded concurrency lane (N browser slots, queued) decoupled from run concurrency; the per-worker memory budget is a stated NFR. [RT-08] |

## Realtime & progress

| ID | Priority | Requirement |
|---|---|---|
| REQ-R1 | MUST | Each job publishes a typed progress record {state, stage, done, total, eta, heartbeat_at}; persisted and query-able. |
| REQ-R2 | MUST | Progress is backed by a durable per-run event log (Redis Streams / append-only table); SSE carries a Last-Event-ID and replays from the last acked offset on reconnect, so a brief disconnect never loses events. Fire-and-forget pub/sub is only an optional fast-path. Interval polling of GET /runs/{id}/status remains the fallback. [RT-05] |
| REQ-R3 | MUST | A heartbeat distinguishes "running slowly" from "stalled"; a job with no heartbeat past threshold is surfaced as stalled, not running. |
| REQ-R4 | SHOULD | Status endpoint supports conditional requests (ETag / If-None-Match) so polling is cheap and returns 304 when unchanged. |

## Data stores

| ID | Priority | Requirement |
|---|---|---|
| REQ-D1 | MUST | Relational store (Postgres) holds sessions, runs, findings, endpoints, params, triage state, users; migrations managed (Alembic). |
| REQ-D2 | MUST | Large blobs (raw JS, source maps, reconstructed sources, reports) live in object storage (S3/MinIO), referenced by key — never in a row. |
| REQ-D3 | MUST | Findings are content-addressed over stable fields only (identity-version + type + value), excluding volatile per-sighting detail (host, source path, col/evidence), so a retry — or the same finding seen in another source file — yields the same hash. Distinct sightings are kept as occurrences (never silently merged away). This hash keys the exactly-once outbox write (REQ-A3). [RT-04] |
| REQ-D4 | SHOULD | Vector store (pgvector/Qdrant) grounds the threat modeler; retrieved chunks carry provenance + recency weighting, are cited distinctly from live evidence, and stale context is down-weighted or flagged so a wrong prior decision cannot pose as authoritative. [RT-16] |
| REQ-D5 | SHOULD | Runs are immutable snapshots; diffing is partial-aware — assets not observed in a partial run are marked "unknown," never "removed." Only complete-vs-complete runs may assert removals. [RT-13] |
| REQ-D6 | SHOULD | Data-lifecycle policy: snapshots older than N days roll up to deltas against a periodic baseline and raw blobs age out to cold storage, so continuous monitoring does not grow storage without bound. [RT-15] |

## LLM orchestration

| ID | Priority | Requirement |
|---|---|---|
| REQ-L1 | MUST | Provider config (keys, model routing, limits) is user-supplied at RUNTIME via the API and read by workers per-run — no server restart to change it. Secrets are envelope-encrypted (KMS) or held by reference in a vault, never a plaintext row and never logged/returned; non-secret config is plain rows. Calls are made by the workers (not the browser) so the async pipeline holds. [RT-01] |
| REQ-L1b | SHOULD | Workers read provider config through a short-TTL Redis read-through cache so many concurrent workers do not all hit Postgres for the same settings; a config change invalidates the cache key so the next run picks it up. [RT-01] |
| REQ-L2 | MUST | All model calls run on the llm queue with per-provider rate + token-budget limits and timeout + retry with backoff on 429/5xx. |
| REQ-L3 | MUST | Structured outputs validated against a schema; invalid responses are repaired or re-requested, never persisted raw. |
| REQ-L4 | MUST | Every generated threat carries source attribution AND passes a verification pass: cited finding ids must resolve to existing records and an automated groundedness check must confirm the evidence supports the claim, else the threat is demoted to "ungrounded inference" and ranked apart. Schema-valid is not accepted as grounded. [RT-06] |
| REQ-L5 | SHOULD | Prompt/response cache keyed by (model, prompt hash, evidence hash) to avoid re-billing identical analyses; streamed tokens relayed to client. |
| REQ-L6 | SHOULD | Two-tier routing (frontier planner + cheap worker) with a pre-flight cost estimate from node count + effort level and a per-node budget; the meter pauses only at safe node boundaries with a complete-so-far model, never mid-fan-out. The estimate is surfaced before the run starts. [RT-12] |

## Probe / active traffic

| ID | Priority | Requirement |
|---|---|---|
| REQ-P1 | MUST | The platform does NOT send automated active/exploit traffic at this stage. It reconstructs the request and hands the user a ready-to-fire artifact (copy-as-request, curl, Burp/Postman/mitmproxy export); the USER runs the probe manually and marks the finding confirmed. [RT-03 decision] |
| REQ-P2 | MUST | The only outbound traffic the platform itself makes is fetch/crawl/archive. That egress uses a static, pre-declared, DNS-pinned host set; link-local/RFC1918/cloud-metadata IPs (169.254.169.254 et al.) are blocked at the network layer, and scope is NEVER derived from crawled or bundle-referenced URLs — defeating Sourcemapper/katana SSRF. [RT-02] |
| REQ-P3 | SHOULD | A lightweight authorization acknowledgment + declared in-scope host list precedes fetch/crawl of a target; since exploitation is manual (REQ-P1) no heavy attestation gate is required yet. Revisit if automated probing is added later. [RT-03 decision] |

## Platform (security, tenancy, observability)

| ID | Priority | Requirement |
|---|---|---|
| REQ-S1 | MUST | Multi-tenant isolation: every row and object key is tenant-scoped; authorization enforced at the data layer, not just the API. |
| REQ-S2 | MUST | Secrets found in analysis are stored as a one-way hash + location by default — never plaintext; reveal is ephemeral, just-in-time, and audit-logged, so the platform is not a concentrated store of third-party live credentials. [RT-09] |
| REQ-S3 | SHOULD | Structured logs + traces across API→queue→worker→LLM with the run_id as correlation id; per-stage metrics exported. |
| REQ-S4 | MUST | Explicit retention, purge, and breach-handling policy for any custodied sensitive data (secrets, tokens, reconstructed sources); default TTLs and a tenant-initiated purge path. [RT-09] |

## External engines / tooling

| ID | Priority | Requirement |
|---|---|---|
| REQ-T1 | MUST | External engines run as pinned, version-locked binaries/images invoked out-of-process; the worker parses their machine output (JSON/SARIF/JSONL) and never imports Go/Rust internals. |
| REQ-T2 | MUST | Any engine that emits network traffic (katana, gau, Sourcemapper URL fetch, Kingfisher validation) runs only in the scoped egress sandbox under the run's host allow-list and per-target throttle. |
| REQ-T3 | SHOULD | Runtime prerequisites (headless Chrome, Node+cdxgen, Hyperscan/SIMD, vuln-DB cache) are baked into the worker image and health-checked at boot, not installed per-run. |
| REQ-T4 | MUST | Each external engine has a contract-test suite with golden-output fixtures in CI that fails the build on upstream output-schema drift, so a silent field rename can never drop findings in production. [RT-07] |
| REQ-T5 | SHOULD | A documented monthly upgrade + CVE-triage cadence per pinned binary/image (Kingfisher, Sourcemapper, katana, gau, depscan, Chrome, cdxgen); pins are reviewed, not frozen. [RT-07] |

## Crawl enhancements (`REQ-CE*`, post-original addendum)

Added after the verbatim transcription above — crawl/fetch capabilities the build grew that the
code references by ID (so `REQ-CE1` resolves here). Not part of the original 41.

| ID | Priority | Requirement |
|---|---|---|
| REQ-CE1 | SHOULD | A standard (non-headless) crawl passes katana `-jc` so lazy/dynamic `import()` chunk URLs (webpack/vite) are discovered during the crawl, not only on a runtime scroll. Config-gated kill-switch (`RECON_CRAWL_JS_CRAWL`, default on) since katana flag semantics drift between releases. |
| REQ-CE2 | SHOULD | On the crawl/fetch path, a fetched JS asset's external `//# sourceMappingURL=` is discovered, the `.map` is fetched **through the egress guard** (REQ-P2/T2), and linked to the asset so analyze recovers original sources (tolerant `"capture"` origin — a malformed map degrades to bundle analysis, never drops findings). |
| REQ-CE3 | dev/test | An SSRF-guard override (`RECON_ALLOW_LOCAL_EGRESS`, **DEFAULT OFF**) that also permits loopback + private-range targets and single-label hosts (`localhost`) so the pipeline can run against a local target in dev/test. Off in any real deployment; it relaxes only the public-IP requirement, never scope. |

## Recommended stack candidates (from the original component diagram)

The requirements describe topology, not a mandated stack; the original design named these
technology **candidates** per tier (swap-friendly — "the shape is the requirement"). Recorded
here because some ADRs cite the candidate set (e.g. ADR-0001 weighs the broker and worker-pool
candidates below):

| Tier | Component | Candidate technology |
|---|---|---|
| Clients | Web workspace · Browser extension | React / SSE client · MV3 service worker |
| Edge / API | API gateway · SSE/WS hub | FastAPI / Uvicorn · Redis pub/sub |
| **Broker** | Message broker (durable queues, priority lanes, DLQ) | **Redis Streams / RabbitMQ** |
| Workers | Fetch/crawl pool · **Analysis pool** · LLM/probe pool | Async HTTP workers · **Python / Celery** · Rate-limited workers |
| State | Postgres · Object store · Redis · Vector DB | + Alembic · S3 / MinIO · in-memory · pgvector / Qdrant |

## Non-functional targets

6 worker queues · 4 data stores · **< 4 min target run SLA** (bounded input: ≤ N assets,
single host).

---

*Provenance: transcribed verbatim from the original red-teamed engineering requirements
(design export) so the requirement set is a first-class, versionable artifact rather than a
design-tool file. The IDs and wording are stable; the ADR trail cites them.*
