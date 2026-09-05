# D45 — capture coverage: inline/eval, XHR/JSON/GraphQL bodies, source-map header/`.map` probe

Status: DRAFT (design review pending) · 2026-09-05 · branch `feat/d45-capture-coverage`
Owner: capture-reliability workstream · closes DEBT **D45** (Tier 1, correctness)

## 1. Goal + the one hard constraint

Close **all of D45** (a + b + c). "Done" = the register item is fully resolved, not a
subset. The operator's explicit constraint, load-bearing on every sub-part:

> **Capture more real surface WITHOUT adding noise.** No irrelevant / third-party /
> library / analytics junk may enter the capture set. Every new capture path must be
> at least as strict as today's script path.

The three gaps (evidence at time of review):
- **(a)** inline `<script>` bodies + `eval`/`Function` code are never captured
  (`background.js` `webRequest` `types:["script"]` only; `content-script.js` enumerates
  `script[src]` + resource-timing only, never reads inline `.textContent`). And
  `docs/OPERATING.md:57` **falsely claims** inline/eval coverage.
- **(b)** XHR/fetch/JSON/GraphQL request+response bodies are ignored — the tool
  reconstructs the API *statically* and never observes the app's *real* calls, so
  everything stays `suspected`, never `confirmed`.
- **(c)** source-map recovery is inline-comment-only (`sourcemap-detector.js` reads
  only `//# sourceMappingURL=`), never the `SourceMap:`/`X-SourceMap` response header
  (already captured into `metadata.headers`, just unused) nor the conventional
  `<file>.js.map` probe.

## 2. Current architecture (what we build on)

Capture is a **URL re-fetch** model, not a body-interception model:

```
webRequest(onCompleted, types:["script"])  ──►  handleRequest(details)
   isInScope(url)? ──no──► noteOutOfScopeScript (D44 discovery hint), drop
   shouldSkipUrl(url, documentUrl)? ──yes──► drop
   └─► processFile: ContentFetcher.fetch(url)  (re-GET the URL, credentials:include)
          detect source map (inline comment) → fetch+parse
          extract deps · classify · countSecrets · hash · dedup
          enqueue(fileObject) → durable outbox → upload → platform
```

Existing **noise / relevance filters** (this is the machinery the constraint rides on):
- `isInScope(url)` — host must match configured `domainScopes`; **fail-closed** (no
  scope ⇒ capture nothing unless `captureEverything`). `background.js:722`.
- `shouldSkipUrl(url, documentUrl)` — `isWorkspaceUrl` (never recon ourselves) +
  `matchesDenylist(url, denyRules, denyDefaultProfile)` (glob denylist;
  `DEFAULT_PROFILE_PATTERNS` = WP/analytics/ad/CDN/jquery) + `outOfScopeMode==='exclude'
  && isThirdParty`. `background.js:264`.
- `classifyAsset(url)` → `app|lib|cms|tracker`; `isThirdParty(asset, page)` (registrable
  domain compare). `modules/asset-classifier.js`.

**Design rule for D45:** every new artifact (inline script, observed endpoint, body)
passes through the SAME `isInScope` + `shouldSkipUrl` gate, keyed on the right URL (the
document URL for an inline script; the request URL for an XHR). Plus a per-kind
relevance gate (below). Nothing new bypasses the denylist.

MV3 facts that bound the mechanism choices:
- `webRequest` here is **observational** — in MV3 it exposes request/response
  *metadata + headers*, never response *bodies*.
- Reading response **bodies** or hooking `eval` requires **MAIN-world** page execution.
  A statically-declared `content_scripts` entry with `"world":"MAIN"` (Chrome 111+)
  does this with **no new permission** and no `debugger` infobar.
- `chrome.debugger` (CDP `Network.getResponseBody`) is **rejected**: it needs the
  `debugger` permission, paints a persistent "extension is debugging this browser"
  banner over an operator's authenticated session, and conflicts with DevTools. Wrong
  tool for a browse-along recon client.

## 3. Design per sub-part

### 3c — source-map header + `.map` probe  (smallest, ship first)

Pure completeness, no new data kind, no posture change. In `processFile`, when the
inline comment yields no map (`detectedSourceMapUrl === null`):
1. consult the response header already in `metadata.headers`: `sourcemap` then
   `x-sourcemap` (both lowercased by `extractMetadata`); resolve relative to the JS URL
   via the detector's existing `resolveSourceMapUrl`.
2. else probe the convention: `HEAD`/`GET` `url + '.map'` (strip `?`/`#` first), accept
   only a `200` that parses as JSON with a `version`/`sources` shape (avoid capturing a
   SPA `index.html` 200 fallback).

`SourceMapDetector` grows a `detectFromHeaders(headers, fileUrl)` and a
`conventionalMapUrl(fileUrl)`; `detect()` stays the comment path. New `sourceMapFetchStatus`
values: `header` / `probed` (vs today's `fetched`/`not_detected`/…) so the popup + the
platform can tell how a map was found. The `.map` probe is **gated** behind
`captureSourceMaps` (already the master switch) and skipped for out-of-scope/denied URLs
(it reuses the same fetch path, which the operator already trusts for maps).

Noise: none added — a map only ever attaches to an already-in-scope, already-captured JS
file.

### 3a — inline `<script>` capture (+ the `eval` decision)

**Inline `<script>` (ON by default):** `content-script.js` already runs at
`document_start`, `all_frames`, with a MutationObserver — timing-robust (a DOM read, no
early-request race). Extend it to read **inline** nodes (`<script>` with no `src`, type
empty/`text/javascript`/`module`) — `.textContent` — on initial scan + mutation. Emit
`inlineScriptDetected` `{ pageUrl, content, ordinal }`; background records a **synthetic
file**:
- **synthetic URL keyed on POSITION, not hash** (review Claim 4): `${pageUrl}#inline-<ordinal>`
  where `ordinal` is the stable DOM slot. A re-rendered slot then supersedes its prior
  version via the existing changed-content branch (`background.js:465-472`) instead of
  minting a new entry — else SPA/Next.js streaming `<script>self.__next_f.push(…)>` churn
  accumulates unbounded (a 50-route app → 50+ synthetic files). Content-hash dedup still
  collapses identical bodies.
- **per-page inline cap** (review Claim 4): bound distinct inline captures per page/origin
  so a pathological page can't flood the outbox.
- **scope:** filter on the **page URL** through `isInScope` + `shouldSkipUrl`.
- **content-based relevance filter** (review Claim 3 — REQUIRED, this is the no-noise
  linchpin): the URL gate CANNOT filter an inline analytics bootstrap — an in-scope-page
  GTM/GA/Segment snippet passes `isInScope` + `shouldSkipUrl` because its synthetic host
  IS the in-scope page. So a new **pure, content-keyed** predicate
  `modules/inline-relevance.js` drops: below a byte floor; serialized-data pushes
  (`__next_f`/`__NUXT__`/hydration payloads — data, not source); and known analytics
  bootstraps (`dataLayer`/`gtag(`/`GTM-`/`analytics.load`/segment/hotjar signatures).
  Kept: inline blocks with real callable app JS. Colocated unit test like `asset-classifier`.

**`eval` / `Function` — DEFERRED (review Claim 6 + operator decision 2026-09-05).** The
review showed an `eval`/`Function` MAIN-world hook is low-value and unreliable: it races
page scripts at `document_start` (a page that stashes `const E=eval` before us wins),
can't catch `new Function`/`setTimeout("…")`/dynamic `import()`/Wasm, only fires where CSP
already permits `eval`, and is inherently noisy. Per repo YAGNI we do NOT build it
speculatively. **D45a's real gap is the false doc claim** — so we (1) ship inline capture
(above) and (2) correct `docs/OPERATING.md:57` to state inline IS captured and
`eval`-generated code is NOT. Recorded as a deliberate, operator-approved deferral in
DEBT.md (never a silent under-delivery). If a concrete need arises later, the
opt-in/off/per-page-capped envelope is the right shape.

### 3b — observed API calls: confirm endpoints + request bodies (b1, ON) · response bodies (b2, opt-in)

Restructured per review Claim 2: **request bodies (incl. GraphQL) do NOT need the main
world** — `webRequest.onBeforeRequest` exposes `requestBody` to a non-blocking MV3
listener. So the on-by-default tier gets endpoint confirmation AND GraphQL/JSON request
shapes with no main-world hook, no `document_start` race, no page-forgeability. Only
*response* bodies (the PII surface) need the main world — and they are opt-in.

**b1 — endpoint confirmation + request bodies (ON by default; no main world):**
- a second `webRequest` observer on `types:["xmlhttprequest"]` (+ `"websocket"`), reading
  `onBeforeRequest` with `["requestBody"]` and `onCompleted` for `{status, content-type}`.
- record `{ method, url, status, requestType, content-type }` and, for API-ish requests,
  the **request body** (`formData` or `raw` bytes → GraphQL query/variables, JSON payload).
- **noise:** `isInScope(url)` + `shouldSkipUrl(url, documentUrl)` (kills tracker beacons) +
  an API-ish gate (drop `document`/image/font/css; keep `application/json`,
  `application/graphql`, `text/*`) + a first-party-telemetry path heuristic (drop
  `/collect`, `/sentry`, `/gtm`, `/analytics`-style same-origin beacons — review Claim 3
  secondary). Deduped by `(method, normalized-url)`; the server-side `noise_hosts` filter
  is the second net (§4).
- **redaction:** a request body can carry creds/PII (a login POST) → strip
  `authorization`/`cookie`/`x-api-key` from recorded headers + redact obvious credential
  fields; keep the endpoint + shape, not the caller's secret.
- this metadata + request body is exactly what promotes an `endpoint_suspected` → confirmed
  `endpoint` on the platform (§4).

**b2 — response bodies (OPT-IN, off by default; the ONLY main-world footprint):** a
MAIN-world interceptor (`inject/xhr-hook.js`, `"world":"MAIN"`, conditionally registered
via `chrome.scripting` ONLY when the toggle is on — so the default build ships **zero**
main-world code) wrapping `window.fetch` + `XMLHttpRequest` to capture the **response**
body for in-scope API calls. Responses hold the biggest PII surface (user records):
- **default OFF**, behind `captureResponseBodies` (Capture Rules) + a one-time in-app
  consent note ("collects response DATA from in-scope API calls; not purged on delete
  until DEBT D47 lands").
- **scoped hard:** in-scope only; denylist; API content-types; per-body size cap (256 KB)
  + per-session count cap (bounds ever-changing poll responses).
- **trust:** main-world observations are page-forgeable (review Claim 1.3) → tag them a
  **lower-trust provenance** than webRequest-sourced metadata; the postMessage bridge
  carries a closure-held nonce (best-effort in a shared world).
- **permission cost:** conditional `chrome.scripting.registerContentScripts` needs the
  `scripting` permission (review Claim 1.4) — added ONLY for this opt-in slice; the
  on-by-default build needs no new permission.

Rejected: `chrome.debugger` (review Claim 5 — an un-suppressible "extension is debugging
this browser" infobar over the operator's authenticated session, DevTools mutual-exclusion,
and a scary `debugger`-on-`<all_urls>` grant; the main-world tradeoffs
[detectable/forgeable/early-miss race] are accepted knowingly). Rejected: response bodies
on by default (PII + D47).

## 4. Platform ingest changes  (grounded in the platform map, 2026-09-05)

Headline: **the platform already has the machinery for observed requests and source
maps; the extension path just never feeds it.** Most of D45's platform tail is wiring,
not greenfield.

**4c — source maps: NO platform change.** The platform already recovers + secret-scans
maps itself (`findings/sourcemapper.py`; three inputs — uploaded `source_map_ref`
blob, inline `data:` map, external `//# ` ref — resolved in `analyze._analysis_units`
`analyze.py:981-1050`). The extension already uploads a parsed map via `sourceMapContent`
(`capture_router._valid_source_map:331-365`, cap `max_source_map_bytes` 96 MiB). D45c is
**detection-only on the extension**: find the map via header/`.map` probe, then hand the
platform what it already knows how to consume. Nothing to build server-side.

**4a — inline scripts: (near) NO platform change.** `save-files.files` is a lenient
`list[dict]` (`SaveFilesIn` `capture_router.py:71-76`) — an inline script uploaded as an
ordinary file (its JS `content`, synthetic `url`) parses and flows through the existing
static analysis with no model/contract change. Optional additive nicety: a per-file
`kind:"inline_script"` label for the Sources UI (lenient model accepts it free; only a new
*response* field would break `capture_contract_test.py:72` + need a version bump).

**4b1 — confirmed endpoints: REUSE the existing correlate stage.** The platform's own
headless capture stage already records in-scope observed `{method,url}` into a
`capture-requests` blob (`capture/stage.py:190-194`), and `correlate/stage.py:correlate_run`
matches those to host-less endpoint findings and attaches a real
`Occurrence(engine="capture", host=…, raw_url=…)` to the matched finding
(`correlate/stage.py:74-91`) — promoting a `endpoint_suspected`/`endpoint` with runtime
provenance. **The extension path just doesn't populate it:** `analyze_start` emits
`discover.assets` WITHOUT a `requests_ref` (`capture_router.py:673-679`), and the correlate
consumer no-ops when it's absent (`correlate/stage.py:53-55`). So b1 =
1. accept observed `{method,url,status}` in `save-files` (lenient model → no contract break);
2. persist them as a `capture-requests` blob (`storage.BLOB_KINDS` already lists that kind,
   `storage.py:33`);
3. attach `requests_ref` to the `discover.assets` event `analyze_start` emits — the one new
   seam.
Then the existing correlate stage labels findings for free. **Design choice (flag in
review):** whether to ALSO mint a first-class confirmed `ENDPOINT` for an observation that
matches nothing static (today correlate only *attaches* to existing findings, never forges
one). A runtime-issued request IS a confirmed sink, so minting is defensible — but it needs
a `engine="capture"` writer + care re: `finding_hash` stability. Lean: start with
attach-only (zero new finding lane), add minting only if measurement shows real observed
endpoints with no static match.

**Noise parity server-side (second net under the client denylist):** observed hosts still
pass the read-time `noise_hosts.is_noise_host` filter in `list_findings`/`hosts`
(`noise_hosts.py:98-114`, reversible), so an analytics beacon that slips the extension
denylist is still hidden in results. NOTE the map's caveat: extension-ingest scope is inert
server-side (captured assets are never egressed, `capture_router.py:23-25`), so **scope
enforcement for observations must happen client-side** (the extension `isInScope`) — the
platform won't drop an off-scope observed request. This makes the client-side scope gate on
the b1/b2 paths load-bearing, exactly the no-noise constraint.

**4b2 — bodies (pragmatic v1, AS-BUILT): store in the capture-requests blob + secret-scan +
param hints.** No new blob kind and no migration — captured `reqBody`/`respBody` ride the existing
`capture-requests` blob as optional per-observation fields (`_normalize_observations` preserves +
re-caps them via `_capped_body`; new config caps `capture_max_request_body_bytes` /
`capture_max_response_body_bytes` / `capture_max_bodies`). The analysis is
`correlate/bodies.py::analyze_bodies`, run from `correlate_run` AFTER the endpoint occurrences
commit, BEST-EFFORT (its own txn, logs `correlate.bodies_failed`, never fails the stage):
- Kingfisher `scan_many` over all bodies → `SECRET` findings, `engine="capture"`, synthetic
  `capture-{request,response}://<host><path>` source_path. Same `normalize_secret_value` identity
  as a bundle secret (a token seen in both a payload and the bundle dedupes to one finding). Stored
  OFFSET-LESS on purpose — a captured body isn't a persisted blob, so reveal fail-closes on it
  (`no_offsets`) rather than promising bytes it can't reproduce (verified vs `reveal.py`).
- Light PARAM hints: a request body that cleanly parses as a JSON object or a strict urlencoded
  form contributes its TOP-LEVEL keys as `PARAM` findings, tied to the endpoint its URL matched in
  correlate (reuses that endpoint's identity, never a phantom). Conservative — no nested descent,
  capped 50 keys/body, skipped when not cleanly parseable.
Reuses `SECRET`/`PARAM` (no new FindingType, no migration) because main-world response bodies are
page-forgeable (lower-trust). Deep GraphQL-operation / JSON-schema extraction is the documented
follow-up, deferred per the operator's pragmatic-v1 choice. Idempotent (REQ-A3), RLS-scoped.

**Contract-version rule:** new *request* fields are free (lenient model, test doesn't assert
request shape); a new *response* field breaks `capture_contract_test.py:72` and must bump
`CAPTURE_CONTRACT_VERSION` (`capture_router.py:68`).

## 5. Rollout (final scope — operator-approved 2026-09-05)

Defaults: inline + source maps + endpoint confirmation + request/GraphQL bodies ON;
response bodies OPT-IN; eval hook DEFERRED (docs fix + inline cover D45a's real gap).

1. **Slice 1 — D45c + honesty** (extension-only, no contract change): source-map detection
   via `SourceMap:`/`X-SourceMap` header, then a SINGLE conventional `.map` probe (one
   request, NO retry-on-4xx — review Finding A: `ContentFetcher`'s 3× retry would add ~3
   requests + ~3s serial latency per map-less file). Correct `docs/OPERATING.md:57`. First.
2. **Slice 2 — D45a inline** (extension): inline `<script>` read → synthetic file keyed on
   page+ordinal, per-page cap, + the new content-based `inline-relevance.js` filter. No
   platform change required (lenient contract); optional additive `kind:"inline_script"`.
3. **Slice 3 — D45b1 endpoints + request bodies** (extension + platform): the xhr
   `webRequest` observer (`onBeforeRequest.requestBody` + `onCompleted`) → new observations
   field on `save-files` → `capture-requests` blob → `requests_ref` on the `discover.assets`
   event → the existing correlate stage labels findings. Attach-only first; mint-confirmed
   only if measured. Request-side additive (no version bump).
4. **Slice 4 — D45b2 bodies** (extension + platform): request bodies ON via webRequest
   `onBeforeRequest.requestBody` (no main world); response bodies OPT-IN via `inject/xhr-hook.js`
   main-world hook, conditional `chrome.scripting` registration (adds the `scripting` perm), + a
   Settings toggle. Both redacted + capped, bounded by a per-session total. Platform (pragmatic
   v1): bodies ride the capture-requests blob (no new kind / migration) + Kingfisher secret-scan +
   light param hints (§4b2). Off-by-default for responses; lower-trust provenance.

**Status (2026-09-05): all four slices BUILT.** Extension: 37 Node suites green + popup builds.
Platform: full fast lane green + ruff + mypy-strict + contract-test unchanged. Eval hook deferred
(operator-approved); deep body schema/GraphQL extraction deferred (pragmatic-v1). Pending: the §4
code-review gate + DEBT.md flip + commit.

Each slice: colocated tests, the §4 code-review gate, a DEBT.md note, no red trunk. Slices
1–3 land the on-by-default D45 value with zero main-world code and no new permission.

## 6. Test plan (matches repo conventions)

- Module behavioral (`vm`-sandbox / pure): `sourcemap-detector` header + convention
  paths; a new `inline-relevance` / `api-relevance` predicate module (pure, unit-tested
  like `asset-classifier`); redaction helper.
- Structural source-pins (`background.js` is not Node-importable): the new
  `webRequest` xhr listener registration + the inline/eval/api message routes +
  scope/denylist gating call-sites (mirror `test_mv3_listeners.mjs`,
  `test_background_engagement_wiring.mjs`).
- Platform: `capture_contract_test.py` for the new fields/version; a findings test that
  an `api_observation` confirms a matching suspected endpoint; a noise test that a
  denied host can't enter via the observation path.

## 7. Decisions (settled 2026-09-05)

1. **Response-body capture** — OPT-IN, off by default (`captureResponseBodies`); a
   login/response body carries PII/secrets filters can't drop (they're in legit in-scope
   responses) and D47 won't purge. Endpoint confirmation + request/GraphQL bodies stay ON.
2. **eval capture** — DEFERRED (review Claim 6: unreliable + noisy + low-value); ship inline
   capture + the OPERATING.md correction, D45a's real gap. Recorded in DEBT.md.
3. Everything else = the defaults above.

## 8. Adversarial design review — verdict + folded changes

§4-gate design review (2026-09-05, evidence-backed vs official MV3 docs + repo code):
**GO-WITH-CHANGES.** All required changes folded into §2/§3/§5:

- **C1 MAIN-world mechanism** — (a) main-world runtime IS subject to page CSP (benign for
  `fetch`/XHR reassignment; documented). (b) `document_start` main-world races page scripts →
  early requests/evals can be missed; documented as a b2 limitation (b1 metadata via
  webRequest has no such race). (c) resolved the "not-injected-when-off vs no-new-permission"
  contradiction — b2 uses conditional `chrome.scripting` registration (adds `scripting` ONLY
  for the opt-in slice); the default build ships no main-world entry. (d) main-world
  observations are page-forgeable → tagged lower-trust + a nonce'd bridge.
- **C2 webRequest bodies** — request bodies (incl. GraphQL) moved to the safe b1 path
  (`onBeforeRequest.requestBody`); b2 scoped to RESPONSE bodies only. This is the change that
  lets the on-by-default tier recover GraphQL ops without the PII/main-world cost.
- **C3 no-noise (NO-GO → fixed)** — the URL-based gate can't filter an inline analytics
  bootstrap on an in-scope page; added the REQUIRED content-based `inline-relevance.js`
  classifier + a first-party-telemetry path heuristic for b1.
- **C4 inline dedup** — synthetic URL keyed on page+ordinal (not hash) so re-renders
  supersede; + a per-page inline cap (SPA/Next.js churn otherwise unbounded).
- **C5 debugger** — rejection upheld; main-world tradeoffs acknowledged.
- **C6 eval** — deferred (inline + docs only).
- **Finding A** — `.map` probe = one request, no retry-on-4xx (avoid 3× amplification on the
  serial queue).
- **Finding B** — b1 has no operator-visible value until the platform confirmed-endpoint
  wiring lands → Slice 3 is extension+platform together.
- **Finding C** — add a test for a self-removing inline `<script>` (`.textContent` on a
  detached node is still readable).
