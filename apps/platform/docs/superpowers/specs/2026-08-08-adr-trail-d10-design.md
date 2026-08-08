# DEBT D10 — ADR trail (design)

**Slice:** add an Architecture Decision Record trail (MADR) and backfill the shipped
architectural decisions, so the "why" stops living only in per-slice specs + off-repo
AI memory. **Status:** design ratified by both §4 gates (2026-08-08).

## Problem

`DEBT.md` D10: *Architectural "why" (Redis Streams, RLS-in-DB, SIGSTOP-rejected,
export-only GraphQL) lives in per-slice specs + off-repo memory. Add `docs/adr/`
(MADR); backfill ~8.* There is no `docs/adr/` today, and the canonical "what" doc
(`docs/ARCHITECTURE.md`, repo-root, product-level) has no "why" companion.

## Decision (reconciled from the two §4 design engineers — both BUILD WITH CHANGES)

- **Format: MADR 4.0**, trimmed. Load-bearing sections: *Context & Problem Statement ·
  Considered Options · Decision Outcome (+ Consequences) · Confirmation*. `Decision
  Drivers`, `Pros and Cons`, `More Information` are optional (MADR-native). Frontmatter:
  `status`, `date`. The `superseded-by`/`supersedes` affordance is modeled in the
  template for future use. Drop `decision-makers`/`consulted`/`informed` (noise for a
  small repo). The **Confirmation** section — a pointer to the exact code/test that
  enforces the decision — is the trail's anti-drift anchor and is kept on every ADR.
- **Location: repo-root `docs/adr/`**, beside `docs/ARCHITECTURE.md`. The ADRs are the
  "why" companion to that repo-root, cross-app doc; the set includes genuinely
  cross-app / product-level decisions (v1 convergence; static/no-active-traffic stance)
  that do not belong under `apps/platform/`. `DEBT.md:150` says "Add `docs/adr/`"
  (matching the existing repo-root `docs/`). Files `NNNN-kebab-title.md` from `0001`,
  plus `0000-adr-template.md` and a `README.md` index.
  - *(Engineer divergence — resolved.)* Meta = repo-root; Google = `apps/platform/docs/adr/`
    (6/7 decisions are `recon.*`-internal; shorter, restructure-safer test path). Resolved
    to **repo-root** because the log's center of gravity is the repo-root ARCHITECTURE.md it
    explains, and splitting a decision log across two trees is worse than one home. Google's
    path-brittleness concern is neutralised by resolving the dir via an **upward walk to the
    repo root** (not a fixed `parents[N]`), which is restructure-proof.
- **Status is per-decision-reality, not a blanket `accepted`.** All eight backfilled
  decisions are shipped on `main` → `accepted`. A constant status field is dead metadata.
- **The decision set (8, all grounded on `main`; GraphQL dropped):**
  1. Redis Streams task broker — **at-least-once folded in** (a property of the broker +
     consumer-group + idempotent-consumer choice, not an independent technology).
  2. Postgres row-level security for tenant isolation.
  3. Cooperative, orchestrator-level run pause (**not** OS signal-stop).
  4. Content-addressed blob storage (S3/MinIO, sha256 key).
  5. SSRF egress guard, fail-closed.
  6. Static analysis, no automated active/exploit traffic (product stance).
  7. Single analysis core / v1 convergence (no jsluice+REP union) — the repo's one real
     *supersession* (`ARCHITECTURE.md` "Convergence history" rejects the "complementary
     cores" draft assumption).
  8. Hardened out-of-process engine harness (one timeout / output-cap / exit-allowlist /
     non-root harness for all engine binaries).
- **GraphQL export-only is NOT an accepted ADR — it is not on the trunk.** `grep graphql`
  over `apps/platform/src` = 0 hits; it is parked on the unmerged `feat/enrichment@58de1a9`
  (D13). A `status: accepted` ADR would have no Confirmation anchor. It is instead recorded
  **honestly as a corollary** in ADR-0006's consequences: outputs are *static exports*
  (OpenAPI today; GraphQL SDL / Swagger 2.0 / gRPC are deferred *export* formats per the
  openapi-export spec §9), never a served/active GraphQL API.

### Honesty guards (both engineers; the point of D10 is a "why" that is still true in a year)

Three decisions have rationale that is **reconstructed from off-repo memory**, not proven by
a repo artifact — the ADRs must say so and cite the memory, not imply the repo proves them:
- **Pause (ADR-0003):** the string `SIGSTOP` appears nowhere in code/specs — only in the
  D10 task text (`DEBT.md`/`HANDOFF.md`). The *mechanism* (flag polled at safe checkpoints)
  is grounded (`worker/main.py:101-135`); the SIGSTOP *rejection* is design-time judgment
  (memory `run-pause-model`).
- **Redis Streams (ADR-0001):** the requirements name a *candidate set* ("Redis Streams /
  RabbitMQ", "Python / Celery" — `Developer Requirements.dc.html:456,460`), not a recorded
  choose-and-reject. The "why Redis over RabbitMQ/Celery" is off-repo (memory
  `slice1-foundation-choices`).
- **Static / no-active-traffic (ADR-0006):** must be scoped honestly — the platform *does*
  make outbound requests (SSRF-guarded JS-asset fetch; a **user-initiated** manual probe).
  The stance is "no *automated active/exploit* traffic," with runtime-evidence ingest
  (Burp/HAR) recorded as a *deliberate future relaxation*, not an absolute.

### Distill, don't duplicate

ADRs are the durable 1-page distillation; the rich per-slice specs
(`apps/platform/docs/superpowers/specs/…`) stay the detail. An ADR **links** its spec in
*More Information* rather than copying it (two sources of truth drift). Each backfill carries
a "Recorded retroactively 2026-08-08" line so the file date is not mistaken for the decision
date.

## Test (TDD artifact) — index-integrity + metadata, not prose-lint

A single hermetic test, at **`apps/platform/src/recon/adr_structure_test.py`** (must be under
`src/` and named `*_test.py` — `pyproject.toml` pins `testpaths=["src"]`, `python_files=
["*_test.py"]`, and CI runs from `apps/platform`; a test anywhere else is never collected =
a dead gate). It resolves the ADR dir by walking **up** from `__file__` to the repo root
(the ancestor containing `docs/adr`), so no fixed `parents[N]` and no `**/adr/**` glob (which
would sweep the stale `.claude/worktrees/**/docs` copies).

Asserted invariants (structural/metadata that genuinely rot — *not* section-heading prose,
which couples every doc edit to CI-green and is a review catch, per Meta's YAGNI point):
- filename matches `^\d{4}-[a-z0-9-]+\.md$`;
- 4-digit numbers are **unique** (not strictly contiguous — removal shouldn't red-build);
- frontmatter has an ISO `date` and a `status` matching
  `^(proposed|accepted|rejected|deprecated|superseded by .+)$` (MADR `status` is an open set,
  not a closed enum — a future supersession must not fail);
- the `README.md` index and the ADR files are **bidirectionally** consistent (every ADR is
  linked; every link resolves) — the one thing that predictably rots as the set grows.
- `0000-adr-template.md` is excluded from the content assertions (its placeholders would
  false-fail).

Coverage-neutral (`--cov=recon` measures product lines; a markdown-reading test executes
none). Not mypy-gated (mypy is scoped to `findings`+`spec`).

## Out of scope

- A machine-checkable Confirmation-pointer resolver (would force format rigidity on human
  docs for ~8 files — over-engineering; the pointers are policed by review).
- Section-heading presence linting (Meta: docs-lint / YAGNI).
- New ADRs for unbuilt/parked work (GraphQL export — resume with D13/enrichment).

## §4 gates

- **Design (this doc):** Meta IC8 = BUILD WITH CHANGES (6 must-fixes: GraphQL-not-accepted;
  per-decision status; test→index-integrity-only; test forced into `src/` + repo-root walk;
  add convergence ADR; distill-don't-duplicate). Google staff = BUILD WITH CHANGES (5:
  location→test-collection; GraphQL drop/propose; flag reconstruct-from-memory decisions;
  MADR-correct open-set status + exclude template + single-dir scope; honest static-stance
  scope). Reconciled above; the one divergence (location) resolved to repo-root with a
  repo-root-walk in the test.
- **Code review:** higher-model subagent = CHANGES REQUIRED → 1 must-fix (ADR-0007
  overstated `apps/capture/` contents — reworded to match `ARCHITECTURE.md`'s "removed
  `apps/capture/{api,web}`") + 5 citation-precision nits (ARCHITECTURE line-drift from the
  cross-link insert; outbox anchor → `findings/store.py`; harness `killpg` line
  `:94-100`; append-only-table mis-cite; `*_TABLES` consumed by migration bodies not
  `env.py`). All fixed; ~50 other citations + all three honesty framings verified clean →
  SHIP.
