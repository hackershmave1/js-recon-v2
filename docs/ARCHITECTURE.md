# Architecture

This repo (`js-recon-v2`) is **one product** — the recon **platform** — plus the surviving v1
**Chrome extension**, a client that feeds it. Both reconstruct a backend's API surface from
JavaScript; the platform does it from **static crawl/upload**, the extension adds **runtime,
post-authentication capture** and hands what it grabs to the platform for the same analysis.

> **This document is the *what*; the [`adr/`](adr/README.md) trail is the *why*.** The
> load-bearing decisions behind the design below — the Redis Streams broker, DB-enforced
> row-level security, the fail-closed SSRF egress guard, the static / no-active-traffic
> stance, the v1 convergence, and more — are recorded as Architecture Decision Records
> (MADR) with a pointer to the code that enforces each.

```
apps/
├── platform/   The recon platform — the whole product. Upload/crawl a target's JS -> Vespasian
│               tree-sitter AST analysis -> content-addressed findings -> OpenAPI rebuild ->
│               evidence-grounded threat model. Async spine (FastAPI API + Redis-Streams worker +
│               MinIO/S3 blobs + multi-tenant Postgres with row-level security). Frontend: the
│               Recon Workspace — React + Vite + TypeScript. API :8000.
└── capture/
    └── chrome-extension/   MV3 extension — the one v1 piece carried forward. Intercepts the JS
                            the browser actually loads (behind auth) and pushes it to the
                            platform's ingest contract. Default backend http://localhost:8000.
```

## The platform (`js-recon-v2`)

The platform is the sole backend **and** the workspace UI. It is a requirements-driven rewrite
(the 40 `REQ-*` IDs in [`REQUIREMENTS.md`](REQUIREMENTS.md)).

**Async spine.** The API is a thin accept/validate/enqueue/read tier (`api/app.py` — "no route
does crawl/fetch/parse/LLM/probe work"): `POST /runs` persists a run and enqueues, returning in
<200ms; heavy work runs in the **worker** process. The two communicate over a **Redis Streams**
broker — one stream per work class, a `workers` consumer group, at-least-once delivery with
backoff-retry and a per-queue dead-letter stream (`queue/streams.py`). A run is a persisted state
machine (`queued→discovering→fetching→ingesting→analyzing→correlating→done`, plus `paused`/
`cancelled`) advanced by the worker at cancel/pause-checkpointed stages (`worker/main.py`).

**Data + isolation.** State lives in **Postgres**, isolated per tenant by **row-level security**
enforced in the database, not just the API: every tenant-scoped table has an RLS policy keyed on
`current_setting('app.current_tenant')`, and the only supported access path is `tenant_session()`,
which sets that GUC for one transaction (`db/base.py`). Large/binary artifacts (raw JS, source
maps, reconstructed sources, reports) never sit in a row — they go to **S3/MinIO** under a
content-addressed, tenant-scoped key `{tenant_id}/{run_id}/{kind}/{sha256}` (`storage.py`, one
boto3 client, path-style + s3v4 so the same code hits MinIO locally and S3 in production).

**Engines.** Three analysis engines, plus katana for discovery:

| engine | role | how it runs |
|---|---|---|
| **Vespasian** | tree-sitter JS AST walker; traces `fetch` / `XMLHttpRequest.open` / `axios.*` / jQuery `$.ajax\|$.get\|$.post` / `new WebSocket` into endpoints, methods, URLs, and statically-resolvable params (engine tag `vespasian`) | **in-process** (`findings/extract.py`, `findings/analyze.py` — `tree_sitter` + `tree_sitter_javascript`) |
| **Kingfisher** | MongoDB Kingfisher secret scanner (`kingfisher-bin==1.106.0`), run offline (`--no-validate --no-update-check --no-dedup`); the raw token is hashed into the finding, never stored in the identity | **out-of-process** binary (`findings/kingfisher.py`) |
| **Sourcemapper** | recovers a bundle's original sources from its source map so findings attribute to real paths (`app/src/api.js`) instead of the `input.js` placeholder (`github.com/denandz/sourcemapper`, Go-built) | **out-of-process** binary (`findings/sourcemapper.py`) |
| katana | JS-asset discovery crawler for the DISCOVER stage (discovery-only argv; "Vespasian parses, not katana") | out-of-process (`discover/katana.py`) |

The out-of-process engines run through one hardened harness (wall-clock timeout, output-size cap,
explicit acceptable exit codes, non-root container user) so the safety controls live in one place
(`findings/engines.py`). Analysis is honest by construction (REQ-C2): a detected sink whose URL
isn't statically resolvable is counted as *unattributed*, never invented; a missing engine binary
degrades coverage honestly rather than silently reporting "clean".

**API surface + frontend.** The API mounts routers for sessions, engagements, runs, findings,
manual-probe, sources, the OpenAPI spec + diff, export, base-URL overlay, and wrapper-teaching
(`api/app.py`) — driven day-to-day at `localhost:8000/sessions`, `/runs`, `/runs/{id}/findings`,
etc. The **Recon Workspace** frontend (React 19 + Vite + TypeScript, `apps/platform/web/`) is the
operator UI over that surface. Status streams back over SSE with an `ETag`/`304` polling fallback.
Its **Sources** page browses both the fetched JS chunks and the source-map-recovered originals —
the recovered list is derived from persisted `occurrence.source_path` (no subprocess fan-out; the
Sourcemapper binary reruns on demand only for the one file being viewed) — with lazy syntax
highlighting, and a finding occurrence links straight to its line in the recovered original.

**Runtime JS capture (server-side, default-off).** A run can set `crawl_mode="capture"` to route the
DISCOVER stage away from katana and into an in-process CDP capture stage (`recon/capture/`) instead.
It drives the headless Chromium already baked into the worker image over raw CDP
(`websockets.sync`) and records every script V8 actually parses — `Debugger.scriptParsed` +
`Debugger.getScriptSource`, fanned out across the whole target tree (the page, dedicated/shared
workers, and service workers) via a browser-level `Target.setAutoAttach{flatten}` waterfall. This
recovers inline, runtime-injected, and `eval`'d JS — and worker / service-worker code — that crosses
no network response and so is invisible to the static fetch path. Captured scripts are written as the
same capture asset contract the extension produces (`run_asset` `input` blobs pre-marked `fetch_ok` +
a `discover.assets` event), so FETCH no-ops and ANALYZE is unchanged. It is **default-off** behind
`RECON_ENABLE_CAPTURE_MODE` and is a deliberate, gated relaxation of the static / no-active-traffic
stance (ADR 0006): it runs only against an in-scope, `authorization_ack`-ed target, with a pre-launch
egress-scope validation and a per-script in-scope re-check. See
[ADR 0009](adr/0009-runtime-cdp-js-capture.md).

## The capture extension (the surviving v1 client)

The MV3 Chrome extension (`apps/capture/chrome-extension/`) is the **only** capability carried
forward from v1. It does what a static crawl cannot: it runs *in the browser*, so it sees the JS
the app actually loads **after authentication** — lazy/dynamic chunks, post-login bundles, and
their source maps — and uploads that to the platform. It is a pure client: it captures and pushes;
all analysis (Vespasian / Kingfisher / Sourcemapper) happens on the platform, on the same async
spine as a native crawl. Its default backend is `http://localhost:8000` (`modules/batch-uploader.js`,
`modules/workspace-client.js`), overridable in the popup's Settings.

## The extension <-> platform ingest contract

The extension talks to the platform over a small `/api` ingest surface (distinct from the
platform's native `/sessions` + `/runs` routes). It is **flag-gated**: `capture_router.py` is
mounted only when `RECON_ENABLE_CAPTURE_INGEST` is enabled (config default off — `config.py`
`enable_capture_ingest`; the mount is conditional at `api/app.py`), so a normal recon deployment is
unaffected. A deployment that serves the extension turns the flag on.

| route | purpose |
|---|---|
| `POST /api/save-files` | batched JSON push of captured files; accumulates into the capture session's single open run — one `run_asset` row per file, pre-marked `fetch_ok`, blobs to S3. Binds a valid `projectId`→engagement defensively (an unknown id is ignored, never dropping the batch). |
| `POST /api/sessions/{id}/analyze/start` | emits `discover.assets` and enqueues one worker walk; the pre-fetched assets make DISCOVER/FETCH no-op (no katana, no network egress of captured URLs), so only ANALYZE does real work. |
| `GET /api/sessions/{id}/analyze/progress` | adapts the run's per-asset status into the popup's counts+files `job` shape. |
| `GET\|POST /api/projects` | adapts platform engagements to the extension's project shape (a bare JSON array, `id`, a synthesized `defaults.scope`). |
| `GET /api/health` | capture-ingest liveness (distinct from the platform's own `/healthz`). |

The path is idempotent by construction — content-addressed blobs + `(run_id, url)` conflict-skip —
so the extension's whole-batch retries never duplicate a run or an asset. Per-file source maps the
extension sends are stored per asset (`run_asset.source_map_ref`, migration `0010`) and recovered
with the tolerant `source_map_origin="capture"`, so a malformed map falls back to bundle analysis
instead of dropping the asset's findings.

## Convergence history (v1 retired)

Before the cutover, `apps/capture/` was a full v1 app in its own right — the older *JS Security
Extractor*:

- `apps/capture/api/` — a single-user, threaded FastAPI backend (no Redis/worker/S3; local-disk
  storage; Postgres) that served `:3000`, with its own REP-style endpoint/secret extractors and
  jsluice integration.
- `apps/capture/web/` — the original "RECON Workspace" SPA, built and served by that backend.

Both were **retired and deleted** in the convergence: the platform already ships its own analysis
(Vespasian + Kingfisher + Sourcemapper), Sources viewer, and findings/OpenAPI pipeline per the v2
requirements, so v1's backend and UI were redundant. Their one durable capability — runtime capture
followed by analysis — is now served by the platform through the ingest contract above, built
incrementally behind `RECON_ENABLE_CAPTURE_INGEST` (Phase 1 ingest → Phase 2 endpoint parity →
Phase 3 source-map robustness) so the extension was never broken mid-migration. The cutover then
repointed the extension's default backend from `:3000` to `:8000` and removed `apps/capture/{api,web}`.

v1-only tooling was **not** carried into v2: jsluice, the REP-style regex extractors, and the
asset-provenance graph appear in zero REQ-* items and were left behind, not unioned in. (An earlier
draft called the two analysis cores "complementary" and proposed unioning them; that was a
v1-porting assumption, unsupported by the requirements, and is not part of the converged product.)

## Running

Everything is one full stack — the stores (Postgres, Redis, MinIO), a one-shot `migrate` job, the
`api`, and the `worker` (one image serves all three app roles):

```bash
cd apps/platform
docker compose up -d --build   # postgres + redis + minio + migrate + api + worker; API at http://localhost:8000

# create a tenant (privileged admin connection, off the HTTP surface)
docker compose run --rm api python -m recon.bootstrap create-tenant "Acme Security"
```

To also serve the capture extension, enable the ingest flag on the API + worker, then load the
extension unpacked:

```bash
# RECON_ENABLE_CAPTURE_INGEST=true in the api/worker env, then:
# chrome://extensions -> Developer mode -> Load unpacked -> apps/capture/chrome-extension
# the extension's default backend is already http://localhost:8000
```

To run a **server-side capture** run instead of a static crawl, enable the capture flag on the API +
worker and start the run in capture mode:

```bash
# RECON_ENABLE_CAPTURE_MODE=true in the api/worker env (add RECON_ALLOW_LOCAL_EGRESS=true for a local
# target), then start a run with crawl_mode="capture" — the New Recon "Runtime capture" toggle in the
# workspace, or capture:true on POST /runs. The API rejects capture:true while the flag is off.
```

Per-app detail lives under each app (`apps/platform/README.md`; the extension's own docs under
`apps/capture/chrome-extension/`).
