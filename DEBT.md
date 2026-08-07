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

### D2 · Ruff format sweep + broaden the ruleset [M] — ✅ RESOLVED 2026-08-07
Two isolated commits: (1) `style:` `ruff format` across the backend — 111/167 files
reflowed, pure formatting (fast lane stayed green; SHA in `.git-blame-ignore-revs` so
blame skips it); (2) broadened `select` to `F,I,UP,B,C4,SIM,PIE,RET` +
`extend-immutable-calls` for the FastAPI DI markers (the 9 `B008` sites are the
framework idiom, not bugs). Applied 22 safe + 8 verified-equivalent unsafe autofixes
+ 4 hand-fixes (2 `SIM117` combined-`with`, 1 `SIM115` `# noqa` for a deliberate
long-lived Popen handle, 1 `B017` → specific `FrozenInstanceError`). CI's host-tests
lane now also runs `ruff format --check src` so the format can't drift.
**Deferred** (tracked follow-up): `TC`/`TCH` (39 stylistic typing-only-import
relocations that fight `from __future__ import annotations`). Both §4 gates passed.

### D3 · mypy — no Python type checking [M–L] — ✅ RESOLVED 2026-08-07
Introduced mypy 2.3.0 (dev extra + `uv.lock`) with a per-module `strict = true`
override on `recon.findings.*` + `recon.spec.*` in `[tool.mypy]`; the base config
follows all other imports *silently* so out-of-scope errors (e.g. `db/models.py`'s
194) never enter this gate, and colocated `*_test.py` are excluded. Fixed all 37
resulting errors: 25 pure annotations (`no-untyped-def` + `dict[str, Any]` generics),
5 SQLAlchemy DML `Result` typings (`cast(CursorResult[Any], …).rowcount`), and 7
targeted fixes. The 3 real None-safety sites were resolved by *invariant*, not blind
guard: an `assert asset.input_ref is not None` in `analyze._analyze_assets` (an
OK-fetched asset always has `input_ref` — `runs/assets.set_fetch_ok` writes it +
`fetch_status=OK` atomically, and the loop only reaches OK assets); `(node.text or
b"")` in `extract._text` (tree-sitter stub Optional, matching the fn's empty-on-
absence contract); and `row.reason or ""` in `queries._run_spec_summary` (value-
neutral — a null reason can never equal `"suffix-verify"`, the only value `summarize`
reads). No stub packages (untyped 3rd-party → `Any`); the one `SafeLoader` subclass in
`ingest.py` carries `# type: ignore[misc]` (adding `types-PyYAML` would *introduce* a
new `no-untyped-call`). CI's host-tests lane now runs `mypy src/recon/findings
src/recon/spec` (hermetic) and fails loudly if the override ever stops matching (no
silent non-strict downgrade). Fast lane stays 421-green; ruff clean. Both §4 gates
passed (design: Meta SHIP / Google BUILD-WITH-CHANGES — both deltas simplifications;
code: SHIP WITH ONE NIT, nit addressed). **Widen next**, module-by-module; `db/models.py`
(194) is the natural follow-up. Design: `docs/superpowers/specs/2026-08-07-mypy-d3-design.md`.

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

**Update 2026-08-07 — Dependabot version-updates PAUSED (config removed):** at the
maintainer's request ("too early to be dealing with it"), `.github/dependabot.yml` was
removed after its first run opened 15 update PRs (#6–#20, all closed). This pauses only
the *auto-update-PR bot*; the *scanning* half of D6 — `gitleaks` + `pip-audit` +
`npm audit` in `security.yml` — is untouched and still gates, so D6's core resolution
stands. Repo-level Dependabot alerts + security auto-updates were already off
(`automated-security-fixes` → `enabled:false`, `vulnerability-alerts` → 404), so no
version PRs can regenerate. To re-enable later, restore `.github/dependabot.yml` from
PR #5 (`git checkout 922335c -- .github/dependabot.yml`).

### D7 · Image build isn't lock-pinned [M] — ✅ RESOLVED 2026-08-07
`apps/platform/Dockerfile` now installs Python deps from the committed lock instead
of `pyproject` `>=` floors: a `deps-export` stage runs `uv export --frozen --no-dev`
to a hash-pinned `requirements.txt`, and the runtime stage `pip install -r`s that,
then `pip install --no-deps .` for the project (kept NON-editable — a real wheel
build — so its packaged `findings/rules/*.yml` data stays validated in the image, the
gap the integration-lane AKIA test catches). The image now matches CI's
`uv sync --frozen`; verified by a full image build. Web was already `npm ci`-pinned.
Both §4 gates passed.

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

### D11 · Files over the ~300-line cap [M] — ⏳ PARTIAL 2026-08-07 (extract.py split)
`findings/extract.py` (639, 2.1x) split into a 3-module import DAG — `_jsast.py` (185,
leaf: tree-sitter parser/AST helpers + value dataclasses + param builders) ← `_base_env.py`
(251, REQ-C2 base-URL resolution) ← `extract.py` (276, network-sink handlers + `extract()`).
Pure move (per-symbol AST diff proved byte-identical; §4 code gate SHIP-WITH-NITS), with an
`__all__` re-export shim so `analyze.py`/`classify.py`/tests keep importing `RawEndpoint`,
`HTTP_METHODS`, `collect_base_env`, `_PARSER` from `recon.findings.extract` unchanged (matters
under D3's now-strict `no_implicit_reexport`). All three modules are mypy-strict-clean.

**Deferred (evidence-backed, both §4 design engineers):**
- `db/models.py` (596) — DON'T split: a cohesive declarative schema (17 classes + 35
  FK/`back_populates` cross-refs + the RLS `*_TABLES` tuples read by Alembic `env.py`). With
  `from __future__ import annotations`, `relationship()` targets resolve only via the class
  registry, so a package split makes `__init__` load-bearing for ORM registration (a bypassing
  `from recon.db.models.run import Run` silently breaks `configure_mappers()`). High fragility,
  zero behavior gain.
- `api/capture_router.py` (654) — DEFER: the D1/D8a tests *rendezvous-monkeypatch*
  `capture_router.<helper>` (e.g. `monkeypatch.setattr(capture_router, "_run_has_job", …)`);
  moving a handler/helper to a sibling module silently breaks the patch (a test would pass while
  testing nothing). Also can't reach the cap (~420 residual). Needs a careful test-aware slice.
- `findings/analyze.py` (659, the largest) — DEFER: a clean record-trio seam exists but it
  touches the outbox/RLS/REQ-A3–A4 invariants and `reextract.py` imports `_extract_endpoints`;
  higher-risk, own slice. `findings/queries.py` (455) + `fetch/fetch.py` (462): smaller
  overages, low priority (fetch is SSRF-fail-closed-critical — don't fragment).
(The DEBT.md counts above were stale pre-split; actuals measured 2026-08-07.)

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
