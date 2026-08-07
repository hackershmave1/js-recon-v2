# Tech debt register

Known, deliberate debt — written down so it's visible to the next contributor (and
the next session) instead of living only in an AI's memory. Add here when you defer
something on purpose; link the code with a `# NOTE(DEBT):` comment where it helps.
Effort: S (hours) · M (a day-ish) · L (multi-day).

## Correctness

### D1 · Capture get-or-create race — silent duplicate sessions/runs [M] — ✅ RESOLVED 2026-08-07
Fixed via approach A (design: `apps/platform/docs/superpowers/specs/2026-08-07-capture-race-fix-design.md`).
Added dedicated idempotency-key columns `session.external_id` + `run.capture_external_id`
(migration 0011), each with a `UNIQUE(tenant_id, …)` index (NULLS DISTINCT — only
capture rows bind); `capture_router` keys get-or-create on them and self-heals on
`IntegrityError`. The open capture "round" is the run whose `capture_external_id` is
set; `analyze/start` seals it by nulling the marker in the SAME transaction that
inserts the Job (so a run can never be sealed-but-jobless, which would re-orphan JS).
The singleton capture tenant uses a `pg_advisory_xact_lock` (its `name` is
deliberately non-unique). Covered by a live-PG two-writer concurrency test
(`capture_router_test.py`, verified red-without-index → green-with-index). Both §4
gates passed (design: BUILD WITH CHANGES; code: SHIP).

### D14 · Concurrent `analyze/start` can double-enqueue a walk [S] — ✅ RESOLVED 2026-08-07
Closed via a guarded-seal CAS in `capture_router.py` `analyze_start`: the seal that
nulls the `capture_external_id` marker is now `UPDATE run SET capture_external_id=NULL
WHERE id=:run AND capture_external_id IS NOT NULL`, and only the caller whose
`rowcount == 1` proceeds to insert the DISCOVERING Job — the loser returns the
idempotent "already started" (mirrors `runs/service._apply_transition`'s guarded-UPDATE
idiom; relies on D1's "a capture run has a Job ⟺ its marker is NULL" invariant). Two
concurrent `analyze/start` calls now enqueue exactly ONE walk. Covered by a live-PG
two-writer test (`capture_router_test.py::test_concurrent_analyze_start_enqueues_one_walk`,
verified **red — 2 jobs — without the guard, green with it**). No migration (reuses
D1's atomic seal); approach A (a partial unique index) was rejected as unnecessary
heft since `analyze_start` is the sole capture enqueue path. Both §4 gates passed.

## Enforcement / tooling (deferred from the CI-keystone slice)

### D2 · Ruff format sweep + broaden the ruleset [M]
`ruff check` is scoped to `F,I` (real-bug + import-sort, green today). `ruff format`
would restyle **110 of 165 files** and the code was never formatted; the broader
`UP`/`B`/`SIM` modernization rules add ~60 more findings. Do the format sweep as one
isolated `style:` commit, then broaden `select`. Kept out of the keystone to avoid a
blame-churning big-bang. (Note: `B008` is FastAPI `Depends()` in defaults — configure
`extend-immutable-calls`, don't "fix" it; `BLE001` blind-except in the worker loop is
intentional.)

### D3 · mypy — no Python type checking [M–L]
No mypy/pyright anywhere. Introduce incrementally: `--strict` on `recon.findings` +
`recon.spec` first, widen module-by-module, then gate in CI.

### D4 · TypeScript strict off [S] — ✅ RESOLVED 2026-08-07
Enabled `"strict": true` in both `apps/platform/web/tsconfig.app.json` and
`tsconfig.node.json`. The "~5 feature pages to burn down" estimate was pessimistic —
the code was already written null-safe, so a forced clean typecheck
(`tsc -b --noEmit --force`) is **0 errors**; lint + build + 133 vitest tests stay
green, and CI's `frontend` lane (`tsc -b --noEmit`) now enforces it. Both §4 gates
passed (design: SHIP AS-IS; code: APPROVE). Deferred (a separate future slice, NOT
this one): the beyond-umbrella flags `noUncheckedIndexedAccess` /
`exactOptionalPropertyTypes` / `noImplicitReturns` add ~20 errors on current code, so
enabling them means a real burn-down.

### D5 · Coverage ratchet [ongoing]
Floor is `--cov-fail-under=55` (fast-lane coverage is ~58.6%). Ratchet the floor up
as coverage grows; never lower it.

## Supply chain / security (a security tool with an unscanned supply chain)

### D6 · No dependency/secret scanning [S–M] — ✅ RESOLVED 2026-08-07
Added `.github/dependabot.yml` (weekly version-update PRs for the `uv` Python project
+ both npm projects + github-actions + Docker base images), `.gitleaks.toml`, and
`.github/workflows/security.yml` (push/PR + weekly schedule) with three advisory
gates: `gitleaks dir` (secret scan of the working TREE — history is intentionally NOT
scanned: a secret-detection tool's history is saturated with fixture tokens, measured
at 437 fixture-only findings across 380 commits vs 0 in the tree; a one-time history
triage confirmed all 437 sit in fixture/test/deleted-v1 paths, no real leak),
`pip-audit` (clean today), and `npm audit --audit-level=high` for web + extension.
Fixed a pre-existing dev-only `undici` HIGH in web via a non-breaking `npm audit fix`
so the web gate is green. CodeQL DEFERRED (needs GitHub Advanced Security, unavailable
on private Free-tier — the same limit that blocks branch protection). Both §4 gates
passed (design: BUILD WITH CHANGES, then ENDORSED the working-tree-scan pivot).

### D7 · Image build isn't lock-pinned [M]
`host-tests` now installs `uv sync --frozen`, but the Dockerfile (integration lane +
prod image) still `pip install`s from `pyproject` `>=` floors. Pin the image build to
the lock too.

## Maintainability

### D8 · Unversioned contracts [M] — D8a ✅ RESOLVED 2026-08-07; D8b open [S]
Two wire contracts carried no version field and no consumer-contract test.

**D8a (capture ingest — DONE):** `capture_router.py` now stamps a server-authored
`CAPTURE_CONTRACT_VERSION` on the `GET /api/health` handshake (response-side only —
additive, so deployed extensions that ignore the health body aren't broken), and a
hermetic fast-lane `capture_contract_test.py` pins the wire shapes the extension
depends on (health / save-files / analyze-start / progress envelopes + the
`GET /api/projects` bare-array invariant), so drift fails in the fast lane instead of
silently in prod. Both §4 gates passed (design: BUILD WITH CHANGES; code: APPROVE
WITH NITS).

**D8b (OpenAPI export — OPEN):** the export serializer is `probe/openapi.py` (NOT
`probe/reconstruct.py` as previously written here); it already emits `openapi: 3.0.3`
with a colocated `openapi_test.py`, so the remaining work is a small explicit
contract/version marker + a drift test. Must precede any D13 (enrichment) resume,
which extends that output.

### D9 · Test-pyramid inversion [L, ongoing]
42/75 backend test files need live PG/Redis/MinIO; the fast hermetic layer is the
minority, so the heavy lane catches most real bugs. Grow the small-test layer.

### D10 · No ADR trail [M]
Architectural "why" (Redis Streams, RLS-in-DB, SIGSTOP-rejected, export-only GraphQL)
lives in per-slice specs + off-repo memory. Add `docs/adr/` (MADR); backfill ~8.

### D11 · Files over the ~300-line cap [M]
`findings/extract.py` 619, `db/models.py` 586, `findings/analyze.py` 556,
`api/capture_router.py` 519, `findings/queries.py` 455, `fetch/fetch.py` 422. Split
the top few by responsibility.

### D12 · Stale branches [S]
`spike/platform-ingest` (now == `main`), the `claude/*` worktree branches, and
`claude/busy-boyd-e00cc4` (its AKIA fix is superseded on main) can be pruned +
`git worktree prune`.

## Parked work

### D13 · Enrichment slice [M] — parked, design-gated
`feat/enrichment` @ `58de1a9`: param risk-tags + header→securitySchemes + GraphQL
export-only. Spec + §4 design gate done (BUILD WITH CHANGES, 5 must-fixes captured in
`apps/platform/docs/superpowers/specs/2026-08-07-enrichment-design.md`). Resume after
the hardening slice.
