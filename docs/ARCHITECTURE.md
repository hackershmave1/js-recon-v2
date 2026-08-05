# Architecture

This repo (`js-recon-v2`) is a **monorepo** with two applications that both reconstruct
a backend's API surface from JavaScript — from opposite ends — plus shared infra/docs.

```
apps/
├── platform/   Static recon platform. Upload/crawl a target's JS -> tree-sitter AST
│               analysis -> content-addressed findings -> OpenAPI rebuild. Async spine
│               (FastAPI API + Redis-Streams worker + MinIO/S3 + multi-tenant Postgres
│               with row-level security). Frontend: React + Vite + TypeScript. API :8000.
└── capture/    Runtime capture system (three parts):
    ├── chrome-extension/  MV3 extension — intercepts the JS the browser actually loads
    │                      (behind auth) and uploads it.
    ├── api/               FastAPI backend — ingests captured JS, runs per-file analysis,
    │                      builds an asset-provenance graph. Single-user, threaded (no
    │                      Redis/worker/S3); local-disk storage; Postgres. Serves :3000.
    └── web/               The "RECON Workspace" SPA (Preact + esbuild), built into
                           api/app/static/workspace/ and served by capture/api.
```

## Why two apps (converging onto v2)

`platform/` is **v2** — a complete, requirements-driven rewrite (`archive/Javascript recon
app redesign/Developer Requirements.dc.html`, 40 REQ-* IDs). `capture/` is **v1** (the older
JS Security Extractor). The **only** capability carried forward from v1 is the **Chrome
extension** — runtime, in-browser, post-authentication capture (v2 otherwise only does static
crawl/upload). v1's own backend + web UI are **not** kept: v2 already ships its own analysis
(Vespasian/tree-sitter AST + Kingfisher secrets + Sourcemapper), its own Sources viewer, and
its own findings/OpenAPI pipeline, all per v2's requirements. v1-only tooling (`jsluice`, the
asset-provenance graph) is **not** in v2's spec and is being left behind — NOT unioned in.
(Earlier drafts of this doc claimed the two analysis cores were "complementary" and should be
"unioned"; that was a v1-porting assumption, unsupported by the requirements — jsluice appears
in zero REQ-* items — and is retracted here.)

**Active program (2026-08): converge onto v2.** Wire the extension into v2's existing pipeline
— harden the flag-gated `apps/platform/src/recon/api/capture_router.py` ingest into a real,
`run_asset`-batched, worker-driven path — reach contract parity, then **delete** `capture/api`
+ `capture/web`. Done incrementally behind a flag; the extension is never broken and v1 is
removed last — never a big-bang rewrite.

**Phase 1 (ingest) — DONE (flag-gated, `RECON_ENABLE_CAPTURE_INGEST`).** The spike's throwaway
ingest is productionized on the model **one Run per capture session**: every `POST /api/save-files`
batch accumulates into the session's single open (QUEUED) run — blobs to real S3, one `run_asset`
row per file pre-marked `fetch_ok` — and `POST /api/sessions/{id}/analyze/start` emits the
`discover.assets` event and enqueues **one** worker walk. The pre-fetched assets make the
DISCOVER/FETCH stages no-op (no katana, **no network egress** of captured URLs); only ANALYZE does
real work. Idempotent by construction (content-addressed blobs + `(run_id,url)` conflict-skip), so
the extension's whole-batch retries never duplicate a run. Phases 2–4 (endpoint parity → robustness
→ cutover + delete v1) still pending.

## The extension <-> capture-backend contract

The extension is hard-coupled to `apps/capture/api` (default `workspaceUrl=http://localhost:3000`).
"Re-point the extension at the platform" is a real feature, not a config change. Status of each
route on the platform (flag-gated by `RECON_ENABLE_CAPTURE_INGEST`; see
`apps/capture/chrome-extension/modules/workspace-client.js`):

- `POST /api/save-files` — batched JSON push of captured files — **Phase 1, implemented**
- `GET  /api/health` — **Phase 1, implemented**
- `POST /api/sessions/{id}/analyze/start` — **Phase 1, implemented** (worker-driven)
- `GET /api/sessions/{id}/analyze/progress` — **Phase 2, pending** (extension polls it; 404s until then)
- `GET|POST /api/projects` — **Phase 2, pending** (project binding; a `projectId` in save-files
  metadata is currently ignored — Phase 1 deliberately keeps client metadata off the
  session-create path)

## Running

**Capture app** (the extension + workspace used day-to-day):

```bash
# Postgres on host 5433 (avoids the platform's 5432):
docker start jsse-test-pg   # first time: docker run -d --name jsse-test-pg -e POSTGRES_USER=jsextractor -e POSTGRES_PASSWORD=changeme123 -e POSTGRES_DB=js_extractor -p 5433:5432 postgres:15
# Backend on the host — serves /api, the workspace SPA, and /docs:
cd apps/capture/api
DATABASE_URL=postgresql://jsextractor:changeme123@localhost:5433/js_extractor STORAGE_PATH=C:/jsse-store uv run uvicorn app.main:app --host 127.0.0.1 --port 3000
# then load apps/capture/chrome-extension as an unpacked extension and open http://localhost:3000
```

**Platform app** (its own full stack):

```bash
cd apps/platform
docker compose up -d --build   # postgres + redis + minio + migrate + api + worker; API at http://localhost:8000
```

Per-app detail lives under each app (`apps/platform/README.md`, `apps/capture/README.md`,
`apps/capture/APPLICATION_OVERVIEW.md`).
