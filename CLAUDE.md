# CLAUDE.md — js-recon-v2 (repo standards)

Repo-specific engineering standards for this project. General engineering defaults
(commit style, naming, review discipline) are assumed; this file records the facts
a contributor — human or agent — needs to work here correctly. Keep it current: a
change that alters these rules updates this file in the same PR.

## What this is

A static **JavaScript → API-recon security platform**: it reconstructs the backend
API surface a JS bundle talks to (endpoints, params, request shapes), scans for
secrets, and emits an OpenAPI spec + a recon report. No active traffic against
targets — analysis is static; the fetch stage is SSRF-guarded (in-scope public
hosts only).

## Layout (monorepo)

```
docs/                   ARCHITECTURE.md (the "what") + adr/ (MADR decision trail, the "why")
                        + REQUIREMENTS.md (the 41 REQ-* IDs)
apps/platform/          the product
  src/recon/            Python backend — FastAPI + Redis Streams queue + Postgres
                        (RLS, multi-tenant) + S3/MinIO blobs + a worker
  web/                  React/Vite SPA (the Recon Workspace)
  docs/                 req-d3-finding-hash-normalization.md (the finding-identity spec)
apps/capture/
  chrome-extension/     MV3 extension: captures runtime (post-auth) JS -> the platform
DEBT.md                 tracked tech debt (owners + effort) — read before "why isn't X done"
```

Engines: **Vespasian** (in-process tree-sitter — endpoints/params, engine tag
`vespasian`), **Kingfisher** (secrets, real pinned binary), **Sourcemapper** (source
maps). Kingfisher is secrets-only.

## Running + testing

Backend — from `apps/platform`:
- Install (reproducible): `uv sync --frozen --extra dev`
- Fast lane (no infra; what CI gates): `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration"`
- Lint: `uv run ruff check src`  ·  Full stack: `docker compose up -d --build`
- Integration lane (needs live PG/Redis/MinIO): `uv run pytest` with
  `RECON_S3_ENDPOINT_URL=http://localhost:9000 RECON_S3_ACCESS_KEY=recon RECON_S3_SECRET_KEY=recon-secret RECON_S3_BUCKET=recon-artifacts`

Frontend — from `apps/platform/web`: `npm ci` · `npm run lint` (oxlint + tsc) ·
`npm test` (vitest) · `npm run build`.

Tests are **colocated** (`*_test.py` next to source; `*.test.tsx` for web).

## CI gates (enforced on the trunk — `.github/workflows/ci.yml`)

`main` is the trunk; land work via a PR per slice. A green build means all four
lanes passed:
- **host-tests**: `uv sync --frozen` (reproducible) → `ruff check src` + `ruff format
  --check src` → `mypy src/recon/findings src/recon/spec` →
  `pytest -m "not integration" --cov=recon --cov-fail-under=60`, with
  `RECON_REQUIRE_ENGINES=1` (a missing engine is a hard failure, not a silent skip).
- **frontend**: `npm ci` → `npm run lint` (oxlint + tsc) → vitest → build.
- **extension**: runs the MV3 capture extension's `tests/test_*.mjs` Node suites
  (`apps/capture/chrome-extension`) — dependency-free, so no `npm ci`/build (DEBT D16).
- **integration-tests**: builds the app image (compiles the pinned Sourcemapper),
  brings up the stores, runs the whole suite incl. real-engine + integration tests.

Don't add a gate that isn't green on current code (a red trunk is worse than no
gate). Ruff runs `F,I,UP,B,C4,SIM,PIE,RET` + `ruff format --check`, and mypy is
`strict` on `recon.findings.*` + `recon.spec.*`; widening mypy module-by-module and
ratcheting `--cov-fail-under` are the tracked follow-ups (DEBT.md), not a big-bang.

## Conventions

- **File size**: cap ~300 lines; split when exceeded (some files currently over —
  see DEBT.md). Single responsibility; business logic, state, and UI in separate files.
- **Review gates**: a slice gets an adversarial design review (before build) and a
  higher-model code review (after) — evidence-backed, not rubber-stamp.
- **Comments** explain the *why* (vendor constraints, non-obvious invariants), not
  the *how*.
- **Idempotency + fail-closed** are load-bearing here (SSRF egress guard, at-least-once
  queue, content-addressed blobs). Don't weaken them for convenience.
