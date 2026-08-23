# Tech debt register

Known, deliberate debt - written down so it's visible to the next contributor (and
the next session) instead of living only in an AI's memory. Add here when you defer
something on purpose; link the code with a `# NOTE(DEBT):` comment where it helps.
Effort: S (hours) · M (a day-ish) · L (multi-day).

**Organized by user impact (2026-08-18).** Open items are grouped into three tiers by
how much a user actually feels them - most-impactful first, then ranked within each
tier. Resolved items are kept below as a record. Each item keeps its original category
tag (correctness · enforcement/tooling · supply-chain/security · maintainability) so
nothing is lost in the regrouping. Nothing is currently parked.

- **Tier 1 - you'd feel this:** visible in results today, on real targets.
- **Tier 2 - fix before it scales up:** safe now for a single trusted operator; matters
  before untrusted / multi-tenant load or a live-data upgrade.
- **Tier 3 - behind the scenes:** developer-facing hygiene; a user never sees it.

## Open debt - by user impact

### Tier 1 · you'd feel this

Visible in results today, on the real targets you point the tool at. Fix these first.

**D31–D35 added 2026-08-23 from a dogfood audit** against a real ~4.4 MB minified React SPA
(an Azure-AD-fronted app). The tool reported "2 files, 0 secrets, 10 hosts"; manual review found
a source map, real config secrets, real in-scope hosts in the file tail, and a host inventory that
was ~95% library boilerplate. Each item below is root-caused with a fix path (four-agent research
swarm, evidence-backed). Cross-cutting theme: **two of these are SILENT** (D31, D32) — the run
finalizes DONE with no partial-coverage signal, which is the honesty (REQ-C2/REQ-D5) half of the bug.

#### D31 · Large-file AST node-budget silently curtails endpoint/host recovery [S–M] — ✅ RESOLVED 2026-08-23  ·  correctness
✅ **RESOLVED 2026-08-23.** Shipped both halves. **Honesty:** an `Extraction.curtailed` flag, set in
`extract()` whenever the node budget bounds the walk, is threaded through `_EndpointExtraction` →
`Coverage` + the `analyze.coverage` event payload + a `log.warning("analyze.extract_curtailed")`, the
`_merge_coverage`/`_merge_coverage_payloads` roll-ups, the `CoverageView` read model, the
`/runs/{id}/findings` API, and a `--warn` "Partial" banner on the Overview page; the out-of-band
re-extract (which emits no coverage event) logs its own curtailment. A curtailed extract is now
surfaced everywhere, never silent (REQ-C2). **Recall:** `_MAX_AST_NODES` raised 2M → **6M** — above a
DEFAULT-cap (10 MiB ≈ ~5.5M-node) real bundle, so genuine default runs no longer curtail (recovers the
dogfood 31→88 sinks + tail hosts). **Deliberate deviations from the sketch below:** (a) kept 6M FIXED
rather than byte-keyed, to keep `extract()` pure (no settings dependency) — a run that raises its byte
cap toward the 32 MiB ceiling (~18M nodes) can still curtail, but is now FLAGGED, which is exactly what
the honesty flag is for; (b) the "never silent" signal is the flag on the existing `analyze.coverage`
event + a `log.warning`, NOT a new persisted run event — matching the actual
`analyze.asset_failed`/`fetch.source_map_skipped` convention (both are log lines) and avoiding a
redundant event + second merge site; (c) the run is NOT finalized PARTIAL on curtailment (it is not an
asset FAILURE; `_finalize_state` keys off asset status, not coverage) — a `# NOTE(DEBT D31)` at the flag
records the REQ-D5 hazard so a future run-to-run diff downgrades a "removed" endpoint/host to "unknown"
on a curtailed run. **DoS (honest):** raising the cap re-admits 2M–6M-node crafted inputs to a full
sink+harvest, forfeiting the ~15s the 2M cap saved on the D21 nested-concat shape (~25s → ~40s, crossing
the 30s lease); tolerable only because the lease breach is idempotent self-healing double-work (REQ-A3 +
analyze-terminal skip) and the densest inputs already breach it inside the unbounded `collect_base_env`.
**Still owed (deferred, [M–L]):** the strategic follow-up below — a heartbeat threaded through ALL passes
— remains the real DoS fix and is NOT closed by this change. Tests: new hermetic
`findings/node_budget_honesty_test.py`; fast lane + frontend green; ruff + mypy-strict clean. Both §4
gates passed (design: BUILD-WITH-CHANGES, all must-fixes folded; code: SHIP-WITH-NITS, the substantive
nit folded). Original analysis below.

The static extractor caps its two expensive AST walks (the sink walk + the off-sink route harvest)
at `_MAX_AST_NODES = 2_000_000` nodes once `tree.root_node.descendant_count` exceeds it
(`findings/extract.py:116`, `_jsast.py:209`, enforced by node-count in `_jsast._walk`). The base-env
poison/const pre-pass is deliberately NOT curtailed — it must see the whole tree for soundness (a name
shadowed past the prefix could fold to a stale value = a false-positive URL; `extract.py:111-115`,
`_base_env.py:39-52`). **Impact (measured, 4.4 MB bundle = 2.36M nodes):** the 2-millionth preorder node
sits at 83.8% of the file, so all sink/harvest work over the final ~16% is dropped **silently** (no log,
event, or completeness flag). Recall fell **88 → 31** unresolved network sinks (~65% lost) and two real
in-scope hosts in the file tail were dropped entirely. Any bundle over ~2M nodes (~3.5 MB minified —
common for real SPAs) reports a fraction of its surface with no warning. **The budget barely helps DoS
(measured):** the crafted-input worst case is dominated by the UNBOUNDED parse (~10.7s @ 10 MiB) +
`collect_base_env` (~30.6s @ 10 MiB — already over the 30s job lease), which the node cap never touches;
what actually bounds crafted input is D21's algorithmic `_merge_spans` O(n²)→O(n log n) fix + idempotent
at-least-once reclaim (D23), not the cap (it saves ~0–2s of noise on a real file). Real-file `extract()`
runs ~13s uncurtailed — half the lease. **Fix (recommended, both S, ship together):** (1) *honesty* — add
a `curtailed`/completeness field to `Extraction` (`_jsast.py:154-173`), set at `extract.py:116`, surface
it on asset coverage + a `job.warning` event so a partial extract is never silent (REQ-C2); (2) *recall* —
raise/re-key `_MAX_AST_NODES` above the real-file ceiling (~6M; a real 10 MiB file ≈ ≤5.5M nodes at
~0.53 nodes/byte) or key it to the 10 MiB ingest byte cap (recovers 31→88 + the tail hosts). **Strategic
follow-up [M–L]:** thread a heartbeat callback through ALL passes (base-env's two walks + sink + harvest),
renewing the lease every N nodes, so even a 40s+ crafted extract stays lease-safe — the invariant the node
budget only pretended to hold (the pre-existing base-env/parse lease breach on crafted 10 MiB+ input is
orthogonal and is not worsened by raising the cap). **Trigger:** any target bundle > ~2M AST nodes.

#### D32 · Source-map recovery: oversized-map soft-drop + recovered sources not secret-scanned, both silent [S / M–L]  ·  correctness
Two independent gaps hide everything that lives only in a JS source map (the recovered original source,
plus any secrets in `sourcesContent`). **(a)** The `.map` fetch inherits the *shared* bundle byte cap: the
crawl stage reads `//# sourceMappingURL=` and GETs the map, but passes the same per-run
`cap = min(max_fetch_bytes 10 MiB, ceiling 32 MiB)` as `max_bytes` (`fetch.py:575,631,751-757`). A real map
is 3–6× the minified bundle, so a 4.4 MB bundle's ~15–25 MB map trips the streamed cap (`fetch.py:277-282`)
and is swallowed as a soft miss (`fetch.py:759-761`, logged `fetch.source_map_skipped`, verbatim
`response exceeds 10485760 bytes`) → no `source_map_ref` → analyze `source_map:"none"`. **(b)** Kingfisher
runs once on the raw bundle only (`analyze.py:576,581`); recovered source-map units are fed only to
`extract()` for endpoints (`analyze.py:466`), never to `kingfisher.scan` (explicit deferral,
`analyze.py:597-599`) — so a recovered map's `sourcesContent` secrets (e.g. a hardcoded JWT, which
`kingfisher.jwt.1` WOULD flag) are missed. **Silent (REQ-D5 hole):** a soft-missed map leaves the asset
`fetch_ok`+`analyze_ok` → run finalizes DONE, not PARTIAL (`coordinator.py:376-378`), and `source_map:"none"`
is indistinguishable from "no map existed", so a "complete" run that skipped a map could later license a
"secret removed" diff. **Fix (recommended order):** (C, S — honesty first) record a `fetch.source_map_skipped`
run event + a distinct `source_map:"skipped"` coverage status and finalize the run PARTIAL (zero
memory/DoS/egress change); (A1, S) add a separate `max_source_map_bytes` (default = engine cap, 32 MiB) and
pass it — not the shared bundle cap — to the map `fetch_url`; (B1, M–L) scan recovered sources through
`kingfisher.scan` with `source_path=f.path` AND teach `probe/reveal.py::_derive` to re-derive recovered
content from `source_map_ref` (mirroring `probe/sources.py:207-234`) so a recovered-source secret round-trips
(else audited reveal 409s) — dedup is free (same finding, extra occurrence). **Invariants:** egress
re-validated on every map hop (intact today, `fetch.py:242`); content-addressed blobs; no silent under-report
(REQ-D5). **Note:** the single-URL/upload fetch path has NO source-map logic at all (only crawl + capture do).

#### D33 · Secret detection misses config-key / GUID exposure (precision-first ruleset) [S + M]  ·  correctness
Secret scanning = Kingfisher 1.106.0 built-in provider ruleset (~930 rules) + one custom AWS-AKIA rule, at
default `--confidence medium` (`kingfisher.py:211-227`) — precision-first by design. Two structural COVERAGE
gaps (not entropy): **(1)** config identifiers named `*_KEY` don't match — Kingfisher's Azure GUID rules
(`azure.7` Entra Tenant ID, `azure.8` Client ID) are keyword-anchored on `..._ID`/`client_id`/`appId`, so a
`*_TENANT_KEY/*_CLIENT_KEY/*_API_KEY: '<guid>'` assignment never matches (verified 0 hits on a real Azure-AD
`config.js`), and `azure.7/8` are additionally `visible:false` under `--no-validate` (tallied but not emitted —
the same non-emission the custom AKIA rule was written to work around); **(2)** info-disclosure classes
(RFC1918 internal IPs) have no built-in rule. (A well-formed JWT is NOT a gap — `kingfisher.jwt.1` is
`visible:true` and emits at medium; the audit's JWT was missed only because it lived in an un-fetched `.map`
— see D32.) **Fix — precision-first default + opt-in recall lane (a product decision):** (A, S, ~0-FP, ship
now) one `visible:true` custom rule for a UUID assigned to a `TENANT/CLIENT/APPID/APP_ID/*_KEY` identifier —
verified **19/19** real config GUIDs (incl. commented env blocks), **0 FP** across 4.4 MB of minified JS (the
readable-identifier + quoted-UUID shape can't match minified output); pin provider in
`normalize._PROVIDER_BY_RULE`; respect the custom-rule gotchas (`pattern: |-`, omit `min_entropy`, wheel
`package-data` for `rules/*.yml`, `--rules-path` cache key). (B, M, opt-in) a broader
keyword/`*_KEY=UUID`/RFC1918 heuristic lane at `--confidence low`, surfaced as a NEW **suspected-secret** tier
(mirroring `endpoint_unresolved`/`endpoint_generic`) so recall rises (~50% FP like generic scanners) WITHOUT
polluting the high-precision default lane or the REQ-D5 diff — requires a new `FindingType`. **Reject C**
(lowering the global `--confidence`): turns the ~50%-FP generic lane on for every scan, contradicting the
honesty/precision stance. **Invariants:** REQ-S2 (raw value never in identity cleartext —
`normalize_secret_value` sha256); REQ-D5 (opt-in recall must be a distinct surface); fail-closed engine contract.

#### D34 · Endpoint/host inventory polluted by off-sink library-boilerplate URLs [S] — ✅ RESOLVED 2026-08-23  ·  correctness
✅ **RESOLVED 2026-08-23.** Shipped a layered `_harvest_denied` predicate at the single harvest
chokepoint (`findings/extract.py`): (1) a scheme allow-list (`http/https/ws/wss`) drops `file://…`
junk; (2) an EXACT host-or-dot-suffix denylist of namespace registrars + JS-library PROJECT
domains (replacing the broken 5-substring test); (3) an `http://` XML-namespace shape rule (no
query/fragment, a NON-TERMINAL `/YYYY/` segment, not API-ish) that generalizes to unlisted
registrars. On the real bundle the harvested Hosts inventory went from ~10 library-noise hosts to
**0** (the real `login.microsoftonline.com` + two `*.accenture.com` hosts survive); confirmed-`endpoint`
lane untouched by construction. Colocated tests in `findings/harvest_filter_test.py` (11). §4
adversarial review = SHIP-WITH-NITS → all three nits folded: dropped bare public-suffix
`github.io` / third-party `fb.me` / `npms.io` (a bare public suffix could shadow a target's own
`*.github.io` host); required a non-terminal year so a trailing id segment (`/products/2020`) is
not mistaken for a namespace year; and claim the denied builder's span so a denied composite can't
leak a truncated sub-literal. Fast lane + mypy-strict + ruff clean. Residual (documented): a few
host-LESS route literals (`http://`, `http://macVmlSchemaUri`) remain but never reach the Hosts
inventory (no ≥2-label host). Original analysis below.

The off-sink route harvest (`findings/extract.py::_harvest_routes`, :316-374) turns ANY absolute-URL string
literal (has `://`, passes `_looks_like_route`, not `_looks_api_ish`) into a `page_route`/`endpoint_generic`
by SHAPE ALONE — no requirement it flow to a network/nav sink. The only host filter is `_HARVEST_HOST_DENY`
(:193), a 5-entry raw SUBSTRING test that is near-empty AND wrong both ways: `schema.org` is not a substring
of `schemas.openxmlformats.org` (misses it), and `example.com` IS a substring of `notexample.com` (would
false-drop a real host). Host attribution (`egress.attributed_host`) then launders any valid ≥2-label host
into the Hosts inventory (`hosts.py` route roll-up). **Impact (real bundle w/ SheetJS + React):** of 63
unique harvested routes, ~95% are OOXML/ODF XML-namespace + library doc URLs (`schemas.openxmlformats.org`,
`schemas.microsoft.com`, `openoffice.org`, `purl.oclc.org`, `reactjs.org`, …); only one was a genuine host.
The confirmed-`endpoint` lane is IMMUNE by construction (it emits only from resolved sink URLs via
`normalize_endpoint`), so the fix is safe — it touches only the harvest lane. **Fix (recommended, S — one
predicate at the `extract.py:366` chokepoint, layered):** (a) replace the substring test with EXACT
host-or-dot-suffix match (`host==d or host.endswith("."+d)`, mirroring `egress.host_in_scope`), seeded with
namespace registrars + lib-doc hosts; (b) a self-maintaining XML-namespace shape rule — drop an off-sink
literal with no query, no fragment, a dated path segment (`/(19|20)\d\d/`), and not API-ish (matches 50/63
with no per-library upkeep); plus a scheme allow-list (`http/https/ws/wss`) to kill `file://`/mangled-ident
junk. This preserves the `.concat()` page-route differentiator (real absolute client routes are neither
denylisted nor namespace-shaped) and needs no read-model change. **Optional layer (d):** exclude harvested-`low`
route hosts from the Hosts `universe` roll-up (`hosts.py:190-197`) unless the host also appears via
asset/endpoint/tech — a read-time lever, no re-extract. **Invariant:** never drop a real in-scope host (both
(a) and (b) are strictly safer than today's over-matching substring); keep the confirmed-endpoint lane untouched.

#### D35 · Sources viewer can't display large bundles [M]  ·  performance
A multi-MB minified bundle is effectively unviewable in the Sources page: `CodeViewer` clamps to 512 K chars /
10 K lines and `SourcesPage` disables pretty-print above `BEAUTIFY_MAX_CHARS` = 200 K (js-beautify runs
synchronously on the main thread and froze the tab on a large bundle in past QA — the reason for the cap) and
highlighting above 200 K, so a 4.4 MB bundle renders as one truncated 512 K single line with only a "Download"
affordance. **Fix:** move beautify off the main thread (Web Worker) behind an explicit "expand / format"
affordance with an in-progress spinner, and virtualize the resulting ~100 K formatted lines (the CodeViewer
already virtualizes the file TREE but not the code body). Files:
`web/src/features/sources/{CodeViewer,SourcesPage}.tsx`. (User-reported 2026-08-23.)

#### D22 · Tech detection JS-runtime + header-allowlist gaps [M] — ⏳ PARTIAL 2026-08-22 (js + header allowlist shipped; html/dom remain)  ·  correctness
- ✅ **RESOLVED 2026-08-19 — the `js` (window-global) surface now fires (PR #85).** Bundled
  SPAs (Next.js `__NEXT_DATA__`, Nuxt `$nuxt`, React) were invisible; the matcher now
  presence-matches enthec's `js` global names in stored bundle source via one RE2 `Set`
  (`compile.compile_js_surface`; the naive per-pattern loop measured ~50s/host, the Set ~0.01s).
  Static source has no runtime value, so `version` is `None` and each tech's js contribution is
  capped (`match._JS_SURFACE_CEILING`) so a js-only detection reads "suspected", never "certain".
  A distinctiveness filter (`_keep_js_key`) drops the false-positive band (<4 chars, bare words
  <8). Both §4 gates passed. **html/dom stay unimplemented** (they need raw HTML / rendered DOM
  the allowlist signal omits); the **header-allowlist review is now resolved** (below).
- ✅ **RESOLVED 2026-08-22 — the curated header allowlist is widened (data-driven).**
  `fetch.py::_HEADER_ALLOWLIST` grew from ~14 to ~28 keys + a CORS `access-control-allow-*` prefix
  rule, chosen by measuring the enthec dataset's real header keys (665/7586 techs carry a header
  fingerprint; 324 distinct keys). The adds are CSP + CORS + vendor/CDN/CMS identifiers —
  architecture signal, not credentials — so ~158 more techs can fire on a header. Privacy held:
  `link` is excluded (its URLs carry signed-CDN query tokens, REQ-S2/S4), `www-authenticate` stores
  only its scheme token (no NTLM/Negotiate challenge blob), each kept value is size-capped (16 KiB in
  the shared `_allowlisted_headers`, covering both the fetch and capture producers), and
  `set-cookie`/`authorization`/`cookie`/`proxy-authorization` stay excluded (cookie NAMES only). Both
  §4 gates passed (design: BUILD-WITH-CHANGES — drop `link`, narrow `www-authenticate`, cap in the
  shared helper, add `platform`; code: see PR).

The fingerprint matcher (`findings/techdetect/match.py`) originally implemented only the Phase-1
signal surfaces — response headers, cookie names, `scriptSrc` URLs, `scripts` (JS source text), and
`<meta generator>` — and NOT enthec's `js` (window-global; now shipped above), `html`, or `dom`
surfaces. Consequence after the full-dataset re-pin (`techdetect_data/commit.txt` =
`1b9eee8…`, 7586 techs): ~25% of the dataset (≈1900 techs) has *zero* Phase-1-matchable
surface, and detection that relies on runtime globals/markup misses on bundled SPAs — e.g.
**Next.js** fires only via `x-powered-by: Next.js` (often disabled in prod) or a `NEXT_LOCALE`
cookie, and **React** (bundled into `_next/static/*` chunks, no standalone `react.js` URL)
does not fire at all. Separately, `fetch.py::_HEADER_ALLOWLIST` keeps only ~14 fingerprint
headers (a T1 privacy control), so header-keyed techs outside that set never fire. **Why
deferred:** the re-pin already resolves the reported "0 techs on modern sites" bug (vercel.com
0 → Next.js + Vercel); the `js`/`html` surface (Phase 2) and any allowlist widening are
separate, larger slices each needing their own privacy/perf review. **Still owed:** ~~(a) a
Phase-2 `js`-global surface so bundled SPA frameworks fire~~ (shipped, PR #85); ~~(b) a data-driven
review of the header allowlist against the full dataset's header keys~~ (shipped 2026-08-22, above).
Remaining: the `html`/`dom` surfaces (need raw HTML / rendered DOM). **Trigger:** if "site X
still shows no `<framework>`" becomes a recurring operator complaint. **Detection note:** the
`analyze.technologies` event carries per-host detection counts + `skipped_patterns`, so a
widening blind spot is observable, not silent.

#### D20 · Slice-Y multi-asset scale/robustness deferrals [M–L, ongoing]  ·  maintainability
Consciously-deferred SHOULDs from the multi-asset (Slice Y) build — safe now at bounded
single-host scale, revisit at M3/scale. Design spec:
`apps/platform/docs/superpowers/specs/2026-07-26-slice-y-multi-asset-design.md`.
- ✅ **RESOLVED 2026-08-22 — per-asset fetch retry (transient 5xx/429).** The crawl fetch loop
  now retries a transient 429/5xx per asset via `fetch._fetch_asset_with_retry` before dropping it
  to `failed` (→ run `PARTIAL`), bounded by `fetch_asset_retry_attempts` (default 2) with
  heartbeat-aware backoff capped at `fetch_asset_retry_max_delay_seconds` (default 5.0s). Only a
  `_TransientStatus` (429/5xx) retries — a deadline-exceeded `RetryableError`, `FatalError`, and
  `EgressBlocked` still fail fast. Every attempt heartbeats first, so the retry can't outrun the
  30s job lease (no peer reclaim / double-fetch); the retry is synchronous in the worker thread, so
  the DNS-pin single-thread invariant holds. `attempts=0` reproduces the pre-D20 behavior. Both §4
  gates passed (design: BUILD-WITH-CHANGES — the per-attempt-beat lease fix folded; code: SHIP-WITH-NITS
  — cap-clamp + heartbeat-cadence tests and a between-retries REQ-A4 control check folded). Residual
  (pre-existing, low-probability): `_beat_sleep` does not heartbeat a wait shorter than
  `crawl_heartbeat_interval_seconds`, so sustained host-slot contention narrows the lease margin —
  a heartbeat-family property shared with the at-scale bullets below, not introduced by this fix.
- **Analyze mid-scan heartbeat:** a long per-asset `kingfisher.scan` (≤ `engine_timeout_
  seconds`=120s) can exceed the 30s job lease with no mid-scan beat, so a peer can reclaim and
  re-run the analyze loop. Correctness-safe (idempotent REQ-A3 outbox upserts + analyze-
  terminal assets skipped), only wasteful; fix mirrors the crawl harness's in-subprocess beat.
- **Long-stage stream-reclaim strand:** `progress.beat` renews the DB job lease but never
  touches the Redis stream, so `reclaim_stalled` can hand a long stage's message to a peer; if
  the original then crashes the job can strand.
- **Commit-time DB error inside a per-asset analyze txn:** recorded as a permanent
  `analyze_failed` (→ `PARTIAL`) rather than job-level retry — structural tension with the
  per-asset-commit requirement (findings + status share one txn).
- **Dual asset-list source of truth:** the discovery manifest blob (URL list) and the
  `run_asset` rows (per-asset state) both list assets; unify only if drift is observed.
- **Queue fan-out / per-asset parallelism (model C):** fetch/analyze loop assets sequentially
  in one job (the fetch DNS-pin single-thread invariant); parallel per-asset jobs deferred.
- **Per-asset secret scanning of recovered source-map files.**
- **Multi-asset e2e is host-partial:** a real katana crawl→fetch→findings e2e can't be
  automated locally (`egress.validate_target` rejects the fixture's private Docker IP; we must
  not auto-crawl a public domain). `multi_asset_integration_test.py` Part A proves the pipeline
  stubbed (host-green); Part B is engine-gated in CI — run it against a gated staging env with
  real domain access.
(Migrated 2026-08-15 from the retired `slice2-deferred-debt.md`.)

**Tier-1 facet:** the per-asset fetch-retry bullet (a transient 5xx/429 during a crawl drops that asset -> run PARTIAL) was the one a user feels on a real target — ✅ RESOLVED 2026-08-22 (above); the remaining bullets are at-scale robustness (Tier-2), kept together as one register entry.

#### D16 · Capture extension deferred items [S] — ⏳ PARTIAL 2026-08-17 (CI test gate added)  ·  maintainability
Small deferred work in the MV3 capture extension (`apps/capture/chrome-extension/`), recorded here
when the point-in-time `REFACTOR-NOTES.md` was folded into the capture app README (`apps/capture/README.md`) during the
enterprise-hygiene cleanup (so the "later" doesn't become "never"):
- ✅ **RESOLVED 2026-08-17 — Live `tests/*.mjs` suites are now gated in CI.** Added a dedicated
  `extension` job to `.github/workflows/ci.yml` that runs all `tests/test_*.mjs` Node suites on every
  push/PR (previously `security.yml` only `npm audit`ed the package, so a broken suite couldn't fail
  the build). The job needs no `npm ci`/build — the suites import only `node:` builtins + local
  modules. Fail-closed: an empty glob is a hard error (no silent zero-test pass), and every suite runs
  so one failure can't mask another. Both §4 gates passed (design: SHIP AS-IS; code: see PR).
- ✅ **RESOLVED 2026-08-22 — the popup bundle is now compiled in CI.** The `extension` lane now runs
  `npm ci` + `npm run build` (with npm caching keyed to the extension lockfile), mirroring the
  `frontend` lane, so a broken popup import/JSX fails the build instead of merging green. The build
  only asserts the popup COMPILES; the emitted `dist/` is not diffed against the force-committed
  bundle (minified output isn't a stable equality target). This also pulls the `modules/*` files
  (imported only by the previously-never-compiled popup) into a compiled path for the first time.
  Verified: `npm ci && npm run build` → exit 0 (dist/popup.js 55kb + dist/popup.css); no
  `.npmrc`/ignore-scripts gotcha blocks esbuild's binary fetch in CI. (Originally surfaced by the
  D16 code-review gate 2026-08-17.)
- **`background.js` is well over 1,000 lines** (~3× the ~300 cap) — the message router +
  `processFile` could extract further. Same class as D11; test-aware (the service worker is the
  capture entry point), so a careful slice, low priority.
- **Sourcemap reconstruction runs synchronously at upload** for map-bearing files — the uploaded map
  content is ephemeral, so deferring it risks losing it. Turning off "capture source maps" is the
  current escape hatch for maximum bulk-capture speed.
- **Legacy removed-setting keys linger unread** in `chrome.storage.local` (`API Key`, `autoStart`,
  `useLocalApi`, `exportIncludeContent`, `allowSourceMapFallback`, `authContextDomains`) — no
  migration was written; harmless, cleanup only.
- **(Optional, not a gap)** a workspace-SPA "Analyze" button — the popup already triggers analysis,
  so this is a convenience feature, not missing behavior.
- **Counter can undercount the final ≤750ms burst before an MV3 teardown** (added by the
  counter-persistence fix, PR #55): a file captured in that window lands in the dedup store but not
  the debounced `capturedFilesMeta`, so on respawn it is dedup-suppressed and never re-enters the
  count. Rare, display-only, self-heals on the next capture — still a strict improvement over the
  reset-to-0 bug it fixed. Optional close: eager-persist the projection in `stopCapture`.

**Tier-1 facet:** the "Popup bundle is not compiled in CI" bullet was the user-facing risk (a broken capture popup could merge green) — ✅ RESOLVED 2026-08-22 (above); the remaining bullets are housekeeping. Kept together as one register entry.

### Tier 2 · fix before it scales up

Safe now for a single trusted operator. Each has a concrete future trigger - untrusted /
multi-tenant load, or the first upgrade against a database that holds live data.

#### D18 · OS/network-level egress isolation [L]  ·  supply-chain/security
REQ-P2 (metadata/RFC1918 blocked at the **network layer**) and REQ-T2 (net-emitting engines
in a scoped egress sandbox) are only partially met. Enforcement today is **application-level**
(`recon/fetch/egress.py`, ADR-0005): scheme + in-scope host + all-resolved-IPs-globally-
routable, DNS-pinned per request, redirects re-validated per hop, scope never derived from
crawled URLs. **Why safe now:** the app guard defeats the actual SSRF threat for the only
outbound traffic we make (the fetch stage); Kingfisher runs `--no-validate` (no network).
**Still owed (defense-in-depth):** OS-level isolation (network namespace + egress firewall /
seccomp / nsjail) against a compromised worker or a shelled-out engine that ignores our host
argument — the spec's "network layer" wording — plus the crawl-time subresource-SSRF gap
(headless Chrome loads subresources outside `egress.py`; app-level scope flags + per-URL
`egress.validate_target` on manifest entries only). **Hardening path:** deployment network
control (no route to metadata/RFC1918) → forced egress proxy enforcing `egress.py` →
netns/nftables. **Trigger:** before exposing the fetcher/crawler to untrusted multi-tenant
load. (Migrated 2026-08-15 from the retired `slice2-deferred-debt.md`.)

#### D19 · Migrations build tables with `create_all`, not frozen snapshots [M]  ·  correctness
`0001_initial`/`0002_findings` (and later new-table revisions) call
`Base.metadata.create_all(bind)` from the LIVE model metadata, not an explicit
column-by-column snapshot. On a from-scratch `alembic upgrade head`, 0001 already stands
up the entire *current* schema (including columns that later revisions "add"), so a plain
`op.add_column` in a later revision hits `DuplicateColumn` on a fresh DB — this bit CI when
`0003` added `run.source_map_ref`. **Mitigation in place:** incremental column-adds use
`ADD COLUMN IF NOT EXISTS` (see `0003`, `0005`). **Still owed:** freeze `0001` to an
explicit `op.create_table` snapshot and stop calling `create_all` inside migrations, so each
revision is an immutable historical step and plain `add_column` is safe. Do this before real
incremental upgrades against live tenant data (M3); deferred because the build is pre-prod
(no data to preserve) and the rewrite must exactly mirror the models (columns/FKs/indexes/
RLS). **Detection note:** CI catches a broken migration because api/worker `depends_on
migrate: service_completed_successfully`; `docker compose up -d migrate` alone swallows the
exit code. (Migrated 2026-08-15 from the retired `slice2-deferred-debt.md`.)

#### D23 · Per-asset cumulative beautify has no budget/heartbeat [S]  ·  correctness
`findings/analyze._analysis_units` beautifies each source-map-recovered *minified* file before
extraction (`deobfuscate.beautify_if_minified`, per-file 1 MiB cap) so its findings land on
distinct lines that match what `probe/sources` later serves — the byte-identical invariant that
fixed jump-to-finding for recovered minified vendor sources (PR #76, 2026-08-17). **Residual:**
the per-file cap bounds each file, but a single source map with many large minified
`sourcesContent` entries can total up to ~`engine_max_output_bytes` of beautify work per asset
with **no heartbeat between files** — this loop and the tree-sitter `extract()` that follows share
the one per-asset `progress.beat`, so a pathological map could approach the 30 s job-lease stall
window and let a peer reclaim the RUNNING job. **Why safe now:** the outcome is idempotent-safe
double-work (the reclaiming peer re-runs the analyze loop; REQ-A3 outbox upserts + analyze-
terminal-asset skipping make it wasteful, not corrupting), not data loss, and real source maps are
nowhere near this shape. **Still owed:** a per-asset cumulative-beautify budget (serve raw past it)
or a heartbeat between files — same heartbeat family as D20 ("Analyze mid-scan heartbeat") and D21
(extractor linear-but-unbounded). **Trigger:** before exposing analyze to untrusted multi-tenant
load at scale. Anchor: the `# NOTE(DEBT)` in `findings/analyze.py::_analysis_units`.

#### D17 · Capture Origin-lock allows a `null` Origin [S]  ·  supply-chain/security
The capture-ingest Origin-lock (`capture_router.py` `_enforce_origin_lock`) rejects a
web-page `http(s)` Origin but ALLOWS an opaque `Origin: null` (a sandboxed iframe /
`data:` document), because the MV3 worker may itself emit `null` and we won't risk
dropping real capture. The blast radius is bounded to the SHARED `capture-spike` tenant:
central login re-homes a logged-in operator's real captures into their OWN tenant (the
auth session token in `_resolve_ingest_tenant`), so a `null`-Origin write can only land
fake findings / storage-worker DoS in the throwaway shared tenant, never an operator's.
Optionally also reject `Origin: null` once the extension worker's real Origin is
confirmed live. The decision is pinned by `capture_origin_lock_test.py` so it can't be
flipped silently.

### Tier 3 · behind the scenes

Developer-facing hygiene - it keeps contributors fast and the trunk healthy, but a user
never sees it directly.

#### D28 · Cross-chunk export index double-recovers source maps [S]  ·  performance
The cross-module endpoint resolver (`findings/analyze.py::build_export_index`) runs a
run-level pre-pass that recovers each mapped asset's source map to harvest its exported
string consts, then the existing per-asset extract loop recovers the SAME maps again for
full extraction — so a mapped crawl pays **2× sourcemapper subprocess spawns per asset**
(and a no-map asset is likewise tree-sitter-parsed twice: once to harvest exports, once
in the loop to extract).
Chosen deliberately: it keeps the pre-pass memory-bounded (only the small export index
persists, not recovered source) and guarantees the index keys match the loop's recovered
`f.path` by construction (they come from the identical `recover_sources` call), which is
what makes cross-chunk resolution correct at all. The extra spawns are idempotent-safe
bounded work, not corruption, and the pre-pass heartbeats per asset so it can't lose the
lease. Follow-up: cache the recovered units for reuse across the two passes, or fold the
export harvest into the main loop with deferred (post-loop) resolution. Extends the
recovery/stall note in `_analysis_units`. `# NOTE(DEBT)` marks the site.

#### D29 · Deferred SES/Node exec engine for webpack chunk-URL enumeration [L]  ·  supply-chain/security
The static cross-chunk resolver (Increments 1/2a/2b, main @ `93d2fd8`) resolves fetch/axios
URLs split across chunks, but does NOT yet **enumerate lazy-chunk URLs** by executing the
bundle's own `__webpack_require__.u` builder — the recall edge `js-recon` gets via `ses`/`lockdown`.
The user approved that execution as a **posture change** (static-only → local sandboxed execution
of target code), but we **sequenced it behind** a pure-Python static-template-emulation slice
(covers the standard `"static/chunks/"+id+"."+map[id]+".js"` form with zero new attack surface,
no posture change). The exec engine (executing *arbitrary/obfuscated* builders in a Node sandbox)
is deferred to its own hardening slice. Design spec:
`apps/platform/docs/superpowers/p4-ses-chunk-enumeration-design.md`.
**§4 adversarial security review (2026-08-20) = PROCEED-WITH-CHANGES.** These six are a hard
security contract that gates ANY exec-path code (SES `lockdown` is a JavaScript boundary only —
if V8/SES is escaped the process has ambient OS authority):
1. **Network namespace, no interfaces** — the real "no traffic" guarantee. `engines.py:15-20`
   documents that the `subprocess.run` timeout kills only the DIRECT child, not grandchildren,
   so escaped code could fork a detached grandchild that outlives the kill and does network I/O.
2. **Kill the whole process tree** — `start_new_session=True` + `os.killpg`, or let nsjail reap.
3. **Explicit minimal env** — `run_engine`'s `env` defaults to `None` → `subprocess.run` inherits
   the worker's secret-bearing environment (auth signing key, S3, DB creds). Never inherit; test it.
4. **Memory + pids caps** — `node --max-old-space-size` + `ulimit -v`/cgroup + `pids_limit`. SES
   by its own docs "does not protect availability"; `run_engine`'s output cap is post-hoc
   (`engines.py:111-117` bounds what we *process*, not peak RAM).
5. **Cap enumerated URL count + length** before seeding — the builder output is attacker-controlled.
6. **Pin the whole `@endo` tree with integrity**; treat SES as defense-in-depth, never the sole boundary.
Also requires an ADR-0006 posture amendment (local sandboxed execution of extracted target code, no-network
sandbox) + an ADR-0005 note, and its own §4 gates. **Already safe (no work owed):** enumerated
(content-derived) URLs cannot widen egress — every fetch hop re-runs `egress.validate_target`
(out-of-scope host / `data:`+`file:` scheme / userinfo all rejected; `scope_hosts` never populated
from content), so the static slice and any future engine both inherit that guard by routing URLs
exclusively through the seed→fetch path.

#### D30 · Deferred interprocedural param-URL resolution (static recall ceiling) [L]  ·  correctness
The static extractor resolves a sink URL held in / built from a single-unshadowed local binding
(Phase 2 for fetch/axios; extended by S1 to `XMLHttpRequest.open`, jQuery, and `new WebSocket` so
the same `const u = "…"; sink(u)` folds at every sink). What stays `unattributed` is a URL that
arrives as a **function parameter** (`fetch(o)`, `c.open("GET", a)`) or from a **builder-method
call** (`fetch(t.build("fetch", r))`) — the shapes that dominate the real unresolved sinks on
minified webpack (18/18 on the Asana corpus). This is REQ-C2-honest (unattributed, never guessed),
not a bug — it is the deliberate ceiling of the static path.
Resolving a parameter would need **cross-function (interprocedural) data-flow** — the taint
analysis the thorough-endpoint-recovery §4 design review explicitly ruled out (F5: it is boolean
source→sink *reachability* that does not reconstruct the URL string, and a per-sink pass over a
function's call sites reintroduces the O(n²)/FP class DEBT D21 just closed). On minified-no-map
bundles the names are mangled → poisoned → recall there is ~0 regardless of any analysis; the lever
only ever pays on readable / source-map-recovered source.
**Measure-first gate (do this BEFORE any build):** run the already-shipped static path across more
real bundles and quantify what fraction of genuinely-unresolved sinks are (a) a *single unshadowed
call site* whose argument is statically foldable (safely resolvable, 0-FP) versus (b) genuinely
interprocedural / builder-method (not). Build only if (a) is a material population. If ever built:
a bounded intraprocedural + single-call-site fold ONLY (never a full taint port), 0-FP re-proved on
the real corpus, index-once / no per-sink re-traversal (D21 discipline). Related: the deferred exec
engine (D29) and `apps/platform/docs/superpowers/thorough-endpoint-recovery-design.md`.

#### D9 · Test-pyramid inversion [L, ongoing]  ·  maintainability
58 of 123 backend test files carry an integration marker (need live PG/Redis/MinIO); the fast
hermetic layer is now ~half (≈65 files) — grown by the D9 slices below, no longer the clear
minority — but the heavy lane still catches most real bugs. Grow the small-test layer.

**Slice 1 (2026-08-09):** added hermetic tests for the decision kernels that were
previously only exercised under live infra — `worker/main.py::process_message` (the
full run-lifecycle routing matrix: gone/skipped/paused/cancel/pause/duplicate/
mid-loop-checkpoint/ControlInterrupt/happy-path) + `_handle_failure` DLQ branch (30%→79%),
`probe/reveal.py::_derive` + `_reveal_candidates` (the fail-closed `integrity` drift
check + deterministic ordering; 41%→59%, faking only `storage.get_blob`), and
`storage.py::object_key` (tenant-isolation key shape + content-addressing, no test
existed). Fast-lane total 59%→61%; D5 floor ratcheted 58→60. **Slice 2 (2026-08-09):**
extracted a pure `_etag()` from `runs/queries.get_status` (byte-identical refactor) +
hermetic tests pinning the REQ-R4 invariant (a pause/cancel *request* changes the ETag
without moving `state`, so `If-None-Match` polling can't miss it), and
`probe/sources._as_content` (the byte-slice-before-decode bounded-response invariant).
Fast lane 61.09%→61.32% (floor stays 60 — too little headroom to ratchet). Out of scope
(intrinsically integration —
uncovered lines are DB/queue semantics where a hermetic test buys mock-fiction):
`spec/service.py`, `spec/base_url_service.py`, `findings/wrapper_service.py`,
`findings/reextract.py`, `runs/service.py`, `runs/coordinator.py`, `probe/triage.py`.

#### D11 · Files over the ~300-line cap [M] — ⏳ PARTIAL 2026-08-07 (extract.py split)  ·  maintainability
`findings/extract.py` (639, 2.1x) was split into a 3-module import DAG — `_jsast.py`
(leaf: tree-sitter parser/AST helpers + value dataclasses + param builders) ← `_base_env.py`
(REQ-C2 base-URL resolution) ← `extract.py` (network-sink handlers + `extract()`).
Pure move (per-symbol AST diff proved byte-identical; §4 code gate SHIP-WITH-NITS), with an
`__all__` re-export shim so `analyze.py`/`classify.py`/tests keep importing `RawEndpoint`,
`HTTP_METHODS`, `collect_base_env`, `_PARSER` from `recon.findings.extract` unchanged (matters
under D3's now-strict `no_implicit_reexport`). All three modules are mypy-strict-clean.
Since the split, cross-chunk resolution + dataflow work has regrown all three back over the cap
(2026-08-23: `_jsast.py` 679, `_base_env.py` 394, `extract.py` 610) — a re-split is a future slice.

**Deferred (evidence-backed, both §4 design engineers):**
- `db/models.py` (645) — DON'T split: a cohesive declarative schema (17 classes + 35
  FK/`back_populates` cross-refs + the RLS `*_TABLES` tuples read by Alembic `env.py`). With
  `from __future__ import annotations`, `relationship()` targets resolve only via the class
  registry, so a package split makes `__init__` load-bearing for ORM registration (a bypassing
  `from recon.db.models.run import Run` silently breaks `configure_mappers()`). High fragility,
  zero behavior gain.
- `api/capture_router.py` (793) — DEFER: the D1/D8a tests *rendezvous-monkeypatch*
  `capture_router.<helper>` (e.g. `monkeypatch.setattr(capture_router, "_run_has_job", …)`);
  moving a handler/helper to a sibling module silently breaks the patch (a test would pass while
  testing nothing). Also can't reach the cap (~420 residual). Needs a careful test-aware slice.
- `findings/analyze.py` (1126, the largest) — DEFER: a clean record-trio seam exists but it
  touches the outbox/RLS/REQ-A3–A4 invariants and `reextract.py` imports `_extract_endpoints`;
  higher-risk, own slice. `findings/queries.py` (623) + `fetch/fetch.py` (879, ~2.9x — grown by
  D20/D22/source-map work): low priority (fetch is SSRF-fail-closed-critical — don't fragment).
(Line counts re-measured 2026-08-23.)

#### D5 · Coverage ratchet [ongoing]  ·  enforcement/tooling
Floor is `--cov-fail-under=60` (fast-lane coverage is ~61%, grown by the D9 slice-1
hermetic tests). **Ratcheted 55→58, then 58→60 on 2026-08-09** to lock in the gains.
Ratchet the floor up as coverage grows; never lower it. (`.github/workflows/ci.yml` is
the single source of the number; the CLAUDE.md mention trails it.)

## Resolved (record)

Kept for the decision trail - what was deferred, why, and how it was closed. In ID order.

### D1 · Capture get-or-create race — silent duplicate sessions/runs [M] — ✅ RESOLVED 2026-08-07  ·  correctness
Fixed via approach A.
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

### D2 · Ruff format sweep + broaden the ruleset [M] — ✅ RESOLVED 2026-08-07  ·  enforcement/tooling
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

### D3 · mypy — no Python type checking [M–L] — ✅ RESOLVED 2026-08-07  ·  enforcement/tooling
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
(194) is the natural follow-up.

### D4 · TypeScript strict off [S] — ✅ RESOLVED 2026-08-07  ·  enforcement/tooling
Enabled `"strict": true` in both `apps/platform/web/tsconfig.app.json` and
`tsconfig.node.json`. The "~5 feature pages to burn down" estimate was pessimistic —
the code was already written null-safe, so a forced clean typecheck
(`tsc -b --noEmit --force`) is **0 errors**; lint + build + 133 vitest tests stay
green, and CI's `frontend` lane (`tsc -b --noEmit`) now enforces it. Both §4 gates
passed (design: SHIP AS-IS; code: APPROVE). Deferred (a separate future slice, NOT
this one): the beyond-umbrella flags `noUncheckedIndexedAccess` /
`exactOptionalPropertyTypes` / `noImplicitReturns` add ~20 errors on current code, so
enabling them means a real burn-down.

### D6 · No dependency/secret scanning [S–M] — ✅ RESOLVED 2026-08-07  ·  supply-chain/security
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

### D7 · Image build isn't lock-pinned [M] — ✅ RESOLVED 2026-08-07  ·  supply-chain/security
`apps/platform/Dockerfile` now installs Python deps from the committed lock instead
of `pyproject` `>=` floors: a `deps-export` stage runs `uv export --frozen --no-dev`
to a hash-pinned `requirements.txt`, and the runtime stage `pip install -r`s that,
then `pip install --no-deps .` for the project (kept NON-editable — a real wheel
build — so its packaged `findings/rules/*.yml` data stays validated in the image, the
gap the integration-lane AKIA test catches). The image now matches CI's
`uv sync --frozen`; verified by a full image build. Web was already `npm ci`-pinned.
Both §4 gates passed.

### D8 · Unversioned contracts [M] — D8a ✅ RESOLVED 2026-08-07; D8b ✅ RESOLVED 2026-08-09  ·  maintainability
Two wire contracts carried no version field and no consumer-contract test.

**D8a (capture ingest — DONE):** `capture_router.py` now stamps a server-authored
`CAPTURE_CONTRACT_VERSION` on the `GET /api/health` handshake (response-side only —
additive, so deployed extensions that ignore the health body aren't broken), and a
hermetic fast-lane `capture_contract_test.py` pins the wire shapes the extension
depends on (health / save-files / analyze-start / progress envelopes + the
`GET /api/projects` bare-array invariant), so drift fails in the fast lane instead of
silently in prod. Both §4 gates passed (design: BUILD WITH CHANGES; code: APPROVE
WITH NITS).

**D8b (OpenAPI export — ✅ RESOLVED 2026-08-09):** `probe/openapi.py` `build_openapi` now
stamps a root `x-recon-export: {contract-version, generator}` on every emitted document —
an explicit version for the export FORMAT (the machine-readable shape of the `x-recon-*`
extensions), distinct from `info.version` (`"0.0.0"` = the reconstructed target's unknown
version). Kept IN the document (not an HTTP header) so the version travels with a saved
`.json`/`.yaml` artifact, so `export_router.py` needs no change. A hermetic drift test
(`openapi_test.py`) pins the version literal, the in-document stamp shape, and the full set
of `x-recon-*` extension names + the `x-recon-confidence` key/value vocabulary, so a silent
shape change fails the fast lane instead of breaking a consumer (Burp / the threat-model
feed). Both §4 gates passed (design: BUILD AS-IS; code: SHIP-WITH-NITS — the 3 nits [kebab
`contract-version` key, tightened comment scope, this ledger flip] all folded). A third
response contract — the spec classify/diff envelope (`api/spec_router.py` `asdict(summary)`)
— is also unversioned but is out of D8's stated two-contract scope; noted here as a future
follow-up if it grows an external consumer. Still precedes any D13 (enrichment) resume,
which extends this output.

### D10 · No ADR trail [M] — ✅ RESOLVED 2026-08-08  ·  maintainability
Added a MADR ADR trail at repo-root `docs/adr/` (beside `ARCHITECTURE.md` — the "what";
the ADRs are the "why"): a trimmed template (`0000-adr-template.md`), a `README.md` index,
and **8 backfilled records** — Redis Streams broker (at-least-once folded in), Postgres
RLS, cooperative orchestrator-level pause (not OS signal-stop), content-addressed blobs,
fail-closed SSRF egress guard, static/no-active-traffic stance, single-analysis-core v1
convergence, and the hardened out-of-process engine harness. Each carries a MADR
**Confirmation** section pointing at the enforcing code/tests (anti-drift anchor) and links
its slice spec rather than copying it; status is per-decision-reality (all 8 shipped →
`accepted`). **GraphQL export-only is a corollary of ADR-0006, not a standalone accepted ADR** —
it now ships (enrichment C, D13) as the export-side `x-recon-graphql-operations` annotation,
consistent with ADR-0006 (outputs are static exports; a reconstructed GraphQL operation is an
*export* annotation, never a served API nor an HTTP path/finding). Three decisions whose rejected-alternative rationale is
off-repo (the SIGSTOP rejection, Redis-vs-alternatives, the exact scope of "no active
traffic") say so explicitly and cite the source. A hermetic structure test
(`apps/platform/src/recon/adr_structure_test.py` — must live under `src/` to be collected
by the gated lane) enforces filename shape, unique numbering, valid frontmatter, and
bidirectional README↔file index integrity, resolving repo-root `docs/adr/` via a `.git`
walk (no fixed depth, no `**` glob). Both §4 gates passed (design: both engineers
BUILD-WITH-CHANGES, all must-fixes folded; code review:
CHANGES-REQUIRED → 1 must-fix [ADR-0007 overstated `apps/capture/` contents] + 5 citation
nits, all fixed → SHIP).

### D12 · Stale branches [S] — ✅ RESOLVED 2026-08-09  ·  maintainability
Pruned 9 stale remote-tracking refs (`git fetch --prune`) + deleted 9 fully-merged
local branches (via `git branch -d`, which refuses anything not actually merged) +
deleted the merged `origin/spike/platform-ingest` (confirmed an ancestor of `main`).
Preserved on purpose: `feat/enrichment` (D13, parked) and the two LIVE worktree
branches `claude/vigilant-hertz-*` + `claude/wonderful-leavitt-*` (other active
sessions under `apps/platform/.claude/worktrees/`). **One remnant:** removing the
`claude/busy-boyd-e00cc4` worktree+branch (clean, detached, AKIA fix superseded on
main) needs `git worktree remove` + `branch -D`, which the auto-mode command
classifier blocked as destructive — remove it manually or add a Bash permission
rule; nothing depends on it.

### D13 · Enrichment slice [M] — ✅ RESOLVED 2026-08-13  ·  parked -> shipped
Param risk-tags (A) + auth headers → OpenAPI `securitySchemes` (B) merged in PR #49; GraphQL
export-only (C) reviewed and landing on `feat/enrichment-graphql`. Both §4 gates passed (design:
BUILD WITH CHANGES; code: SHIP-WITH-NITS — the MEDIUM `RecursionError` soft-miss + the M4
multi-event union test folded). The OpenAPI export now carries `x-recon-risk`,
`components.securitySchemes`, and `x-recon-graphql-operations`. Spec:
`apps/platform/docs/superpowers/specs/2026-08-07-enrichment-design.md`.

### D14 · Concurrent `analyze/start` can double-enqueue a walk [S] — ✅ RESOLVED 2026-08-07  ·  correctness
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

### D15 · Tenant-UUID entry friction [S] — ✅ RESOLVED 2026-08-07  ·  maintainability
The SPA's `TenantGate` forced a first-time operator to paste a tenant UUID they don't
know. Persisted last-tenant was already handled (localStorage). Added an opt-in
build-time `VITE_DEFAULT_TENANT_ID` (`web/.env.example`): when nothing is persisted,
`TenantContext` falls back to it as a cold-start default — silent pass-through, never
persisted (so it tracks the build), an explicit last-used tenant always wins, and the
gate still validates it via `isValidTenant` (an invalid default fails safe to the form).
A tenant *picker* stays DEFERRED on purpose — a `GET /tenants` enumeration would leak
other tenants' identities under the RLS model; a per-caller `GET /me`-style identity
endpoint is the strategic replacement (out of scope here). Caveat documented in
`.env.example`: Vite inlines the var into the bundle, so it's for single-tenant/dev
builds only, not a shared multi-tenant prod bundle. Both §4 gates passed (design: BUILD
WITH CHANGES — the scope caveat + this ledger entry, both addressed; code review: SHIP
WITH NITS — the precedence test was strengthened to assert the *effective* tenant, not
just unclobbered storage). Frontend lane green (oxlint + tsc-strict + vitest 136 + build). NOTE: the `TenantGate`
UI this entry describes was later removed — superseded by central login (PR #57);
`TenantContext` + `VITE_DEFAULT_TENANT_ID` remain.

### D21 · Extractor DoS: harvest O(n²) + one-shot work budget [S] — ✅ RESOLVED 2026-08-21  ·  correctness
PR #71 was thought to have closed the crafted-input DoS class (per-decode span caps + iterative
recursions kill the deep-single-expression O(n²)/crash). Landing the "still-owed" work budget
surfaced a SECOND, live O(n²) the deep-chain guard could not catch: `findings/extract.py`'s
off-sink harvest pass tested each builder node for containment in the recorded-sink `claimed`
list with an `any(...)` scan — O(claimed) per node, so O(n²) once a bundle has many sinks (~5 s
at 8k flat sinks, ~34 s at 20k — a <1 MB crafted input, well under the 10 MB ingest cap). The
existing harvest guard only exercised a deep SINGLE chain, where the result (and thus `claimed`)
stays empty, so it never saw this dimension. **Fixed algorithmically:** `_merge_spans` collapses
the claimed spans once, and `_harvest_routes` probes them with a single forward pointer over the
preorder walk (whose node start-bytes are non-decreasing), plus a scalar `last_harvest_end` for
nested-sub-expression dedup — O(n log n), full recall preserved (byte-identical findings on real
inputs; the 417-test findings lane stays green). **Plus the requested one-shot budget:**
`_MAX_AST_NODES` (2 M nodes ≈ 6–8 MB of source — above any real single file) checked once via
`Node.descendant_count` (O(1)); over it, `extract()` caps its two EXPENSIVE passes (the sink walk
+ harvest) to a prefix via `_walk(limit=…)` — on a measured 10 MB nested-concat input this cut
end-to-end from ~40 s to ~25 s. The budget does NOT bound `collect_base_env`'s poison/const
pre-pass (the dominant residual, ~17 s of full-tree walks on that input): it must see the WHOLE
tree or a name shadowed past the prefix could resolve wrongly (a false positive), so soundness
beats the time. Net: worst-case wall-clock is ~linear and can APPROACH — not sit well under — the
30 s heartbeat on a crafted oversize input, acceptable under the 10 MB ingest cap; the curtailment
only ever drops tail recall, never invents a URL. (§4 code-review gate = SHIP-WITH-NITS; this
softened wording + the `_walk(limit<=0)` guard are the folded nits.) Guards (both in
`findings/extract_test.py`): `test_harvest_stays_linear_on_many_flat_sinks_no_dos` (the
orthogonal many-claimed dimension) + `test_node_budget_curtails_pathological_tree_dos_guard`.

### D24 · Runtime-capture / unconfirmed-lane findings lose host attribution [M] — ✅ RESOLVED 2026-08-19  ·  correctness
Host is now lifted from a finding's absolute-URL literal on the unconfirmed lanes
(`egress.attributed_host` + `analyze`), and the `source_path` mislabel was fixed (52e5669),
so the Findings host facet populates for the hosts capture recovered. Shipped in PR #81
(with the D26-broadening per-host "Suspected" column, PR #82). Both §4 gates passed.

### D25 · Sources view freezes the app on large sessions [L] — ✅ RESOLVED 2026-08-19  ·  performance
Fixed via file-tree virtualization + a one-pass bottom-up finding-count precompute + an
occurrence→file index + decoupling the tree from mid-stream SSE re-renders (plus a FE
long-task/error boundary and BE timing logs). A 500-file tree now renders ~25–40 DOM nodes
instead of thousands. Shipped in PR #78. Both §4 gates passed; live-verified.

### D26 · No host/domain inventory [M] — ✅ RESOLVED 2026-08-19  ·  maintainability
Added a discovered-hosts endpoint (`GET /runs/{id}/hosts`), an Overview metric card, and a
filterable Hosts page (by scope + name) with per-host roll-up counts. Shipped in PR #80
(with the per-host "Suspected" backend column, PR #82). Key fix: http(s)-scheme-only assets
so a capture run's `vm://<hash>` pseudo-hosts don't flood the list. Both §4 gates passed.

### D27 · Session card shows the raw target as host + no failure-reason affordance [S] — ✅ RESOLVED 2026-08-19  ·  maintainability
Card host is now derived from the crawl target's host (`_target_host` → `egress.host_of` of
the first whitespace token, read-time only — fixes existing rows and keeps `run.target` raw
for the re-run prefill; a user rename still shows verbatim). The classified `failure_reason`
is surfaced accessibly on a failed card: on the card-body `aria-label` (screen readers) plus
an `aria-hidden` hover/focus tip that is a SIBLING of the `role="button"` body (not nested,
which would strip its ARIA). Both §4 gates passed (design: BUILD WITH CHANGES — crash-guard
+ tooltip-out-of-button; code: SHIP WITH NITS — folded). Shipped in this PR.
