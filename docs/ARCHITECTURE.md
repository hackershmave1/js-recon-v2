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

## Why two apps (not merged)

They share **zero code** today but overlap heavily — two backends, two Postgres DBs, two
UIs, two analysis cores. The **Chrome extension is the one capability nothing else in the
repo provides**: runtime, in-browser, post-authentication capture (the platform only does
static crawl/upload). The two analysis cores are genuinely different and *complementary*
(platform = tree-sitter AST; capture = regex + jsluice + Kingfisher), so the end-state is
to **union** them, not delete one.

The `apps/` split is deliberate. Per the staff-engineer / engineering-manager review, the
right move now is a clean separation; converging onto one backend/DB/UI is a later,
**product-triggered** step (route runtime captures into the platform's OpenAPI /
shadow-API pipeline) — done incrementally behind a flag, never a big-bang rewrite.

## The extension <-> capture-backend contract

The extension is hard-coupled to `apps/capture/api` (default `workspaceUrl=http://localhost:3000`).
The platform implements **none** of these, so "re-point the extension at the platform" is a
real feature, not a config change. Pin these when converging
(see `apps/capture/chrome-extension/modules/workspace-client.js`):

- `POST /api/save-files` — batched JSON push of captured files
- `GET  /api/health`
- `POST /api/sessions/{id}/analyze/start` · `GET /api/sessions/{id}/analyze/progress`
- `GET|POST /api/projects`

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
