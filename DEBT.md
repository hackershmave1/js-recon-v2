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

### D14 · Concurrent `analyze/start` can double-enqueue a walk [S] — pre-existing, low-impact
`apps/platform/src/recon/api/capture_router.py` `analyze_start`: the `_run_has_job`
idempotency gate (:421) and the seal+Job-insert transaction (:446) are separate, and
`job` has no unique key on `(run_id, stage)` (`db/models.py`), so two simultaneous
`analyze/start` calls for one session can both pass the gate and enqueue two
DISCOVERING walks. Pre-existing (the old code had the same gate→enqueue TOCTOU) and
self-limiting — the coordinator's guarded state transitions make the second walk a
no-op (`runs/service.py:_apply_transition` → `TransitionConflict`, caught in
`coordinator.advance`) — so it wastes work, not data. `analyze/start` is a single
user click, so the window is tiny. Close later by gating the Job insert on a
guarded UPDATE (rowcount) or a partial unique index on the QUEUED discover job.
Surfaced by the D1 §4 code review; left out of that slice on purpose (no run
state-machine changes).

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

### D4 · TypeScript strict off [M]
`apps/platform/web/tsconfig.app.json` lacks `"strict": true` — implicit `any` +
null-unsafety pass the build. Enable + burn down the ~5 feature pages.

### D5 · Coverage ratchet [ongoing]
Floor is `--cov-fail-under=55` (fast-lane coverage is ~58.6%). Ratchet the floor up
as coverage grows; never lower it.

## Supply chain / security (a security tool with an unscanned supply chain)

### D6 · No dependency/secret scanning [S–M]
No `dependabot.yml`, no `pip-audit`/`npm audit`, no `gitleaks`, no CodeQL. Add each
(~one config file). High embarrassment / low effort for a tool whose job is scanning
other people's code.

### D7 · Image build isn't lock-pinned [M]
`host-tests` now installs `uv sync --frozen`, but the Dockerfile (integration lane +
prod image) still `pip install`s from `pyproject` `>=` floors. Pin the image build to
the lock too.

## Maintainability

### D8 · Unversioned contracts [M]
The extension↔platform ingest contract (`capture_router.py`) and the OpenAPI export
(`probe/reconstruct.py`) carry no version field and no consumer-contract test. Add a
version + a schema test that fails on wire-shape drift (Hyrum's law).

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
