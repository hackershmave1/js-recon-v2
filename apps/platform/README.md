# Recon platform

AppSec JavaScript API-recon platform. Statically reconstructs a backend API from a
target's JavaScript, extracts findings, rebuilds an OpenAPI spec, and runs an
evidence-grounded AI threat model. Built against the platform requirements (the `REQ-*`
IDs in `docs/REQUIREMENTS.md`); the load-bearing decisions behind the design are recorded
as ADRs in [`../../docs/adr/`](../../docs/adr/README.md), and the whole-system picture is in
[`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

## What it does

A recon run is a persisted state machine
(`queued→discovering→fetching→ingesting→analyzing→correlating→done`, plus `paused` /
`cancelled`). The API is a thin accept/validate/enqueue/read tier — no route does
crawl/fetch/parse/scan/LLM work; all of it runs off the request thread in a **worker**,
over a **Redis Streams** broker with at-least-once delivery, backoff+jitter retry, and a
per-queue dead-letter stream. Status streams back over SSE with an `ETag`/`304` polling
fallback.

```
client ──POST /runs──▶ API (accept + enqueue, <200ms) ──▶ Redis Streams ──▶ worker
                          │                                                    │
                          ▼                                                    ▼
                   Postgres (runs, findings,        run state machine + stages, heartbeats,
                   run_event) under RLS             cancel/pause checkpoints, retry → DLQ
                          ▲               + S3/MinIO blobs (raw JS, maps, sources)
   GET /runs/{id}/status (ETag/304)  ◀── read models ── run_event (durable) ◀──┘
   GET /runs/{id}/events (SSE, Last-Event-ID replay) ◀── Redis event stream
```

- **Analysis engines.** Vespasian (in-process tree-sitter) traces `fetch` /
  `XMLHttpRequest` / `axios.*` / jQuery ajax / `WebSocket` into endpoints, methods, and
  statically-resolvable path/query/body params. Kingfisher (out-of-process, offline) scans
  for secrets — stored as `provider:sha256(token)` + byte offsets, **never** plaintext, with
  ephemeral just-in-time audited reveal. Sourcemapper (out-of-process) recovers a bundle's
  original source paths from its source map. The out-of-process engines share one hardened
  harness (wall-clock timeout, output-size cap, non-root).
- **Honest coverage (REQ-C2).** A detected sink whose URL isn't statically resolvable is
  counted as *unattributed*, never invented; a missing engine degrades coverage rather than
  reporting a false "clean". Attributed/un-attributed counts are surfaced per source file.
- **Finding identity (REQ-D3/A3).** Findings are content-addressed over
  `type + normalized value + path`, so a rebuilt target or a retry yields the same hash; that
  hash keys an exactly-once transactional outbox, and a normalization merge surfaces as
  occurrences rather than a silent drop. Spec:
  [`docs/req-d3-finding-hash-normalization.md`](docs/req-d3-finding-hash-normalization.md).
- **Fetch / crawl, SSRF-guarded (REQ-P2).** The one outbound path — fetch a target's JS,
  plus katana asset discovery — is fail-closed: http(s) only; host must be in the session's
  declared scope (never derived from crawled or bundle content); every resolved IP must be
  globally routable; DNS is pinned per request and redirects are re-validated per hop.
- **Outputs the operator drives (REQ-P1).** Reconstructed manual-probe requests (curl /
  raw-HTTP), an OpenAPI export, spec-diff / shadow-endpoint detection against an uploaded
  spec, and a manual base-URL overlay + wrapper-teaching for cross-file resolution. The
  platform sends **no** automated active/exploit traffic (ADR-0006).
- **Multi-tenant isolation (REQ-S1).** Every row and object-storage key is tenant-scoped,
  enforced by Postgres row-level security in the database — not just the API. Blobs live in
  S3/MinIO under a content-addressed, tenant-scoped key (REQ-D2).

The **Recon Workspace** frontend (React + Vite + TypeScript, `web/`) is the operator UI over
this surface — sessions, runs, findings/coverage, the Sources viewer (fetched chunks +
source-map-recovered originals), manual-probe, spec/export, and run controls.

## Run in Docker (full stack)

Everything runs in containers: the stores (Postgres, Redis, MinIO), a one-shot `migrate`
job, the `api`, and the `worker`. One image serves all three app roles.

```bash
docker compose up -d --build
# migrate applies schema + RLS + app role and exits; api waits for it, then serves :8000
docker compose ps                       # api healthy, worker up, stores healthy

# create a tenant (uses the privileged admin connection — off the HTTP surface)
docker compose run --rm api python -m recon.bootstrap create-tenant "Acme Security"

# then seed the first operator + log in for a token (the default stack ships auth ON, so every
# call carries a Bearer token and the tenant comes from it). --force allows the weak admin/admin
# dev default — compose sets RECON_ENV=docker, which isn't a recognized dev env.
docker compose run --rm api python -m recon.bootstrap seed-admin \
  --tenant-id <uuid> --username admin --password admin --force
curl -XPOST localhost:8000/auth/login -H 'content-type: application/json' \
  -d '{"username":"admin","password":"admin"}'          # -> {"token":"<token>", ...}

# then drive it with the login token
curl -XPOST localhost:8000/sessions -H "Authorization: Bearer <token>" \
  -H 'content-type: application/json' \
  -d '{"scope_hosts":["acme.io"],"authorized_by":"you"}'
curl -XPOST localhost:8000/runs -H "Authorization: Bearer <token>" \
  -H 'content-type: application/json' -d '{"session_id":"<sid>","target":"acme.io"}'
curl localhost:8000/runs/<run_id>/status -H "Authorization: Bearer <token>"
# X-Tenant-Id is a legacy dev-only fallback — it works only when RECON_AUTH_SECRET is empty (auth off).
```

Compose wiring: `api`/`worker`/`migrate` share `recon-platform:local`; `api` and `worker`
wait on `service_healthy` stores **and** `migrate` `service_completed_successfully`. Inside
the network the stores resolve by service name (`postgres`, `redis`, `minio`) via the
`x-app-env` block. To also serve the capture extension, set `RECON_ENABLE_CAPTURE_INGEST=true`
on the api + worker.

## Local dev (host, without app containers)

```bash
uv sync --frozen --extra dev                         # reproducible install
docker compose up -d postgres redis minio            # infra only
uv run alembic upgrade head                          # schema + RLS + app role
uv run uvicorn recon.api.app:app --reload            # API
uv run python -m recon.worker.main                   # worker
```

The migration provisions two Postgres roles: `recon` (owner, runs migrations) and
`recon_app` (the non-superuser role the app connects as, so RLS is actually enforced — a
superuser bypasses it).

## Tests

Tests are colocated with their source (`*_test.py`). Run from this directory:

```bash
RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration"   # fast lane (no infra; what CI gates)
uv run pytest -m integration                                 # end-to-end vs live PG/Redis/MinIO
uv run pytest -m ""                                          # everything
uv run ruff check src && uv run ruff format --check src      # lint + format
uv run mypy src/recon/findings src/recon/spec                # typed modules
```

Integration coverage includes: full run to `done`, transition atomicity under concurrency,
tenant isolation via RLS (cross-tenant read blocked), pause/resume, cancel, failure→DLQ, and
the real-engine golden-output contract tests (`RECON_REQUIRE_ENGINES=1` turns a missing
engine from a skip into a hard failure).
