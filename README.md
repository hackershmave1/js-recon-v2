# Recon platform

AppSec JavaScript API-recon platform. Statically reconstructs a backend API from
a target's JavaScript, extracts findings, rebuilds an OpenAPI spec, and runs an
evidence-grounded AI threat model. Built against `Javascript recon app
redesign/Developer Requirements.dc.html` (the REQ-* IDs).

## Slice 1 — the async spine (this milestone)

A recon run exists as a persisted state machine, all heavy work is enqueued off
the request thread, and status streams back over SSE with a polling fallback.
Stage work was stubbed in slice 1; the analyze stage now runs the real
in-process extractor (see slice 2 below).

```
client ──POST /runs──▶ API (accept+enqueue, <200ms) ──▶ Redis Streams ──▶ worker
                          │                                                  │
                          ▼                                                  ▼
                   Postgres (runs, jobs,        run state machine + stages, heartbeats,
                   run_event) + RLS             cancel/pause checkpoints, retry→DLQ
                          ▲                                                  │
   GET /runs/{id}/status (ETag/304) ◀── read models ── run_event (durable) ◀┘
   GET /runs/{id}/events (SSE, Last-Event-ID replay) ◀── Redis event stream
```

### What's implemented
- Run state machine `queued→discovering→fetching→ingesting→analyzing→correlating→done`, plus `paused`/`cancelled` (REQ-A2, REQ-A4)
- Atomic, guarded transitions with a same-transaction durable event write (REQ-A2, REQ-R2)
- Six work queues on Redis Streams with backoff+jitter retry and per-queue DLQ (REQ-Q1, REQ-Q2)
- Progress records + heartbeat → stalled-vs-running (REQ-R1, REQ-R3)
- SSE with `Last-Event-ID` replay + polling with `ETag`/`304` (REQ-R2, REQ-R4)
- Postgres schema with **row-level security** enforced at the data layer (REQ-D1, REQ-S1)
- Object-storage key convention; no blob bytes in a row (REQ-D2)
- Engagement scope lock + authorization acknowledgment before a run (REQ-P3)
- Structured logging with `run_id` correlation (REQ-S3)

## Slice 2 — one JS file → findings (in progress)

The analyze stage is real: it reads the run's JS input blob, statically traces
network calls (`fetch`/XHR/`axios`/jQuery/WebSocket) with tree-sitter, scans for
secrets with an out-of-process engine, and writes content-addressed findings
through a transactional outbox.

- Finding identity `finding_hash = sha256(type + normalized value + source path)` — spec in `docs/req-d3-finding-hash-normalization.md` (REQ-D3)
- Exactly-once findings via an outbox (REQ-A3); a normalization merge surfaces as occurrences, never a silent drop (REQ-C2)
- `finding`/`finding_occurrence` tables under row-level security (migration `0002`)
- Secret findings from MongoDB Kingfisher, run out-of-process via the engine
  harness (timeout + output cap + offline flags; OS-level sandbox deferred).
  Identity is `provider:sha256(token)` — the raw match lives only on the
  occurrence, never in the hash. A missing binary degrades coverage honestly; a
  genuine engine failure fails/retries the stage.
- Drive it over HTTP: `POST /runs/upload` (multipart `file=@bundle.js` + `session_id`)
  stores the JS blob and enqueues a run; `GET /runs/{run_id}/findings` reads back the
  findings (each with its occurrences). Service-level `coordinator.start_run_with_input(...)`
  does the same without HTTP.

Still to come this slice: Sourcemapper (source maps → real per-source paths) and
the egress sandbox + real fetch stage.

## Run in Docker (full stack)

Everything runs in containers: the stores (Postgres, Redis, MinIO), a one-shot
`migrate` job, the `api`, and the `worker`. One image serves all three app roles.

```bash
docker compose up -d --build
# migrate applies schema+RLS+app role and exits; api waits for it, then serves :8000
docker compose ps                       # api healthy, worker up, stores healthy

# create a tenant (uses the privileged admin connection — off the HTTP surface)
docker compose run --rm api python -m recon.bootstrap create-tenant "Acme Security"

# then drive it (X-Tenant-Id is the printed UUID)
curl -XPOST localhost:8000/sessions -H "X-Tenant-Id: <uuid>" \
  -H 'content-type: application/json' \
  -d '{"scope_hosts":["acme.io"],"authorized_by":"you"}'
curl -XPOST localhost:8000/runs -H "X-Tenant-Id: <uuid>" \
  -H 'content-type: application/json' -d '{"session_id":"<sid>","target":"acme.io"}'
curl localhost:8000/runs/<run_id>/status -H "X-Tenant-Id: <uuid>"
```

Compose wiring: `api`/`worker`/`migrate` share `recon-platform:local`; `api` and
`worker` wait on `service_healthy` stores **and** `migrate`
`service_completed_successfully`. Inside the network the stores resolve by
service name (`postgres`, `redis`, `minio`) via the `x-app-env` block.

## Local dev (host, without app containers)

```bash
python -m venv .venv
./.venv/Scripts/python -m pip install -e ".[dev]"   # Windows; use bin/ on POSIX
docker compose up -d postgres redis minio            # infra only
./.venv/Scripts/alembic upgrade head                 # schema + RLS + app role
./.venv/Scripts/uvicorn recon.api.app:app --reload   # API
./.venv/Scripts/python -m recon.worker.main          # worker
```

The migration provisions two Postgres roles: `recon` (owner, runs migrations)
and `recon_app` (the non-superuser role the app connects as, so RLS is actually
enforced — a superuser bypasses it).

## Tests

```bash
pytest                 # pure + fakeredis unit tests, no infra required
pytest -m integration  # end-to-end vs live Postgres/Redis (compose must be up)
pytest -m ""           # everything
```

Tests are colocated with their source (`*_test.py`). Integration coverage
includes: full run to `done`, transition atomicity under concurrency, tenant
isolation via RLS (cross-tenant read blocked), pause/resume, cancel, and
failure→DLQ. Measured enqueue latency: p50 ≈ 25ms, p95 ≈ 29ms.
