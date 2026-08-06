# Cutover data-migration plan (v1 capture → platform)

Draft for review — part of Phase 4 step 1 (reversible prep). Decides what happens to existing
v1 capture data when `apps/capture/{api,web}` is retired.

## The key fact: the code delete is data-safe

Deleting `apps/capture/api` and `apps/capture/web` removes **code**. The v1 captures do **not**
live in those directories — they live in:

- the **v1 capture Postgres** (Docker; identify the exact container/volume before any wipe —
  candidates on this machine are `api-postgres-1` and `jsse-test-pg` on host `:5433`), and
- the **local blob store** at `C:/jsse-store`.

Both are **outside** the deleted directories. So the code delete destroys **zero** captures, is
**git-reversible**, and leaves every v1 capture still on disk. Retiring the code and disposing of
the data are therefore **separate, independently-timed decisions** — the delete does not force the
data question.

## Options

### A. Treat v1 data as disposable — RECOMMENDED
Do not migrate. Rationale:
- It is dev/test capture data accumulated while building the extension → platform pipeline, not
  client-engagement data.
- The platform is the product and already holds the verified capture data (the recon-range
  live-verify runs land in the platform's `capture-spike` tenant).
- The code delete doesn't touch it regardless — the v1 stores simply become inert. They can be
  left in place (harmless) or wiped later, on your explicit say-so, as a **separate** step:
  drop the v1 Postgres volume and delete `C:/jsse-store` (verify the exact container/path first).

### B. Migrate v1 captures into the platform first
Only worth it if some v1 capture is real data worth keeping. Approach: a one-off script that reads
v1's stored JS blobs + session metadata and re-submits them through the platform's now-live ingest
contract (`POST /api/save-files` → `analyze/start`, built in Phases 1–3, flag-gated by
`RECON_ENABLE_CAPTURE_INGEST`). Effort: ~M. This reuses the exact path the live extension now uses,
so it needs no new backend code — just an adapter that walks the v1 store and posts batches.

## Recommendation

**Option A (disposable).** Proceed with the code delete (git-reversible, data-safe); leave the v1
data stores in place untouched. Do **not** wipe them as part of the cutover — wiping stays a
separate, explicitly-confirmed action for whenever you're comfortable, after verifying the exact
container/volume and `C:/jsse-store` path.

If instead any v1 capture matters, say so and we do Option B (migrate) **before** the code delete.
