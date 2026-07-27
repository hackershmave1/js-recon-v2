# Slice-2 deferred debt

Slice 2 is **"one JS file → findings"** (upload or a single in-scope target URL).
Auditing all 40 REQ-* IDs against that contract, three items were consciously
deferred with the user's sign-off. This ledger keeps "later" from becoming
"never": each carries what's missing, why it's safe to defer now, and the trigger
that should pull it back in.

| Item | REQ | Priority | Status now | Trigger to revisit |
|---|---|---|---|---|
| OS/network-level egress isolation | P2, T2 | **MUST** (deferred) | App-level guard only | Before running any net-emitting engine (Sourcemapper URL-fetch, Kingfisher validators) or exposing the fetcher to untrusted multi-tenant load |
| Automated asset discovery (katana crawl, gau archive, robots.txt) | C1, Q5 | SHOULD | DISCOVER stage stubbed | When scope moves from "one asset" to "crawl a host" (M3 scale) |
| Ephemeral/JIT/audit-logged secret reveal | S2 | MUST | **DONE (slice 3b)** — hash+location by default, JIT reveal by slicing the source blob, audited | — (see the slice-3b section below for residual debt) |
| Freeze migration 0001 to a static snapshot | D1 | infra | 0001/0002 use `create_all` from live metadata; 0003 guarded with `IF NOT EXISTS` | Before real prod/zero-downtime upgrades (M3) — see below |

## OS/network-level egress isolation (deferred MUST — the one to watch)

REQ-P2 says metadata/RFC1918 are "blocked at the **network layer**"; REQ-T2 wants
net-emitting engines in a "scoped egress sandbox". Today enforcement is
**application-level** (`recon/fetch/egress.py`): scheme + in-scope host + all
resolved IPs globally routable, DNS-pinned per request, redirects re-validated per
hop, scope never derived from crawled URLs.

- **Why deferred is acceptable now:** the app guard already defeats the actual
  SSRF threat for the only outbound traffic we make (the fetch stage). Kingfisher
  runs with `--no-validate` (no network); Sourcemapper's external-URL fetch is not
  wired. So no engine currently makes un-guarded outbound requests.
- **What's still owed:** OS-level isolation (network namespace + egress firewall,
  seccomp, nsjail) as defense-in-depth against a compromised worker or a shelled-
  out engine that ignores our host argument. This is the belt-and-suspenders the
  spec's "network layer" wording asks for.
- **Do not** wire Sourcemapper's external `.map` fetch (or any new net-emitting
  engine) without either routing it through the app guard or landing this
  isolation first.

## Automated asset discovery (katana / gau / robots)

The DISCOVER stage exists in the pipeline but is a stub. Crawl needs headless
Chrome, a CGO build of katana, gau, and per-host politeness at crawl scale
(REQ-Q3's robots.txt handling belongs here — it's only meaningful once multiple
paths on a host are being fetched). This is the M3 "scale" story, not "one JS
file". The `< 4 min` SLA is explicitly defined for bounded input (≤ N assets,
single host).

## Secret reveal (S2) — DONE in slice 3b

**Closed 2026-07-22 (slice 3b, both gates passed).** REQ-S2 is discharged with
storage model A: the raw secret is **never stored** — the DB keeps only the
identity hash (`provider:sha256(token)`) + byte offsets, and the run's source blob
is the single at-rest copy. The findings read redacts secret `evidence` (keyed on
`finding.type == "secret"`, so legacy plaintext rows are masked too) and adds a
`revealable` flag. `POST /runs/{id}/findings/{hash}/reveal` re-derives the value
just-in-time by slicing the blob at the stored offsets (in analyze's exact
decode-replace byte space), re-checks `provider:sha256` (fail-closed → 409 on
drift), and returns it once — auditing **every** attempt (`secret.revealed` /
`secret.reveal_denied`, value-free `run_event`). The offset convention is verified
against the real Kingfisher engine in CI. See
`docs/superpowers/specs/2026-07-22-slice3b-secret-reveal-design.md` (§10 records
the adversarial-gate findings folded in).

## Slice-3a deferred debt (manual-probe handoff)

Slice 3a (REQ-P1) is complete on `main` (both review gates passed). Deliberately
out of scope, plus residual review nits, tracked here:

| Item | Priority | Why deferred | Trigger to revisit |
|---|---|---|---|
| S2 secret reveal (ephemeral/JIT/audit) | MUST | ~~Its own sub-slice~~ | **DONE — slice 3b (see below)** |
| Static request-header extraction (Vespasian) | SHOULD | 3a reconstructs method/path/query/body; headers/auth are the manual tester's to add | Pairs with the C2 wrapper-teaching thread |
| Postman + mitmproxy exporters | SHOULD | curl + raw-HTTP cover curl + Burp; each new format is an isolated pure serializer | On demand (team/Postman or signature-replay workflows) |
| Cross-file base-URL resolution + wrapper-teaching (C2 SHOULDs) | SHOULD | No data model yet; relative endpoints render `{{base_url}}` | The C2 SHOULD thread |
| `recon_object` materialization (persisted per-op projection) | — | 3a reconstructs on-demand at read time; no persisted projection needed yet | Slice 4, when the threat model needs a stable-id projection |

**Residual review nits (non-blocking, from the final gates):**
- `TriageStatus` StrEnum: the three status values live in the model CHECK, `VALID_STATUSES`, and the API — a `recon.domain.TriageStatus` + `_enum_check` would DRY them (matches the codebase convention).
- Migration `0004` `downgrade()` hardcodes `drop_table("finding_triage")` instead of looping `TRIAGE_TABLES` (latent if the tuple grows).
- Test coverage: no `build_requests` permutation (input-order) test though determinism is now load-bearing; only WSS (not plain WS) websocket test; no assertion on the triage note/actor **return value** after a status-only upsert (DB row is asserted); `to_http` unpacks an unused `base` var (rename `_base`).

## Slice-3b deferred debt (secret reveal)

Slice 3b (REQ-S2) is complete on `main` (adversarial design gate at the design
stage + whole-branch code review, both passed). Deliberately out of scope, plus
residual review nits, tracked here:

| Item | Priority | Why deferred / safe now | Trigger to revisit |
|---|---|---|---|
| Retention / TTL + tenant-initiated purge | S4 **MUST** | Its own slice; with model A, purge collapses to the source-blob lifecycle | **Slice 5** (retention/diff) — the TTL numbers were always slated there |
| Backfill/scrub of pre-3b `evidence` rows | S2 | No real prod data; the read redaction already masks them and reveal ignores the column | Fold into slice-5 purge, or before any real data lands |
| Re-scan to relocate offset-less secrets | S2 | Rare (Kingfisher gave no locatable offset); such a finding is `revealable:false` | If offset-less secrets show up in practice and must be revealable |
| SSE-publish of the reveal audit | S3 | Reveal audit is persisted to `run_event` (durable); live streaming is not required | If a SIEM/live-audit consumer needs reveal events on the stream |
| Per-user auth for a verifiable reveal "who" | S1/S2 | Platform-wide — `X-Tenant-Id`/`actor` are best-effort labels (same as 3a triage) | When real per-user auth lands |

**Residual review nits (non-blocking, from the gates):**
- A non-`ClientError` blob-read failure now audits a `denial="error"` and re-raises
  (500) — but if `_audit` itself raised it would mask the original error; pre-exists
  on all paths, not introduced by 3b.
- Non-UUID `run_id` in the reveal/triage/reconstruct routes raises `DataError` → 500
  rather than 404 (shared pre-existing pattern; no leak).
- Test coverage: no multi-occurrence `revealable` `any()`-branch test; no route-level
  409/410 test (covered at the service layer); happy-path route test doesn't assert
  `finding_hash` in the body.
- **Pre-existing flake (NOT slice 3b):** `integration_test.py::test_duplicate_delivery_is_idempotent`
  intermittently fails under full-suite load (shared Redis stream/consumer-group
  timing); passes 3/3 in isolation. A slice-1 worker test — trace + stabilize when
  convenient.

## Migration strategy: `create_all` vs incremental DDL

`0001_initial` and `0002_findings` build tables with
`Base.metadata.create_all(bind)` from the **live** model metadata, not a frozen
snapshot. `create_all` creates any missing *table* (with all its *current*
columns), so on a from-scratch `alembic upgrade head` migration 0001 already
stands up the entire current schema — including columns that later revisions
"add". A plain `op.add_column` in a later revision then hits `DuplicateColumn` on
a fresh DB. This bit CI: `0003` added `run.source_map_ref`, which 0001 had already
created, so the first from-scratch migrate failed (it never surfaced locally
because the dev `pgdata` volume predated the column).

- **Fix applied now (minimal):** `0003` uses `ALTER TABLE ... ADD COLUMN IF NOT
  EXISTS` — a no-op on a fresh DB (0001 made the column) and still correct on an
  older DB. Any future column-add via an incremental revision must be guarded the
  same way while this pattern stands.
- **What's still owed:** freeze `0001` to an explicit, column-by-column
  `op.create_table` snapshot and stop calling `create_all` inside migrations, so
  each revision is an immutable historical step and plain `add_column` is safe.
  Do this before the platform performs real incremental upgrades against live
  tenant data (M3). Deferred here because the build is pre-prod with no data to
  preserve, and the rewrite is large and must exactly mirror the models
  (columns, FKs, indexes, RLS).
- **Detection note:** CI catches a broken migration because `api`/`worker`
  `depends_on migrate: service_completed_successfully`, so `docker compose run api`
  re-triggers migrate and fails the job. `docker compose up -d migrate` alone
  swallows the exit code — don't rely on step 1 to surface a migration failure.

---

## Slice UI-0 (first UI slice — React+Vite Recon Workspace) — deferred debt

Slice UI-0 built a thin React+Vite UI over the done backend (upload → watch → orient →
triage → reveal). Code complete on branch `slice-ui0`; both §4 gates passed (adversarial
design at design-time; higher-model whole-branch review — its one must-fix, RunProgress
error handling, was fixed). Live visual walkthrough passed against the Docker stack.

**Surfaced issues (NOT UI-0 defects — separate work):**

| Item | Where | Priority | Status | Trigger / fix |
|---|---|---|---|---|
| Reveal 409 on fresh AWS/Stripe secrets | backend `recon.findings` analyze offset + `recon.probe.reveal` | **bug** | **FIXED (7bedf21)** | Root cause: Kingfisher's line/column mark the rule *match region* (for AWS-style rules, a different line than the extracted snippet), so the derived byte offset sliced the wrong bytes → 409. Fix: `analyze._record_secret` now locates the snippet by **content** (`kingfisher.locate_snippet`, per-snippet cursor for repeats; `None`→422 when absent); real-Kingfisher round-trip test extended to AWS + Stripe on different lines. |
| Dev-only: hard-refresh on `/runs/:id` blank | `web/vite.config.ts` proxy | Minor (dev DX) | Open | The `/runs` proxy rule forwards the *document* request to the api (returns built index.html on the dev origin → missing assets). Prod (one-origin api catch-all) is fine. Fix: Vite proxy `bypass` returning the dev index.html for `Accept: text/html` navigations, or move API under a distinct prefix. |

**Deferred Minors (from per-task + final review; final reviewer = fine-to-defer):**
- `web/.oxlintrc.json` + `oxlint` devDependency unused (lint script is `tsc -b --noEmit`) — remove or wire in. Stock Vite `web/README.md` also references unused tooling.
- `sseClient.ts`: `parseFrame` drops a whole frame if its first line is a `:`-comment (theoretical — server keep-alives are standalone frames); no `AbortSignal` cancellation test; terse `h`/`e` identifiers.
- `sseClient.ts` (M-1): clean-close reconnect has no backoff/cap — a fast-closing non-terminal stream (abnormal server) would hot-loop. Add a ~1s delay before the clean-close reconnect.
- `RunProgress.tsx` / `FindingDetail.tsx`: occurrence lists use array-index React keys (harmless — append-only lists).
- `FindingsView`: the `coverage === null` branch is untested.
- `sessions_router.py` (M-2): `except IntegrityError` is broad — a future unique/check constraint on session insert would also read as "unknown tenant"; narrow or comment if constraints grow.
- `TriageControls.current` typed `string` rather than the triage-status union (compile-time only).
- `NewRunPanel.tsx` uses two `react` imports vs the combined style (arguably more correct under `verbatimModuleSyntax`; leave).

## Slice X — katana crawl discovery (surfaced issues + deferred debt)

Slice X = a real `DISCOVERING` stage: crawl an in-scope domain with **katana** →
in-scope `.js` assets manifest (surfaced via `GET /runs/{id}/assets` + UI inventory).
Fetch/analyze stay single-asset (a domain run flows DISCOVER → FETCH no-op →
ANALYZE no-op → DONE, manifest = deliverable). Standard-mode crawl **proven live**
(discovers `app.js` + `vendor.js` from the `recon.test` fixture). Both §4 gates passed:
adversarial design at design-time returned **DO NOT BUILD AS SPECIFIED** → the slice
was split into Slice X + Slice Y; the higher-model whole-branch review returned
**MERGE AFTER FIXES** — its one blocker (an existing full-run integration test that,
once discovery became real, crawled the public internet) was fixed (`a800d31`).

| Item | Where | Priority | Status | Trigger / fix |
|---|---|---|---|---|
| Headless crawl unverified in-container | `recon.discover.katana.build_argv` (headless branch) | Deferred (opt-in) | Open | katana's go-rod launcher **rejects the vendored Debian chromium** (`-system-chrome-path` → "system chrome binary does not exist") and downloads its own (~13s, which eats `-crawl-duration`). `crawl_headless` defaults **False** (standard, proven), so this doesn't gate the happy path. Fix: pre-bake go-rod's chromium revision into the image OR attach via `-chrome-ws-url`; account for browser launch time in the crawl budget. |
| Crawl-time subresource SSRF | headless crawl (Chrome loads subresources outside `egress.py`) | Accepted residual risk | Open | App-level egress only (scope flags + per-URL `egress.validate_target` on manifest entries). Hardening path: deployment network control (no route to metadata/RFC1918) → forced egress proxy enforcing `egress.py` → netns/nftables. |
| `discover_run` crawls for any in-scope host target | `recon.discover.crawl.discover_run` | Minor (latent) | Open | It crawls for **any** run whose `target` is a non-empty in-scope host, not only bare-domain crawl runs. No shipped UI path hits this (upload → `target=None` → early-return; crawl → bare domain), but an API-only `POST /runs` with a full asset URL would trigger a domain crawl (and an out-of-scope `target` `FatalError`s a run that previously succeeded). Guard: only crawl when `target` has no path. |
| Live in-UI crawl-mode walkthrough not done | `web/` crawl mode | Deferred | Open | Needs the Task-9 SPA baked into the api image = a full docker rebuild (~hours in this env). Standard crawl proven at the katana/parse layer instead. |

**Deferred Minors (per-task + final review; reviewer = fine-to-defer):**
- `harness.py`: temp-file fd leak if `subprocess.Popen` raises before the `try` — wrap `out` in `try/finally`.
- `storage.py:22-24`: `BLOB_KINDS` comment not updated to mention `"assets"`.
- `queries.py`: `dict(row[0])` redundant copy; the "event exists but no `assets_ref` → None" branch is untested.
- FE `AssetsInventory`: renders for every run (upload runs show a permanent "0 assets · pending" card); no loading placeholder; list `key={a.url}` (dup-source risk).
- `harness_test.py` backstop test asserts `killpg` called but not the `getpgid(pid)` argument.
- Some Slice X commit bodies are trailer-only.

## Slice Y — multi-asset analyze

Slice Y is **"multi-asset fetch/analyze"** (loop the discovery manifest), completing
multi-asset coverage (REQ-C1/C2), the occurrence **asset-dimension identity** (REQ-D3),
**PARTIAL** completeness on a failed asset (REQ-D5), and secret-reveal routing to
per-asset blobs (REQ-S2). Both §4 gates passed: adversarial design review returned
**BUILD WITH CHANGES** (five code-verified blockers all folded into the spec; see
`docs/superpowers/specs/2026-07-26-slice-y-multi-asset-design.md` §13); higher-model
code review returned **MERGE AFTER FIXES** — one existing integration test unrelated to
Slice Y was fixed, and all amendments are documented in the spec's §15.

**Surfaced issues and deferred debt:**

| Item | Priority | Why deferred / safe now | Trigger to revisit |
|---|---|---|---|
| Per-asset fetch retry (transient 5xx/429) | SHOULD | Best-effort drops failed asset (→ `failed` status, run → `PARTIAL`); queue's retry/backoff not applied per-asset | When partial-fetch robustness at scale becomes load-bearing |
| Analyze mid-scan heartbeat | SHOULD | A per-asset `kingfisher.scan` (up to `engine_timeout_seconds`=120s) can exceed the 30s job lease with no mid-scan beat, so a peer can reclaim and re-run the analyze loop. Correctness-safe (REQ-A3 outbox upserts are idempotent + analyze-terminal assets are skipped), only wasteful. Pre-existing in single-asset analyze; Slice Y amplifies it ×N | When avoiding duplicate work on long scans matters; fix mirrors the crawl harness's in-subprocess beat (`harness.py:67-74`) |
| Long-stage stream-reclaim strand | SHOULD | `progress.beat` renews the DB job lease but never touches the Redis stream, so `reclaim_stalled` can hand a long stage's message to a peer that loses `claim_job` and acks it (removing it from the PEL); if the original then crashes the job is stranded. Pre-existing with Slice X crawl; Slice Y inherits it on fetch + analyze stages (two more long-running stages) | Stabilize at scale when message reclaim coordination must be bulletproof |
| Commit-time DB error inside per-asset analyze transaction | SHOULD | Recorded as permanent per-asset `analyze_failed` (→ run `PARTIAL`) rather than propagating to job-level retry. Structural tension with the per-asset-commit requirement (findings + status must share one txn); `fetch.py` avoids it by keeping blob-store + status commit outside any `try`. Post-commit `publish` failure already handled correctly (try/except/else) | When analyzing per-asset infra-error handling for robustness |
| Dual asset-list source of truth | SHOULD | The discovery manifest blob (URL list, for the API/UI) and the `run_asset` rows (per-asset processing state) both list assets. Each serves a distinct purpose (read-only manifest vs. mutable status); unify only if drift occurs | If manifest/`run_asset` versioning causes observable inconsistency |
| Queue fan-out / crawl parallelism (model C) | SHOULD | Fetch/analyze loop assets sequentially in one job (required by the fetch DNS-pin single-thread invariant); parallel per-asset jobs deferred to scale | M3 parallelism milestone when per-asset throughput dominates the path |
| OpenAPI/Swagger export | SHOULD | The other half of "complete the first chunk" (multi-asset coverage) — not yet wired | Slice after Y, or on demand when tooling/integration partners need OpenAPI export |
| Per-asset secret scanning of recovered source-map files | SHOULD | Pre-existing analyze follow-up (source maps recovered from bundles), now also relevant per-asset | When decompiled-map secret-scanning is prioritized after the main path |
| Live in-UI multi-asset walkthrough | SHOULD | Not done; needs the SPA baked into the api image (a multi-hour rebuild), same deferral as Slice UI-0. The FE components + Vitest coverage shipped | When the UI image is rebuilt; meantime, standard crawl proven at the backend layer |
| Multi-asset end-to-end automated test is host-partial | SHOULD | A real katana crawl→fetch→findings e2e cannot be automated here: `egress.validate_target` rejects the fixture-site's private Docker IP (empty manifest) and we must not auto-crawl a public domain. `multi_asset_integration_test.py` Part A proves the fetch+analyze+finalize pipeline with the network stubbed (host-green); Part B (katana/parse ≥2 .js) is engine-gated and runs only in CI/container | Pre-prod constraint; when deploying to a gated staging environment with real domain access, run Part B |

**Slice Y (the second half of the gate-deferred multi-asset build):** fetch/analyze loop
the manifest, the occurrence **asset-dimension migration**, secret-reveal routing to
per-asset blobs, and **PARTIAL** completeness (REQ-D5). **OpenAPI/Swagger export** is
the other half of "complete the first chunk".
