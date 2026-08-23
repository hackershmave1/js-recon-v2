# CLAUDE.md — js-recon-v2 (repo standards)

Repo-specific engineering standards for this project. General engineering defaults
(commit style, naming, review discipline) are assumed; this file records the facts
a contributor — human or agent — needs to work here correctly. Keep it current: a
change that alters these rules updates this file in the same PR.

## What this is

A static **JavaScript → API-recon security platform**: it reconstructs the backend
API surface a JS bundle talks to (endpoints, params, request shapes), scans for
secrets, and exports an OpenAPI spec of the reconstructed surface. (A consolidated
recon report and an evidence-grounded threat model over that surface are planned —
the workspace's "Threat Model" tab is marked SOON.) No active traffic against
targets — analysis is static; the fetch stage is SSRF-guarded (in-scope public
hosts only).

## Where truth lives (documentation map)

One source of truth per fact — read the doc that matches your need; don't infer it from code or
restate it elsewhere. (Each `AGENTS.md` is a pointer-stub to the `CLAUDE.md` beside it.)

| Need | Read |
|------|------|
| Repo standards — layout, run/test, CI gates, conventions | **this file** (`CLAUDE.md`) |
| Stand it up + first run + read the output (operator/QA guide) | [`docs/OPERATING.md`](docs/OPERATING.md) |
| System *what* — components, async spine, engines, ingest contract, auth | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| *Why* a load-bearing decision was made — MADR trail, each cites its code | [`docs/adr/`](docs/adr/README.md) |
| The `REQ-*` capability/invariant IDs the ADRs cite (41 + a `REQ-CE*` addendum) | [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) |
| Known, deliberate tech debt (effort + status) — before "why isn't X done" | [`DEBT.md`](DEBT.md) |
| Backend — run, test, migrations, async-pipeline detail | [`apps/platform/README.md`](apps/platform/README.md) |
| Web workspace (React/Vite SPA) | [`apps/platform/web/README.md`](apps/platform/web/README.md) |
| Finding-identity / hash-normalization spec | [`apps/platform/docs/req-d3-finding-hash-normalization.md`](apps/platform/docs/req-d3-finding-hash-normalization.md) |
| Per-slice design specs + plans (enrichment, tech-detection) | [`apps/platform/docs/superpowers/`](apps/platform/docs/superpowers/) |
| Capture extension — what it is, how to run it, MV3 invariants | [`apps/capture/CLAUDE.md`](apps/capture/CLAUDE.md) · [`apps/capture/README.md`](apps/capture/README.md) |
| Score the capture→platform pipeline vs an answer key | [`test-targets/recon-range/README.md`](test-targets/recon-range/README.md) |

CI is **two** tracked workflows: `.github/workflows/ci.yml` (the four lanes in "CI gates" below) and
`.github/workflows/security.yml` (gitleaks + pip-audit + npm-audit; advisory, per DEBT D6).

## Layout (monorepo)

```
docs/                   architecture, decision records (adr/), requirements  (routed by the map above)
apps/platform/          the product
  src/recon/            Python backend — FastAPI + Redis Streams queue + Postgres
                        (RLS, multi-tenant) + S3/MinIO blobs + a worker
  web/                  React/Vite SPA (the Recon Workspace)
  docs/                 platform design specs (finding-identity, enrichment, tech-detection)
apps/capture/
  chrome-extension/     MV3 extension: captures runtime (post-auth) JS -> the platform
DEBT.md                 tracked tech debt — read before "why isn't X done"
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
