# Slice Y — multi-asset analyze (design)

- **Date:** 2026-07-26
- **Status:** approved (brainstorming); pending written-spec review, then the §4
  adversarial design gate, then implementation plan.
- **Slice:** "Slice Y" — the second half of the discovery split begun in Slice X
  (`docs/superpowers/specs/2026-07-25-slice-discovery-katana-design.md`). Slice X
  made `DISCOVERING` a real katana crawl that produces an in-scope `.js` **assets
  manifest**; that manifest is currently a dead-end deliverable. Slice Y wires the
  manifest into fetch/analyze so every discovered asset becomes findings, and lands
  the data-model, secret-reveal, and completeness changes that multi-asset forces.
- **Primary REQ:** REQ-C1/C2 (multi-asset coverage, honest per-asset), REQ-D3
  (occurrence identity gains an asset dimension; `finding_hash` unchanged), REQ-D5
  (completeness → `DONE` vs `PARTIAL`), REQ-S2 (reveal routes to the right per-asset
  blob). Touches REQ-A3 (outbox idempotency preserved), REQ-P2 (egress — assets are
  already per-URL re-validated at manifest time in Slice X), REQ-Q3 (politeness
  inside the fetch loop), REQ-S1 (RLS on the new `run_asset` table).

## 1. Context

Today the pipeline is depth-first over a **single** asset even for a crawl run:

- `discover.crawl.discover_run` crawls the domain and writes an `assets` manifest
  blob + a `discover.assets` event (`{count, assets_ref, status}`). Nothing consumes
  it.
- `fetch.fetch_run` downloads the single `run.target` URL into `run.input_ref`
  (`fetch.py:141`); a bare-domain target is a no-op (`fetch.py:152`).
- `analyze.analyze_run` reads the one `run.input_ref` blob (`analyze.py:65`) and
  writes findings + occurrences.
- `coordinator.advance` finalizes every run to `DONE` with a hardcoded
  `completeness={fetch_ok: true, analyze_ok: true}` (`coordinator.py:124-131`).

So a crawl run flows `DISCOVER (real) → FETCH no-op → ANALYZE no-op → DONE`: it finds
23 assets and analyzes none of them.

### 1.1 The three hazards this slice must resolve (gate-verified, re-checked 2026-07-26)

The original single-slice design failed its §4 adversarial gate (*do not build as
specified*) on three code-verified points, all deferred to this slice. All three were
re-verified against current `main`:

1. **Attribution vs. dedup are mutually exclusive under the current schema.** The
   occurrence identity (`store.py:39-52` `_identity()`) has no asset dimension, and
   `finding.path` is inside `finding_hash` (`store.py:81`,
   `normalize.finding_hash(type, value, path)`). To attribute a finding to the real
   asset you would put the asset in `path` → the same endpoint in two bundles hashes
   differently → no dedup. To dedup you drop the asset → attribution is lost. Model A
   ("one crawl = one run; the same endpoint across N bundles dedupes to one finding
   with N occurrences") needs both.
2. **Multi-asset silently breaks the shipped S2 secret reveal.** `reveal.py:168`
   slices `target.input_ref`, which is `run.input_ref` (`reveal.py:137`) — a single
   blob. With per-asset blobs, reveal would slice the wrong bytes and fail-close (409)
   or, worse, return bytes from the wrong asset.
3. **`PARTIAL` completeness is unreachable.** `coordinator.advance` hardcodes `DONE`
   and `{fetch_ok, analyze_ok}=true` (`coordinator.py:124-131`); the state machine
   *already* permits `CORRELATING → PARTIAL` and `ACTIVE → PARTIAL`
   (`state_machine.py:30,42`), so `PARTIAL` is wired but never chosen.

## 2. Settled decisions

Decided during brainstorming (2026-07-26); binding for this slice unless re-opened.

1. **Asset dimension home: a new `run_asset` table + an occurrence link.** One row per
   crawled asset carries its blob ref and per-asset fetch/analyze status; the
   occurrence gets a nullable FK into it, and the asset enters occurrence identity.
   Chosen over occurrence-only columns (a fetched-then-failed or zero-finding asset
   would have no occurrence row → invisible to completeness) and over a manifest/event
   store (weak for dedup correctness and completeness queries).
2. **Fan-out execution: an in-stage sequential loop, heartbeating.** One `FETCH` job
   loops all assets; one `ANALYZE` job loops all. Chosen over per-asset queue jobs
   (model C) because `fetch.py` pins `socket.getaddrinfo` **process-globally**
   (`fetch.py:50-80`), which is safe *only* while one worker fetches one asset at a
   time in one thread; parallel per-asset fetch would break that invariant. Queue
   fan-out stays deferred (scale milestone).
3. **Per-asset failure policy: best-effort, record-and-continue → `PARTIAL`.** A
   per-asset fetch/analyze error is recorded on the asset's `run_asset` row and the
   loop continues; only infrastructure errors (DB/Redis) propagate to the stage
   retry/DLQ. Chosen over whole-stage retry (one persistently-bad asset would re-fetch
   all N and eventually `FAIL` the whole run — `PARTIAL` never reached). Per-asset
   retry of transient 5xx is deferred debt.
4. **`PARTIAL` rule: crawl clean AND all assets ok.** `DONE` only if the crawl was not
   `capped`/`timeout` **and** every asset fetched **and** every asset analyzed;
   otherwise `PARTIAL`. A truncated crawl means we cannot even claim we found all
   in-scope assets, so it cannot be complete (the honest REQ-D5 reading).
5. **`finding_hash` is unchanged.** The asset dimension lives only on the occurrence,
   never in `finding_hash` — so cross-asset dedup holds and the REQ-D3 identity that
   triage (`finding_triage.finding_hash`) and reveal depend on is preserved.
6. **Backward compatibility is mandatory.** Upload and single-URL runs have no
   `run_asset` rows and must take their existing single-blob branch in every stage,
   with zero behavior change; reveal falls back to `run.input_ref`.

## 3. Scope

**In scope**

- A `run_asset` table (migration `0005`, RLS-forced) + `finding_occurrence.run_asset_id`.
- The asset dimension in occurrence identity (`store.Occurrence` + `normalize.occurrence_hash`).
- `discover` also inserts `run_asset(pending)` rows for the kept URLs (idempotent).
- Multi-asset `fetch`: loop `run_asset` rows, per-asset blob + status, politeness-paced
  and heartbeating, best-effort.
- Multi-asset `analyze`: loop fetched assets, write asset-tagged occurrences, cross-asset
  dedup, per-asset status.
- Reveal routing to the occurrence's asset blob, with the legacy `run.input_ref` fallback.
- Completeness computed from `run_asset` → `DONE` vs `PARTIAL` in `coordinator.advance`.
- UI: per-asset fetch/analyze status in the assets inventory; occurrence→asset
  attribution in findings; a distinct `PARTIAL` terminal badge. Live walkthrough.

**Out of scope (→ later)**

- Per-asset retry of transient errors (best-effort drops it — debt).
- Queue fan-out / crawl parallelism (model C, scale milestone).
- OpenAPI/Swagger export (the other half of "complete the first chunk").
- Egress hardening beyond application-level (Slice X accepted residual risk, unchanged).
- Scanning recovered source-map files for **secrets** (pre-existing analyze follow-up).

## 4. Architecture

```
DISCOVER (Slice X, + NEW rows)
  crawl -> kept in-scope .js URLs
    -> assets manifest blob + discover.assets event      (unchanged)
    -> INSERT run_asset(url, status=pending) ON CONFLICT DO NOTHING   (NEW)

FETCH  (one job, loops)                                   (NEW multi-asset branch)
  rows = run_asset for run
  if rows: for each not-terminal asset:
      politeness wait (sleep + heartbeat)                 (REQ-Q3, in-loop)
      fetch_url(url) -> put_blob(kind="input") -> run_asset.input_ref, fetch_status=ok
      on per-asset error -> fetch_status=failed, fetch_error; continue  (best-effort)
  else: legacy single-target fetch into run.input_ref     (unchanged)

ANALYZE (one job, loops)                                  (NEW multi-asset branch)
  if rows: for each fetched asset:
      analyze its blob (existing extract/scan), write occurrences tagged run_asset_id
      analyze_status = ok | failed
  else: legacy single-blob analyze over run.input_ref     (unchanged)

FINALIZE (coordinator.advance, after CORRELATING)         (NEW compute)
  is_crawl = a discover.assets event exists for the run   (NOT "rows exist")
  if is_crawl: crawl_ok = discover.assets.status == "ok"
               fetch_ok = crawl_ok AND all rows.fetch_status == ok    (vacuous-true on 0 rows)
               analyze_ok = fetch_ok AND all rows.analyze_status == ok
               -> DONE if (fetch_ok and analyze_ok) else PARTIAL
  else: DONE with {fetch_ok, analyze_ok}=true             (unchanged, legacy single-asset)
```

INGEST/CORRELATE remain no-op stages; analyze precedes CORRELATE, so per-asset results
are complete at finalize. The finalize discriminator is the **`discover.assets` event**,
not the row count: a crawl that timed out finding **zero** assets has no rows but must
still finalize `PARTIAL` (crawl not clean), which a row-count discriminator would miss.

## 5. Data model

### 5.1 `run_asset` (new table)

```
run_asset
  id             UUID PK
  tenant_id      UUID FK tenant(id) ON DELETE CASCADE     (RLS)
  run_id         UUID FK run(id)    ON DELETE CASCADE
  url            Text NOT NULL                            (the discovered asset URL)
  input_ref      Text NULL                                (per-asset blob key once fetched)
  fetch_status   String(16) NOT NULL default 'pending'    (pending | ok | failed)
  fetch_error    Text NULL
  analyze_status String(16) NOT NULL default 'pending'    (pending | ok | failed)
  analyze_error  Text NULL
  created_at     timestamptz NOT NULL default now()
  UNIQUE(run_id, url)  -> uq_run_asset_run_url
  Index(tenant_id, run_id) -> ix_run_asset_run
  CHECK fetch_status/analyze_status IN AssetStatus  (via models._enum_check)
```

- New `AssetStatus(StrEnum)` in `domain.py`: `PENDING="pending"`, `OK="ok"`,
  `FAILED="failed"`. Reuses the `models._enum_check(column, enum_cls)` convention.
- New `ASSET_TABLES: tuple[str, ...] = ("run_asset",)` in `models.py`; migration `0005`
  applies FORCE RLS + `tenant_isolation` policy + GRANT, exactly mirroring `0004`.
- Per-asset blobs reuse `storage.put_blob(kind="input")`; the key is content-addressed
  (`{tenant}/{run}/input/{sha256}`), so distinct asset bytes get distinct keys — no new
  `BLOB_KINDS` entry.

### 5.2 `finding_occurrence` gains the asset dimension

- New column `run_asset_id UUID NULL FK run_asset(id) ON DELETE SET NULL` (the
  occurrence already cascades from `finding`; the asset link is secondary, so nulling
  it — rather than cascade-deleting the occurrence — is the conservative choice).
- `store.Occurrence` gains `run_asset_id: str | None = None` (stored on the row) and
  `asset_url: str | None = None` (identity input only).
- `_identity()` adds `asset_url`, so `normalize.occurrence_hash` includes the asset:
  two assets whose identical endpoint lands at identical byte offsets stay **two**
  occurrences instead of collapsing. The occurrence uniqueness key
  (`UNIQUE(finding_id, occurrence_hash)`) is unchanged in shape.
- **Rehash note:** adding `asset_url` to the identity dict changes `occurrence_hash`
  for *all* occurrences, including legacy single-asset ones (where `asset_url=None`).
  This is safe: the build is pre-prod with no data to preserve (see
  `docs/slice2-deferred-debt.md` migration section); `finding_hash` — the cross-run,
  triage/reveal-stable identity — is untouched.

## 6. Fetch stage (multi-asset)

`fetch.fetch_run(redis, *, tenant_id, run_id, job_id)` — `job_id` is added so the loop
can heartbeat (the worker already has it and passes it to `discover`).

- Load `run_asset` rows for the run. **If none:** the existing single-`target` path,
  byte-for-byte unchanged (upload/single-URL). **If rows exist:** loop them.
- Per asset, in order: skip if terminal (`fetch_status in {ok, failed}` or `input_ref`
  set) — idempotent across redelivery. Otherwise:
  - **Politeness in-loop (REQ-Q3):** all assets share the in-scope host, so
    `politeness.check` would otherwise throttle every asset. Instead of raising
    `RetryableError` (which defers the whole stage — wrong for a per-asset loop), the
    loop `sleep`s the returned wait in short slices, calling `progress.beat` between
    slices, then proceeds. The existing single-asset path keeps its raise-to-defer
    behavior.
  - Fetch via the unchanged `fetch_url(...)` (per-hop egress re-validation + DNS pin).
    On success: `put_blob(kind="input")`, set `input_ref` + `fetch_status=ok`.
  - **Best-effort:** `egress.EgressBlocked` and `retry.FatalError` and
    `retry.RetryableError` for a single asset are caught, recorded as
    `fetch_status=failed` + `fetch_error`, and the loop continues. Only unexpected
    infrastructure errors (DB/Redis/storage) propagate to the worker's retry/DLQ.
  - `progress.beat` at least once per asset so a long loop never trips the 30 s lease
    (`heartbeat_stall_threshold_seconds`).

## 7. Analyze stage (multi-asset)

`analyze.analyze_run(redis, *, tenant_id, run_id, job_id)`.

- Load `run_asset` rows. **If none:** the existing single-blob analyze over
  `run.input_ref`, unchanged (including uploaded-source-map handling). **If rows
  exist:** loop assets whose `fetch_status == ok`.
- Per asset: run the existing `_analysis_units` / `extract` / `kingfisher.scan` over
  that asset's blob (inline source maps handled exactly as today), writing findings +
  occurrences through `store.record_finding`. Every occurrence for that asset carries
  `run_asset_id` and `asset_url` (the asset's URL). Cross-asset dedup falls out of the
  unchanged `finding_hash`: the same endpoint in two assets → one `finding` row + two
  occurrences (one per asset).
- Set `analyze_status = ok`; on a per-asset analyze error, `analyze_status = failed` +
  `analyze_error`, continue (best-effort). Heartbeat per asset.
- The `analyze.coverage` event stays per-run; it gains an `assets` breakdown so
  per-asset coverage is honest (REQ-C2). Uploaded `run.source_map_ref` applies only to
  the legacy single-asset path (crawl assets use inline maps only).

## 8. Reveal routing (REQ-S2)

`reveal._load_target` additionally loads, for the chosen offset-bearing occurrence, its
`run_asset_id` → that `run_asset.input_ref`. `_Target.input_ref` becomes **the
occurrence's asset blob** when `run_asset_id` is set, else `run.input_ref` (legacy).
`_reveal_occurrence` (offset-bearing pick) is extended to also surface the chosen
occurrence's `run_asset_id`. Everything downstream — the JIT byte-slice in the same
utf-8/replace space, the `provider:sha256` re-check (fail-closed 409), the value-free
audit — is unchanged. All occurrences of one `finding_hash` still decode to the same
token, so picking any offset-bearing occurrence and slicing *its own* asset blob is
correct.

## 9. Completeness / `PARTIAL` (REQ-D5)

`coordinator.advance` computes completeness at finalize (when `next_stage` is `None`,
i.e. after `CORRELATING`). The discriminator is **whether a `discover.assets` event
exists** (`discover_queries.latest_assets_event`) — i.e. whether this is a crawl run —
**not** the `run_asset` row count:

- Not a crawl run (legacy upload / single-URL) → today's behavior: `DONE`,
  `completeness={fetch_ok, analyze_ok}=true`.
- Crawl run:
  - `crawl_ok = (latest discover.assets event).status == "ok"` (not `capped`/`timeout`).
  - `fetch_ok = crawl_ok AND every run_asset.fetch_status == "ok"` (vacuously true on
    zero rows — a clean crawl that legitimately found no assets).
  - `analyze_ok = fetch_ok AND every run_asset.analyze_status == "ok"`.
  - Transition to `DONE` iff `fetch_ok and analyze_ok`, else `PARTIAL`; both are legal
    from `CORRELATING`. Persist `completeness={fetch_ok, analyze_ok}`.
  - Edge case this discriminator handles correctly: a crawl that **timed out finding
    zero assets** has no rows but `crawl_ok=false` → `PARTIAL` (a row-count
    discriminator would wrongly finalize `DONE`).

The concurrent/duplicate-delivery guard (`except (TransitionConflict, InvalidTransition):
pass`) is kept.

## 10. UI (Mode A)

- **Assets inventory:** `GET /runs/{id}/assets` left-joins `run_asset` status onto the
  manifest so each asset shows `pending | fetched | analyzed | failed`. The manifest
  blob stays the URL source of truth; `run_asset` supplies status.
- **Findings:** an occurrence shows which asset URL it came from (attribution); a
  finding seen in multiple assets shows its N asset occurrences.
- **Run terminal state:** a `PARTIAL` run renders a distinct badge (vs `DONE`), with the
  `completeness` reason (which axis is incomplete).
- Ends with the mandated live visual walkthrough against the local fixture site (a
  multi-asset crawl → findings from several assets; a forced per-asset 404 → `PARTIAL`).

## 11. Config

No new *required* knobs. Reuse per-asset: `max_fetch_bytes`, `fetch_timeout_seconds`,
politeness settings; the asset count is already bounded by `crawl_max_assets`. Reuse
`crawl_heartbeat_interval_seconds` for the fetch/analyze loop beat cadence (a shared
"long in-stage loop" interval; rename to a neutral name only if the adversarial gate
asks).

## 12. Testing

- **Unit — occurrence identity:** two assets, same endpoint → one `finding`, two
  occurrences; `asset_url` in `occurrence_hash`; legacy `asset_url=None` still stable.
- **Migration `0005`:** `run_asset` created, FORCE RLS + policy + GRANT applied,
  `finding_occurrence.run_asset_id` present; `downgrade` drops policy + column + table.
- **Fetch loop:** mocked `fetch_url` — per-asset success/failure recorded, terminal-asset
  skip (idempotent), politeness sleep+beat, `progress.beat` called, infra error
  propagates while per-asset error does not.
- **Analyze loop:** asset-tagged occurrences, cross-asset dedup (one finding, N
  occurrences), per-asset `analyze_status`, per-asset error → `failed` + continue.
- **Reveal routing:** occurrence with `run_asset_id` slices that asset's blob; legacy
  occurrence falls back to `run.input_ref`; wrong-asset bytes → 409.
- **Completeness matrix:** all-ok → `DONE`; one fetch-fail → `PARTIAL`; one
  analyze-fail → `PARTIAL`; crawl `capped`/`timeout` (incl. timeout with zero assets)
  → `PARTIAL`; clean crawl with zero assets → `DONE`; legacy run (no `discover.assets`
  event) → `DONE`.
- **Integration (real katana + engines, `RECON_REQUIRE_ENGINES=1`):** crawl the fixture
  site (multiple `.js`), assert findings attributed across ≥2 assets and the run reaches
  `DONE`; force one asset to 404, assert `PARTIAL` with `fetch_ok=false`.
- **Front-end (Vitest):** inventory per-asset status; finding→asset attribution;
  `PARTIAL` badge.

## 13. Review gates (§4)

- **Gate 1 (adversarial design):** re-run against *this* spec before coding — the prior
  gate only cleared the Slice X half. Focus areas: the rehash-safety claim, the
  best-effort-vs-retry downgrade, the politeness-in-loop change, the `PARTIAL` compute,
  and the reveal fallback.
- **Gate 2 (higher-model code review):** whole-branch review after implementation.

## 14. Debt ledger additions

To `docs/slice2-deferred-debt.md` on landing:

- **Per-asset retry** of transient (429/5xx) fetch errors — best-effort drops it.
- **Dual asset-list source of truth** — the manifest blob (URL list, for the API/UI)
  and `run_asset` rows (per-asset state). Each serves a distinct purpose; unify only if
  it causes drift.
- **Queue fan-out / crawl parallelism (model C)** — scale milestone.
- **OpenAPI/Swagger export** — the other half of "complete the first chunk".
- **Secret scanning of recovered source-map files** — pre-existing analyze follow-up,
  now also relevant per-asset.

## 15. As-built amendments

_(none yet — filled in during implementation.)_
