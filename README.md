# js-recon-v2

The JS/API-recon **platform** — it recovers a backend's API surface from JavaScript — plus a
Chrome **extension** that captures runtime (post-authentication) JS and feeds it to the platform
for the same analysis. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full picture and
the extension <-> platform ingest contract.

```
apps/
├── platform/   The recon platform — the whole product (FastAPI + Redis worker + MinIO/S3 +
│               multi-tenant Postgres; React/Vite workspace).   API :8000
└── capture/
    └── chrome-extension/   MV3 extension — captures the JS the browser loads behind auth and
                            pushes it to the platform. Default backend http://localhost:8000.
```

## Quick start

Bring up the platform (API + workspace):

```bash
cd apps/platform
docker compose up -d --build   # http://localhost:8000
```

Then load the extension: open `chrome://extensions`, turn on **Developer mode**, click **Load
unpacked**, and select `apps/capture/chrome-extension`. Its default backend is
`http://localhost:8000` and capture ingest is enabled by default, so captured JS flows straight
into the platform's run/analyze pipeline.

Per-app docs live under each app directory.
