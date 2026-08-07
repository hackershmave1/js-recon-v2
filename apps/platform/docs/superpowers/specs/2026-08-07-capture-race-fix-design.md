# Capture get-or-create race — design (DEBT D1)

Date: 2026-08-07 · Slice: fix the silent-duplicate-session/run race on the
extension→platform ingest path. Approach **A** (user-chosen): durable DB-level
uniqueness + self-heal on conflict, not an app-level lock alone.

## Problem (grounded in code)

`apps/platform/src/recon/api/capture_router.py` resolves three rows per ingest
batch, each a **SELECT-then-INSERT with no lock or unique key**:

- `_get_or_create_tenant` (:80) — the singleton capture tenant, keyed on
  `settings.capture_tenant_name` (`config.py:111` = `"capture-spike"`).
- `_get_or_create_session` (:108) via `_find_session_by_name` (:89) — keyed on
  `session.name == ext_session_id`.
- `_accumulating_run_id` (:142) — "the session's latest QUEUED run with no Job".

Neither `tenant.name` nor `session.name` carries a unique constraint
(`db/models.py:64,123`), so two concurrent batches for the **same
`ext_session_id`** (a retry overlapping the first request, or two extension
instances) do **not** raise — they silently create duplicate sessions and/or
runs. `analyze/start` then resolves **one** of them (`_latest_run_id`,
`_find_session_by_name` → `.scalar()` returns an arbitrary first row) and
analyzes it, **orphaning the other's captured post-auth JS** — which is
un-recapturable. The window is narrow (the extension uploads batches
sequentially), but the failure is silent data loss.

CI can't catch it: the integration lane serves unique bytes per test, so the
collision never forms (`DEBT.md` D1).

## Why not the audit's original "savepoint self-heal"

An earlier audit assumed an `IntegrityError` already fires here. It does not —
there is nothing unique to conflict on. The fix must *introduce* the uniqueness,
then self-heal against it. `run_asset` seeding is already on-conflict-safe
(`runs/assets.py:49`) and is the pattern to mirror.

## Design

Three races, one coherent slice. The primary harm (orphaned JS) comes from
duplicate **sessions** and **runs**; the tenant race is a rarer, first-capture-only
failure handled with the minimal correct mechanism.

### 1. Session — `external_id` + `UNIQUE(tenant_id, external_id)`

- New nullable column `session.external_id` (Text). For a capture session it
  holds `ext_session_id`; for every other session it stays NULL.
- `Index("uq_session_tenant_external_id", "tenant_id", "external_id", unique=True)`
  in the model. Postgres UNIQUE is **NULLS DISTINCT** by default, so the ~all-NULL
  non-capture sessions never collide — the constraint only binds capture rows.
- `create_session` gains `external_id: str | None = None` (set on the row).
  Non-capture callers (`conftest.py`, `sessions_router.py`) are unaffected.
- `_get_or_create_session`: look up by `external_id`; on miss call
  `create_session(external_id=ext_session_id, …)`; **on `IntegrityError` (lost the
  race) re-SELECT by `external_id`** and return the winner's id. The existing §4
  defect-A behavior (foreign/deleted engagement → retry unbound) is preserved
  inside the create step.
- All three capture lookups (`_get_or_create_session`, `analyze_start`,
  `analyze_progress`) switch from `name` to `external_id`.

### 2. Run — `capture_external_id` + `UNIQUE(tenant_id, capture_external_id)`

The accumulating run's identity is *dynamic* ("open round for this session"), so
it can't key on a static value alone — but an **"open accumulator" marker column**
makes it work with a plain unique constraint:

- New nullable column `run.capture_external_id` (Text) = `ext_session_id` **while
  this run is the open accumulator**, NULL for non-capture runs and for **sealed**
  capture runs.
- `Index("uq_run_tenant_capture_external_id", "tenant_id", "capture_external_id",
  unique=True)` — NULLS DISTINCT, so only open accumulators bind.
- `create_run` gains `capture_external_id: str | None = None`.
- `_accumulating_run_id`: select the open accumulator by `capture_external_id`; on
  miss `create_run(capture_external_id=ext_session_id, …)`; **on `IntegrityError`
  re-SELECT** and return.
- **Seal = null the marker.** `analyze_start` sets `capture_external_id = NULL`
  (in the same `tenant_session` that records `discover.assets`). This replaces the
  old accumulator-selection signal ("QUEUED + no Job") with the marker; the next
  batch's INSERT no longer conflicts, so a fresh round opens.
  - `_run_has_job` / progress logic still key on "has Job" and are **unchanged** —
    only accumulator *selection* moves to the marker.
  - Preserves `test_analyze_start_seals_run_next_batch_opens_new_round` (:251):
    b1→run1(marker=sid); analyze seals (marker→NULL); b2 misses→run2. ✓
  - Preserves `test_two_batches_accumulate_into_one_run` (:140): b1→run1; b2 finds
    marker=sid→run1. ✓

### 3. Tenant — advisory lock (migration-free)

`tenant.name` is intentionally non-unique (the multi-tenant platform allows
duplicate display names), and the migration can't identify "the capture tenant"
without coupling to a runtime setting — so approach A doesn't fit here. The race is
first-capture-only (before any capture tenant row exists) and astronomically rare,
but cheap to close correctly:

- `_get_or_create_tenant`: SELECT by name (hot path — tenant already exists →
  fast, no lock). On miss, `pg_advisory_xact_lock(hashtextextended(name, 0))` in the
  admin transaction, **re-check under the lock**, then insert. Two concurrent
  bootstraps serialize; the loser's re-check finds the row. Lock auto-releases at
  commit.
- Alternative considered (for §4): deterministic `uuid5(ns, name)` PK +
  `ON CONFLICT (id) DO NOTHING`. Rejected as more surprising (deterministic PKs,
  mixed old-random/new-deterministic ids) for no real gain.

## Migration 0011 (`0011_capture_external_ids`)

Follows the repo pattern (`0010`): 0001 runs `create_all` from live models, so a
fresh DB already has the columns+indexes; the migration is `IF NOT EXISTS`-guarded
to be a no-op there and effective on an older DB.

```
ADD COLUMN IF NOT EXISTS session.external_id TEXT
ADD COLUMN IF NOT EXISTS run.capture_external_id TEXT
-- backfill existing capture rows by the reliable marker, not tenant name:
UPDATE session SET external_id = name
  WHERE authorized_by = 'chrome-extension-capture' AND name IS NOT NULL AND external_id IS NULL
UPDATE run SET capture_external_id = s.name
  FROM session s
  WHERE run.session_id = s.id AND s.authorized_by = 'chrome-extension-capture'
    AND run.state = 'queued' AND run.capture_external_id IS NULL
    AND NOT EXISTS (SELECT 1 FROM job j WHERE j.run_id = run.id)
CREATE UNIQUE INDEX IF NOT EXISTS uq_session_tenant_external_id ON session (tenant_id, external_id)
CREATE UNIQUE INDEX IF NOT EXISTS uq_run_tenant_capture_external_id ON run (tenant_id, capture_external_id)
```

RLS: `session`/`run` already FORCE RLS from 0001; new nullable columns need no
policy change (RLS is table-level — see 0010's note).

**Backfill-safety caveat (for §4):** if a deployment already has pre-fix duplicate
capture rows, the unique-index creation fails loudly. Given the platform DBs were
wiped fresh (2026-08-04) and capture ingest is new/flag-gated, no such dups are
expected. A failing migration surfaces prior damage for manual cleanup rather than
silently continuing — acceptable, and documented in the migration.

## Test plan

- **Two-writer concurrency (the crux, live-PG, integration-marked)** in
  `capture_router_test.py`: two threads call the get-or-create path for the same
  `ext_session_id`, rendezvousing on a `threading.Barrier` positioned between the
  existence check and the insert so both attempt the insert concurrently. Assert
  exactly **one** session and **one** run, and both callers receive the **same**
  id. Written test-first: it reproduces duplicate rows on the pre-fix code, then
  passes after.
- **Regression**: the existing accumulate / retry-idempotent / seal / progress
  tests must stay green (they pin the behavior the seal-signal change must keep).
- **Migration**: fresh-DB `upgrade head` builds cleanly (create_all + no-op
  0011); a from-0010 DB gets the columns+indexes.

## Files touched

`db/models.py` (2 columns + 2 unique indexes), `migrations/versions/0011_*.py`
(new), `sessions/service.py` (`create_session` kwarg), `runs/service.py`
(`create_run` kwarg), `api/capture_router.py` (get-or-create rework + seal),
`api/capture_router_test.py` (two-writer test). `DEBT.md` D1 → resolved.

## Out of scope

Versioning the ingest contract (D8), the test-pyramid inversion (D9), any change
to the run state machine or the worker.

## §4 adversarial design review — verdict: BUILD WITH CHANGES (2026-08-07)

Core approach survived scrutiny (checked & holds): PG16 default NULLS DISTINCT
(confirmed vs `docker-compose.yml:22`) so non-capture NULL rows never bind;
IntegrityError self-heal re-selects in a fresh `tenant_session` and, at READ
COMMITTED, always sees the committed winner under the same tenant GUC; the
`authorized_by='chrome-extension-capture'` backfill marker is unique to the two
capture create paths; migration `create_all`-vs-`IF NOT EXISTS` + index-name match
is correct. Three required changes:

1. **(IMPORTANT) Seal must be atomic with the Job row.** Nulling the marker in the
   `discover.assets` transaction (which commits *before* `enqueue_stage` creates the
   Job in a separate tx) reintroduces the orphaned-JS regression: a failure between
   the two leaves the run marker-NULL **and** job-less → invisible to the
   accumulator, `_latest_analyzed_run`, and the `_run_has_job` idempotency gate → a
   concurrent batch opens a new run and strands the first run's captured JS.
   **Fix:** split `coordinator.enqueue_stage` into `create_stage_job(session, …)`
   (Job row, composed into the caller's tx) + `publish_stage_job(redis, msg)` (Redis
   after commit); `analyze_start` records the event + nulls the marker + inserts the
   Job in **one** `tenant_session`. Residual = the pre-existing, documented DB→Redis
   outbox gap (`coordinator.py:44-48`) where the run consistently has both.
2. **(MINOR) Tenant advisory lock in ONE admin transaction.** Lock + recheck +
   INSERT must all live in a single `admin_session`; do not reuse
   `create_tenant` (it opens its own session → the lock would guard nothing).
3. **(MINOR) Two-writer test covers BOTH races + red-then-green.** Separate focused
   tests: one rendezvous seam at the session lookup, one at the run lookup (extract
   `_find_session_by_external_id` / `_find_open_capture_run` so the tests can inject
   a `threading.Barrier`). Demonstrate duplicate rows without the unique index
   (red), then one row with it (green).
