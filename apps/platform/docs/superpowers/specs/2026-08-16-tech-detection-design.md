# Tech detection (fingerprinting) slice — design

Status: DESIGN (not built). Date: 2026-08-16. Grounded against the current fetch / analyze /
capture code (file:line below) and the adversarial design gate (Meta + Google IC8, 2026-08-16).
Delivery model: **SaaS / internal only** (user, 2026-08-16) — the GPL-3.0 dataset stays
server-side and is never conveyed, so copyleft is not triggered (see G + T10).

## Goal

Give every recon run a **per-host technology stack** — server, framework, CDN, JS libraries,
analytics — with **versions where detectable**, surfaced per session in the Recon Workspace and
structured to **feed the (future) threat model** (known-CVE surfaces, framework-specific attack
paths). Built entirely from signal the platform **already collects**; no new active traffic
beyond what fetch/capture already do; no secret storage; static-analysis honesty preserved.

## Locked decisions (user, 2026-08-16)

1. **Purpose = feed the threat model.** Structured, version-aware, reliable; accuracy over UI polish.
2. **Signal source (REVISED after the gate) = match over ALLOWLISTED evidence from data already
   collected.** Rich path (capture runs): response headers + `html`/`meta` read from the rendered DOM
   in-stream (DOM never persisted) + (Phase 2) runtime `js`. Static path (crawl runs): per-asset
   response headers + `scriptSrc` + the `scripts`-field over the JS bytes already stored. **No raw HTML
   persisted, no full header maps, no dedicated root-document GET** (all three were in the pre-gate
   design and are dropped — see §Gate).
3. **Granularity = per host**, where host comes from **observed asset URLs**, not
   `session.scope_hosts` (empty on capture runs — see grounding).
4. **Detail = name + categories + version + confidence + evidence** per detected technology.
5. **Placement = a pure matcher inside ANALYZE**, beside Vespasian + Kingfisher. All network I/O
   stays in fetch/capture; ANALYZE is a pure function over stored signal.
6. **Engine = in-house pure-Python matcher** over the vendored `enthec/webappanalyzer` fingerprint
   JSON (GPL-3.0, server-side only), matched with `google-re2` (linear-time / ReDoS-safe); versions
   via POST-match capture-group substitution.
7. **One results table** `run_technology`; **migration 0016**; new `TECH_TABLES` RLS group.

## Grounding (verified facts this design rests on)

- **ANALYZE runs for every run mode.** Linear stage order `DISCOVERING → FETCHING → INGESTING →
  ANALYZING → CORRELATING` (`runs/state_machine.py:24-31`); the worker dispatches `analyze_run`
  unconditionally (`worker/main.py:82-83`). So a matcher in ANALYZE always runs.
- **The capture/upload paths carry no scope and no target.** Capture sessions are created with
  `scope_hosts=[]` (`api/capture_router.py:232,246`) and their run with `target=None`
  (`api/capture_router.py:305`); discover/fetch **no-op** on a target-less, pre-fetched run
  (`discover/crawl.py:45`, `fetch/fetch.py:171,200-201`) and `_fetch_assets` skips already-sealed
  assets (`fetch/fetch.py:334`). ⇒ a fetch-side root-document GET can never run there — exactly the
  path with the richest JS — so header/runtime signal must be harvested in the **capture stage**, and
  host must come from asset URLs, not `scope_hosts`.
- **`fetch_url` returns bytes only** (`fetch/fetch.py:119-170`); the full response (headers) is in
  scope at `:145` but only `location`/`retry-after` are read (`:148-159`).
- **The platform never stores raw credentials.** Secrets are hashed, never stored
  (`findings/analyze.py:633-637`); the auth surface is captured as names + scheme only
  (`findings/analyze.py:557-560`); even engine stderr is kept off persisted fields because a scanner
  "can echo matched content" (`findings/engines.py:36-42`). ⇒ the allowlist gate (T1).
- **Migration head is `0015_finding_type_unresolved.py`** — the branch advanced past `0014` via PR #64
  (`05c56fa`) while this was being designed; `0011` was already taken too. The new revision is
  **0016**, `down_revision="0015_finding_type_unresolved"`.
- **`RECON_REQUIRE_ENGINES` is test-only** — read in `conftest.py:27-30` and test modules only, no
  production reader. It cannot gate dataset presence for a pure-Python engine (T7).
- **`google-re2` ships cross-platform wheels** — `win_amd64`/`win32` + manylinux, latest
  `1.1.20251105` ([PyPI](https://pypi.org/project/google-re2/)). Pin a version with a wheel for our
  interpreter on both win + linux. (Refutes the "no Windows wheels" gate claim.)
- **RE2 substitution premise holds, but not every pattern compiles.** `\;version:\1` is a *post-match*
  capture-group substitution, not an in-regex backreference — RE2-safe. But some enthec patterns use
  lookbehind (e.g. Cargo `(?<!elo\.io)/cargo\.`), which RE2 rejects at compile time
  ([RE2 syntax](https://github.com/google/re2/wiki/Syntax)) ⇒ per-pattern defensive compile (T4).
- **The dataset's version-rich `js`/`dom` fields are runtime-only.** enthec `js` matches live JS
  property *values* (e.g. `jQuery.fn.jquery`), not bundle source; `scripts` matches JS source URLs;
  `dom`/`css`/`text` need a rendered DOM. ⇒ static runs get sparse versions; precise library versions
  come from the capture stage's real browser (Phase 2) — scope honestly (T12).
- **RLS pattern:** tenant-scoped table groups + FORCE RLS in the migration + writes via
  `tenant_session` (`db/base.py:35-54`, `db/models.py:576-608`, `migrations/versions/0005_run_asset.py:47-56`).
- **SSRF guard:** `egress.validate_target` + DNS-pinning `_pin_dns`, per-hop re-validation
  (`fetch/fetch.py:131-139`, `fetch/egress.py:220-269`); the pin is a process-global `getaddrinfo`
  override (`fetch/fetch.py:10-15,70-107`) ⇒ single-threaded fetch worker only (T5).
- **Idempotent writes:** `pg_insert(...).on_conflict_do_*` is the established pattern
  (`findings/store.py:101-102,135`).
- **Best-effort isolation:** a raise out of `analyze_run` hits the worker's generic handler →
  retry/DLQ → run FAILED, destroying all findings (`worker/main.py:164-177,220-228`); enrichment must
  be swallowed like spec-reclassify (`runs/coordinator.py:353-356`) / the per-asset best-effort branch
  (`findings/analyze.py:254-257`) (T2).
- **Heartbeat / interruptibility:** `raise_if_control_requested` is checked in the stage loops
  (`findings/analyze.py:222`, `fetch/fetch.py:336`); a stall past
  `heartbeat_stall_threshold_seconds=30` (`config.py:185`) is reclaimed and re-run (T4/heartbeat).
- **Packaging gotcha:** vendored data must be declared package-data or the wheel drops it — the class
  of bug already hit once with the Kingfisher AKIA rule (`pyproject.toml:77-81`).
- **mypy `--strict`** applies to `recon.findings.*` with a CI "unused override" tripwire
  (`pyproject.toml:134-139`, `.github/workflows/ci.yml:45-51`) (T8).

---

## A · Signal harvest (fetch + capture)  [M]

All network I/O stays here; the matcher (C) is pure. Harvest is **allowlist-only** (T1):

- **Allowlisted response-header subset** (case-insensitive): `server, x-powered-by, x-aspnet-version,
  x-aspnetmvc-version, x-generator, x-drupal-dynamic-cache, x-drupal-cache, via, x-varnish,
  cf-ray, x-amz-cf-id, x-served-by, x-shopify-stage, x-github-request-id, x-fastly-*`, plus **Set-Cookie
  NAMES only** (never values). Header VALUES for non-allowlisted headers are discarded.
- **`scriptSrc`**: the `src` URLs of `<script>` tags / fetched JS assets (already known — asset URLs).
- **`html`-field markers / `<meta name=generator>`** (capture runs only): read by capture from the
  rendered DOM **in-stream** — the raw DOM is never persisted, only the extracted marker strings.
  Static crawl runs have no page DOM, so they rely on headers + `scriptSrc` + the `scripts`-field.
- **`scripts`-field** (both modes): matched in ANALYZE over the JS bytes already stored (input blobs) —
  NOT copied into the signal blob (see C); the signal blob stays small and secret-free.

**fetch (crawl/static):** extract a shared validated-hop core
`_fetch_hops(url, scope_hosts, ...) -> (body, status, headers)`; `fetch_url` becomes a thin wrapper
returning `.body` — **its public signature is unchanged** (the SSRF crown jewel is not churned, T5).
In `_fetch_assets`, capture the allowlisted header subset per asset.

**capture (CDP):** in `capture/driver.py`, record allowlisted headers from `Network.responseReceived`
per script/asset; in `capture/stage.py`, harvest `<meta generator>` + script srcs + cookie names from
the page. (Phase 2 adds a bounded runtime js/dom property probe — see fast-follows.)

**Persist as ONE per-run signal blob** (not a table, not per-asset): `storage.put_blob(tenant, run,
"fingerprint-signal", json)` where the JSON is keyed by host:
`{ host: { headers: {...allowlisted...}, scripts: [...], meta: [...], cookies: [names] } }`.
This mirrors the assets / `capture-requests` manifest-blob pattern (`storage.py:33`,
`discover/crawl.py:118-123`) and keeps the only new **table** to the results (D). Written **once per
fetch/capture run** (consolidated — T6). Add `"fingerprint-signal"` to `storage.BLOB_KINDS`
(`storage.py:24-38`). No `"html"` blob kind; no raw HTML anywhere.

> Note vs. the verbal design summary: the earlier "storage untouched" is refined here — the allowlisted
> signal needs a home, and a manifest blob is the table-free, RLS-free idiomatic choice. Still no raw
> HTML, still one results table.

Tests: fetch allowlist (an `Authorization`/`Set-Cookie` value is NOT persisted; `Server`/`X-Powered-By`
is); capture responseReceived header capture; signal blob is one-per-run and host-keyed.

---

## B · Engine — `findings/techdetect/` package  [L]

Split into <300-line modules (T9):

- `dataset.py` — load the vendored enthec JSON (technologies + categories), lru-cached; package-data.
- `compile.py` — a **typed** `re2` adapter; compile each pattern in `try/except re2.error`, **skip +
  count + log** rejects (T4); expose a typed match surface so `recon.findings.*` stays mypy-strict (T8).
- `version.py` — the tag mini-parser: split each pattern on `\;`, apply `version:<template>` by
  substituting `\1`,`\2`… from capture groups (incl. the ternary `\1?present:absent`), read
  `confidence:<n>`.
- `match.py` — apply compiled patterns across the signal surfaces the dataset defines (`headers`,
  `cookies`, `scriptSrc`, `js`(Phase 2), `meta`, `html`-markers).
- `__init__.py: detect(host, signal) -> list[Detection]` where
  `Detection(name, categories, version|None, confidence, evidence)`.

Semantics: **confidence is summed across matching patterns, capped at 100** (enthec confidence is
designed to combine toward 100 — T3-conf); on conflicting versions keep the highest-confidence
pattern's version and record the alternates in `evidence`. `implies`/`requires`/`excludes` are NOT
followed in Phase 1 (flat list — fast-follow).

New dependency `google-re2` (`pyproject.toml`, pinned; `uv lock`). Vendored data under
`findings/techdetect_data/` (enthec JSON pinned to a commit + `refresh.py` to re-pin + a `NOTICE`).

Tests (pure): a fixture host with `Server: nginx`, `X-Powered-By: Express`, a `react`/`jquery-3.5.1.js`
scriptSrc, `<meta generator=WordPress 6.4>` → expected techs + versions; ternary version template;
confidence summed across two patterns; a lookbehind pattern is skipped + counted (not fatal); the
pinned dataset loads and compiles within a bounded, asserted skip count.

---

## C · Analyze integration  [M]

In `analyze.py`, after per-asset analysis, a **best-effort per-host fingerprint pass**:

1. Load the run's `fingerprint-signal` blob; group by host.
2. `techdetect.detect(host, signal, js_texts)` per host — `js_texts` are the host's stored JS bytes
   (already loaded in ANALYZE for Vespasian/Kingfisher, reused) for the `scripts`-field, with **size
   caps** on both the signal and the JS fed in.
3. **Upsert** `run_technology` via `pg_insert(...).on_conflict_do_update(index_elements=[run_id, host,
   name])` (redelivery-safe — T3).
4. Emit an `analyze.technologies` RunEvent (per-host counts + dataset commit + skipped-pattern count),
   commit-then-publish like existing events (`findings/analyze.py:423-440`).

The entire pass is wrapped so any exception is swallowed and logged — it must **never fail the run**
(T2). Between hosts, heartbeat + `raise_if_control_requested` (REQ-A4). Dataset load/compile failure →
fail-closed **skip** (log a health metric) at runtime; presence is guaranteed by the B load-time test,
not `RECON_REQUIRE_ENGINES` (T7).

Tests: best-effort (a forced `detect` raise does NOT fail `analyze_run`); idempotency (deliver ANALYZE
twice → one row per `(run_id, host, name)`); a capture run and a crawl run each yield per-host rows.

---

## D · Data model + migration  [S]

**One** table (`db/models.py`), per-run snapshot (CASCADE, like `Finding`):

```
run_technology(
  id uuid PK, tenant_id uuid FK tenant CASCADE, run_id uuid FK run CASCADE,
  host text, name text, categories jsonb, version text NULL,
  confidence int, evidence jsonb, created_at timestamptz,
  UNIQUE(run_id, host, name), INDEX(tenant_id, run_id))
```

`evidence` holds only allowlisted material (which surface + the matched pattern + a short snippet
**bounded to the matched marker**) — no secrets, no raw HTML (T1). Add `TECH_TABLES = ("run_technology",)` to the RLS groups
(`db/models.py:576-608`). Migration **`0016_technology.py`**
(`down_revision="0015_finding_type_unresolved"`) creates the table and FORCE-enables RLS, mirroring
`0005_run_asset.py:47-56`. `storage.BLOB_KINDS` gains
`"fingerprint-signal"` (A). No `run_host_document` table; no `"html"` blob.

---

## E · API  [S]

`api/tech_router.py` — `GET /runs/{run_id}/technologies` → `queries.list_technologies(tenant_id,
run_id)` under `tenant_session`, returning a per-host-grouped dict, 404-on-None; mirrors
`findings_router.py:18-96` exactly (thin router, `Depends(get_tenant_id)`, no business logic). Register
in `api/app.py`. Add `list_technologies` to `findings/queries.py`.

Tests: shape + 404 on unknown run + RLS isolation (a second tenant sees none).

---

## F · Web UI  [M]

- `api/apiClient.ts` `getTechnologies(runId)` + `api/types.ts` `Technology`.
- `features/progress/runData.tsx` — fold `technologies` into `RunData` so all panels share it.
- `features/overview/OverviewPanel.tsx` — a "Tech stack" metric card + top techs as chips
  (chip idiom `OverviewPanel.tsx:80`).
- `features/tech/TechPage.tsx` (A) + `shell/Sidebar.tsx` entry + `main.tsx` `/runs/:id/tech` route —
  the per-host detail table (name · category · version · confidence · evidence), the threat-model-grade
  surface.

Tests (vitest): card + view render from `RunData`; empty state.

---

## G · Licensing / ops  [S]

- Root `LICENSE` + `NOTICE` recording the enthec GPL-3.0 dataset provenance and "server-side only,
  never bundled into the distributed extension."
- **CI guard**: a step that fails if the extension build (`apps/capture/`) imports or bundles
  `techdetect_data` — makes "server-side only" enforceable, not a convention (T10).
- `pyproject.toml`: `google-re2` pinned; `techdetect_data` declared package-data (wheel gotcha).

---

## Invariants / traps

- **T1 (allowlist / no secrets):** persist only allowlisted headers + cookie NAMES + short evidence
  snippets; never raw HTML, full header maps, or any value. Pinned by fetch/analyze tests.
- **T2 (best-effort):** the fingerprint pass is swallowed; a raise must never fail `analyze_run` (else
  DLQ → run FAILED → all findings lost).
- **T3 (idempotency + confidence):** upsert on `(run_id, host, name)`; confidence summed/capped, not
  last-writer.
- **T4 (defensive RE2 compile):** per-pattern `try/except re2.error`, skip + count + log; never
  all-or-nothing; a load-time test asserts a bounded skip count on the pinned dataset.
- **T5 (SSRF):** reuse `validate_target` + `_pin_dns` via the shared hop-core; no parallel `httpx`
  path; do not mutate `fetch_url`'s signature; single-threaded fetch worker (process-global DNS pin).
- **T6 (consolidate signal):** write ONE `fingerprint-signal` blob per fetch/capture run — a per-asset
  write + "read latest" would drop all but the last asset (the enrichment M4 trap).
- **T7 (no RECON_REQUIRE_ENGINES gating):** pure-Python dep is always present; fail-closed load +
  load-time test, not the test-only flag.
- **T8 (mypy strict):** typed `re2` adapter + `TypedDict`/`cast` for the vendored JSON; keep the ORM
  model in `db/models.py` (not strict).
- **T9 (file cap):** `techdetect` is a package of <300-line modules.
- **T10 (GPL server-side):** dataset only under `apps/platform/src/recon/…`; CI guard blocks extension
  import; `LICENSE`/`NOTICE` present; delivery model is SaaS/internal (re-confirm before any conveyance).
- **T11 (host source):** host from observed `run_asset` URLs, never `session.scope_hosts` (empty on
  capture).
- **T12 (version honesty):** Phase 1 versions come from headers / scriptSrc / meta; precise runtime
  library versions are Phase 2. Do not over-claim in UI or export.

## Fast-follows (out of scope this slice)

- **Phase 2 — capture-time runtime probe:** a bounded, timed `Runtime.evaluate` of the dataset's `js`
  property paths present in the page → precise library versions (jQuery, React, Shopify…).
- `implies` / `requires` / `excludes` → the dataset's stack graph (Phase 1 emits a flat list).
- Dataset auto-refresh automation (Phase 1 = manual pinned refresh via `refresh.py`).
- The threat-model **consumer** wiring (the consumer stage does not exist yet — this slice only
  *produces* the structured feed).
- Per-asset (CDN-host) header nuance beyond the app host.

## Acceptance

- Host-lane unit tests green: techdetect (fixtures, ternary version, confidence sum, RE2-reject
  skip+count, dataset load), fetch allowlist, analyze best-effort + idempotency, queries RLS, api 404.
- Frontend: card + Tech view render + empty state (vitest); build green.
- Integration: a capture run and a crawl run each produce per-host `run_technology`; no
  secret/raw-HTML persisted; a forced fingerprint error does not FAIL the run.
- `ruff` (`F,I,UP,B,C4,SIM,PIE,RET` + format) + mypy `--strict` on `recon.findings.*` + `--cov-fail-under=60`
  all hold.
- Both §4 gates: adversarial design review of THIS doc (done — below); higher-model code review of the diff.

## §4 design-gate verdict (2026-08-16): BUILD WITH CHANGES (folded)

Two adversarial IC8 reviews (Meta = systems/infra; Google = security/correctness) ran against the live
code + PyPI/enthec sources. The core was CONFIRMED sound — ANALYZE runs for every mode; the RE2
substitution mechanism; the RLS + manifest-blob patterns; and the SSRF path (conditional on the shared
hop-core, no parallel client, single thread). The verdict was "do not build as specified"; every
must-fix is folded into the decisions/traps above:

- **Signal reversed to capture-stage + allowlist** (not raw-HTML/full-header persistence) —
  security (Google B1) + reach on the `scope_hosts=[]` / `target=None` capture path (Meta B1).
- **Runtime-vs-static version honesty** — `js` is runtime, not source bytes (Google M1) ⇒ T12 + Phase 2.
- **Best-effort + heartbeat + interruptible + upsert** (Meta M1/M2/M3) ⇒ C + T2/T3.
- **Per-pattern RE2 compile + summed confidence** (Google M2/M3) ⇒ B + T4/T3.
- **Migration 0016** (head advanced to `0015_finding_type_unresolved` via PR #64), not 0011 (both) ⇒ D.
- **One table, manifest blob for signal** (Google M4) ⇒ A + D.
- **GPL LICENSE/NOTICE + CI guard, SaaS-only** (Meta M7 / Google M5) ⇒ G + T10.
- **Fail-closed dataset load, not `RECON_REQUIRE_ENGINES`** (Meta M5) ⇒ T7.
- **Typed re2 adapter for mypy-strict; package-data** (both) ⇒ B/G + T8.
- **`analyze.technologies` RunEvent** (Meta) ⇒ C.

Refuted and dropped: "google-re2 has no Windows wheels" — PyPI ships `win_amd64`/`win32` wheels
(latest `1.1.20251105`); still handled via per-pattern compile (T4).
