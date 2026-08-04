# js-recon-v2

A monorepo of two JS/API-recon apps that recover a backend's API surface from JavaScript —
one **statically**, one from **runtime capture**. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full picture, the boundary between
them, and the extension <-> backend contract.

```
apps/
├── platform/   Static recon platform (FastAPI + Redis worker + MinIO + React/Vite).   API :8000
└── capture/    Runtime capture: chrome-extension/ + api/ (FastAPI) + web/ (RECON Workspace).  :3000
```

## Quick start

**Capture app** (extension + workspace):

```bash
docker start jsse-test-pg
cd apps/capture/api
DATABASE_URL=postgresql://jsextractor:changeme123@localhost:5433/js_extractor STORAGE_PATH=C:/jsse-store uv run uvicorn app.main:app --host 127.0.0.1 --port 3000
# open http://localhost:3000 ; load apps/capture/chrome-extension unpacked
```

**Platform app**:

```bash
cd apps/platform
docker compose up -d --build   # API at http://localhost:8000
```

Per-app docs live under each app directory.
