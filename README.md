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
docker compose up -d --build   # postgres + redis + minio + migrate + api + worker; API at http://localhost:8000
```

The default stack ships **auth on** (`RECON_AUTH_SECRET` is set in compose), so the workspace opens
to a login screen. Seed the first operator off the HTTP surface, then sign in:

```bash
cd apps/platform
# 1) create a tenant — prints its UUID
docker compose run --rm api python -m recon.bootstrap create-tenant "Acme Security"
# 2) seed an operator into that tenant (dev creds admin/admin; --force allows the weak default
#    because compose sets RECON_ENV=docker, which isn't a recognized dev env)
docker compose run --rm api python -m recon.bootstrap seed-admin \
  --tenant-id <uuid-from-step-1> --username admin --password admin --force
# 3) open http://localhost:8000 and log in as  admin / admin
```

Then load the extension: open `chrome://extensions`, turn on **Developer mode**, click **Load
unpacked**, and select `apps/capture/chrome-extension`. Its default backend is
`http://localhost:8000` and capture ingest is on by default; **sign in from the extension popup**
(the same operator) so captured JS lands in your tenant and flows into the platform's run/analyze
pipeline.

Full stand-up detail (host dev, tests, capture-mode runs) is in
[`apps/platform/README.md`](apps/platform/README.md); per-app docs live under each app directory.
