# Tech debt register

Known, deliberate debt — written down so it's visible to the next contributor (and
the next session) instead of living only in an AI's memory. Add here when you defer
something on purpose; link the code with a `# NOTE(DEBT):` comment where it helps.
Effort: S (hours) · M (a day-ish) · L (multi-day).

## Correctness

### D1 · Capture get-or-create race — silent duplicate sessions/runs [M] — needs a design decision
`apps/platform/src/recon/api/capture_router.py` `_get_or_create_tenant` (:80),
`_get_or_create_session` (:108), `_accumulating_run_id` (:142) are all
SELECT-then-INSERT with no lock. **Corrected diagnosis (2026-08-07):** an earlier
audit assumed an `IntegrityError` here, but `Tenant.name` and `session.name` carry
**no unique constraint** (`db/models.py:64,123`), so two concurrent batches for the
same `ext_session_id` (a retry overlapping the first request, or two extension
instances) don't crash — they **silently create duplicate sessions/runs**, and
`analyze/start` then analyzes only one, orphaning the other's captured (post-auth,
un-recapturable) JS. The extension uploads batches sequentially, so the window is
narrow, but the failure is silent data loss.
Fix is a real choice (NOT the savepoint-self-heal the audit implied — there's
nothing to conflict on):
- **A (recommended):** add a dedicated `external_id` column + `UNIQUE(tenant_id,
  external_id)` (migration 0011), key get-or-create on it, self-heal on conflict.
  Doesn't overload the free-form user-facing `name`, which the platform intentionally
  allows to repeat.
- **B:** a Postgres advisory lock (`pg_advisory_xact_lock`) keyed on
  (tenant, ext_session_id) serializing the get-or-create — migration-free, but must
  span the currently-separate get-or-create transactions.
- **C:** accept + document the narrow race; lightly guard the accumulating-run dedup.
Whichever: needs a real two-writer concurrency test against live PG (the CI blind
spot — the integration lane serves unique bytes so the collision can't form) and
both §4 gates. `run_asset` seeding is already on-conflict-safe (`runs/assets.py`), so
that facet is fine.

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
