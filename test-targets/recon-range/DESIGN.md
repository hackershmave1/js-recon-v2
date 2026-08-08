# recon-range — design spec

Date: 2026-08-05 · Status: proposed (§4 adversarial gate applied — verdict BUILD WITH CHANGES; awaiting user approval) · Branch: `spike/platform-ingest`

> Calibration in §4/§5 was corrected against code by the §4 adversarial review (subagent `ad413c13c03704fe7`). Every expected `value` below is the literal string the platform emits, verified at the cited `file:line`.

## 1. Purpose

`recon-range` is a deliberately-messy, self-contained JS web app that serves as the **controlled verify vehicle** for the extension→platform convergence. It replaces "capture a random live site" with a target whose exact API surface and planted secrets are documented up front (the *answer key*), so the extension→platform pipeline can be scored found / missed / unexpected instead of eyeballed.

It is the vehicle for the **Phase 4 gate**: the `apps/capture/{api,web}` deletion stays blocked until the real MV3 extension drives the platform end-to-end against this target and the score is green. This spec covers only the target, its answer key, and the scoring script — not the cutover.

Non-goal: a general recon benchmark. This is scoped to *this* platform's proven capabilities on *this* capture path.

## 2. Grounded constraints (why the answer key is calibrated, not aspirational)

Two read-only grounding passes plus the §4 adversarial review established what this path can and cannot do. The answer key is built to those facts so a "miss" means a real pipeline defect, not a known blind spot.

### 2.1 Capture path — `apps/capture/chrome-extension/`
- Capture = `chrome.webRequest.onCompleted` for `script` + a credentialed re-fetch of each script URL (`background.js:184-191`, `content-fetcher.js:14-18`). **Only network-loaded `<script src>` is captured; inline `<script>` is never captured** (`content-script.js:43`, no HTML scraping). ⇒ **all recon-relevant code must live in external chunks.**
- Manual trigger: capture is gated by `isCapturing`, flipped by the popup `startCapture`/`stopCapture` (`background.js:283`). Passive after start.
- **Fail-closed host scope** (`background.js:699-730`): only hosts in `domainScopes` are captured; a different registrable domain is dropped unless added or `captureEverything` is on. ⇒ **the target's own JS must be served same-origin**; third-party *scripts* are irrelevant (we plant third-party *calls* in our own code, not their bundles).
- Lazy `import()` chunks are captured (each is a real network script request) **while capture is active and the host is in scope**.
- Default denylist drops `*/jquery*.min.js`, `*/gtag/js*`, GA/GTM/DoubleClick, `/wp-content/plugins/*`, `/wp-includes/*` (`asset-classifier.js:21-24`). ⇒ **do not self-host a file named `jquery*.min.js`**; write jQuery-shaped calls into our own bundle instead.
- Source maps captured only from a served `//# sourceMappingURL=` comment (first match only), inline `data:` or same-origin `.map`, and the map must `JSON.parse` (`sourcemap-detector.js:10-23`, `background.js:399-405`). Upload fields: `sourceMapUrl`, `sourceMapContent` (parsed JSON), `hasSourceMap`, `sourceMapFetchStatus`. ⇒ **both bundlers must emit the comment + serve a valid same-origin `.map` that includes `sourcesContent`** (see §2.2 source-map note).
- Re-fetch model ⇒ avoid `blob:`/`data:` chunk URLs and single-use signed chunk URLs (won't reproduce). 10 MB/file cap, batch size 5, 30 s abort — irrelevant at this target's size.

### 2.2 Extractor — `apps/platform/src/recon/findings/` (Vespasian = in-process tree-sitter, `extract.py`)
- **Detected forms** (method always recovered): `fetch` / `window.fetch`, `XMLHttpRequest.open`, `axios()` / `axios.request` / `axios.get|post|put|patch|delete|head`, `axios.create()` instances (joined to a static `baseURL`), jQuery `$.ajax|$.get|$.post|$.getJSON`, `new WebSocket`.
- **URL recovery**: string literal ✓, template literal ✓ but **kept verbatim** — a `${x}` interpolation stays literally `${x}` in the value (`extract.py:121-123`). **Segment templating (`normalize.py:182-196`) only rewrites literal numeric / uuid / hex / high-entropy segments** to `{id}`/`{uuid}`/`{hash}` — it does *not* touch `${x}`. `${CONST}`-fold + base-join apply to **fetch + axios only** — XHR/jQuery/WebSocket use the plain string (no base-join).
- **Endpoint value form** (`normalize.py:218-229`): `"<METHOD> <path>"`, and when the URL has query params the **sorted, de-duped query *names*** are appended as `?name1&name2` (values dropped, `normalize.py:206-215`). The path before `?` is the *operation* (`operation_of_endpoint_value`, `normalize.py:238-243`). Endpoint finding `attributes` = `{"kind","method"}` only (`analyze.py:490-494`).
- **Params are their own findings** — `type=="param"`, value `"<METHOD> <templated-path> <location>:<name>"`, attributes `{"location","name"}` (`normalize.py:232-235`, `analyze.py:497-508`). Query names appear **both** in the endpoint value suffix and as `param` findings; body names appear **only** as `param` findings. Body names come from an object literal or `JSON.stringify({...})`. **Path params are not structured findings**; **request headers are never read** (`Authorization`, `X-API-Key`, HMAC/signature headers invisible).
- **Never detected (leave no trace at all)**: `EventSource`; untaught custom HTTP wrappers; aliased/poisoned axios instances (incl. an axios-create instance whose variable name is redeclared/shadowed/reassigned anywhere in its file — `collect_base_env` poisons it, `extract.py:170-237,254-255`).
- **Finding types**: `"endpoint"`, `"secret"`, `"param"` (`domain.py:64-71`).
- **Secrets**: the **real Kingfisher 1.106.0 binary**, `--no-validate`, whole-file scan (comments included) (`kingfisher.py:202-206`). Detected iff the built-in ruleset has a rule for the class. Identity = `provider:sha256(token)` in the finding `value`, provider/rule also in `attributes.rule`/`attributes.name` (`normalize.py:281-286`, `analyze.py:546`); raw token never returned, secret evidence redacted (`queries.py:369,450`). **Verified in-repo: Stripe + AWS only** (`kingfisher_test.py:95`, `normalize_test.py:144-146`). GitHub/Slack/HMAC are *unconfirmed* — informational until a live run proves them.
- **Scope is never a finding attribute** — no in/out-of-scope tag anywhere in `findings/`. Captured assets arrive pre-fetched (discover/fetch no-op). ⇒ out-of-scope host *calls* inside captured JS surface as normal endpoints; match them on `occurrences[].host` (`analyze.py:485`).
- **Source maps re-extract**: a usable map makes analysis re-run `extract()` on each recovered original source file, attributing endpoint/param findings to real paths (`analyze.py:433-460`); `coverage.source_map=="capture"` for a captured per-asset map (`analyze.py:224,469-470`). **Recovery requires the map to carry non-empty `sourcesContent`** — with none, `recovered.files` is empty and analysis **falls back to the minified bundle** (`analyze.py:456-457`), where minified shapes can break. `source_map_origin="capture"` falls back on a bad map instead of raising. **Secrets are not re-scanned on recovered sources** — a secret `occurrence.source_path` is always `input.js` (`analyze.py:364,540`).

## 3. Architecture

One shared vanilla-ESM source, compiled by both bundlers, served static + same-origin, captured, analyzed, scored.

```
test-targets/recon-range/
  README.md                 # what it is + the verify runbook + capture-config + tenant-UUID lookup
  answer-key.json           # machine-readable ground truth (single source; one key, two runs)
  package.json              # scripts: build:vite, build:webpack, serve:vite, serve:webpack, score, test
  src/                      # SHARED source — plain ESM .js (both bundlers parse ESM + import() natively)
    main.js                 # entry (external): IntersectionObserver infinite-scroll → import() lazy chunks
    api/
      profile.js            # fetch: literal, template, query, JSON body                    (main bundle)
      cart.js               # XMLHttpRequest.open: method + literal numeric segment          (main bundle)
      thirdparty.js         # GA / Stripe / Sentry call literals (third-party surfacing)      (main bundle)
      secure.js             # fetch with auth + HMAC headers (URL is a should-find)           (main bundle)
      inventory.js          # axios.create({baseURL}) UNIQUE unshadowed instance + axios.delete (LAZY 1)
      social.js             # $.ajax / $.getJSON shaped calls (no real jQuery loaded)         (LAZY 2)
      live.js               # new WebSocket(wss://…)                                          (LAZY 3)
      blindspots.js         # EventSource, concat URL, variable URL, untaught wrapper
    secrets.js              # planted FAKE secrets (well-formed, non-live) incl. one in a comment
  build/
    vite/    { vite.config.js, index.html }      # build.sourcemap:true (emits sourcesContent); external entry
    webpack/ { webpack.config.js, index.html }   # devtool:'source-map' (emits sourcesContent); runtimeChunk external
  scripts/
    score.mjs               # GET /runs/{id}/findings → diff vs answer-key.json → found/missed/unexpected + PASS/FAIL
    score.test.mjs          # colocated: fixture findings + fixture key → asserts the diff + verdict
    build-invariants.test.mjs  # asserts each dist has external chunks + .map per chunk + sourceMappingURL comment + non-empty sourcesContent
    answer-key.test.mjs     # asserts answer-key.json internal consistency
```

Decisions:
- **Vanilla ESM `.js`, no TypeScript** — both bundlers handle ESM + dynamic `import()` with zero loaders (YAGNI over a shared TS toolchain).
- **Lazy chunks triggered by scroll/`IntersectionObserver`, never gated on a fetch resolving** — a data call may 404 harmlessly, but the chunk must always load so it is always captured. Endpoints unique to a lazy chunk prove chunk capture.
- **No live backend** — calls target documented hosts and fail harmlessly; static recon needs the call in the code, not a response.
- **Source maps forced on with `sourcesContent`** in both bundlers (emitted comment + same-origin external `.map`), exercising Phase 3 per-asset `source_map_ref` + `source_map_origin="capture"` re-extraction.
- **The `inventory.js` axios instance gets a unique, never-shadowed, never-reassigned name** (e.g. `inventoryApi`) or the base-join silently drops (`extract.py:254-255`).
- **Two origins** (`serve:vite` :4173, `serve:webpack` :4174) — same host `localhost`, so one `domainScopes` entry covers both; each build's chunks/maps are same-origin within its port.

## 4. Answer key (`answer-key.json`)

One key, calibrated to §2.2 with §4-gate corrections. Endpoint `value`s below are the **exact** emitted strings.

### 4.1 `should_find` — a missing one is a real defect
| id | form | source (chunk) | expected endpoint `value` | expected `param` findings |
|---|---|---|---|---|
| ep-profile | `fetch` literal | profile.js (main) | `GET /api/v1/profile` | — |
| ep-user | `fetch` template `${userId}` | profile.js (main) | `GET /api/v1/users/${userId}` (verbatim) | — |
| ep-search | `fetch` template + query | profile.js (main) | `GET /api/v1/search?limit&q` | query:`q`, query:`limit` |
| ep-order | `fetch` POST + JSON body | profile.js (main) | `POST /api/v1/orders` | body:`sku`, body:`qty` |
| ep-cart | `XHR.open` PUT literal numeric | cart.js (main) | `PUT /api/v1/cart/{id}` (42→`{id}`) | — |
| ep-secure | `fetch` (auth+HMAC headers) | secure.js (main) | `GET /api/v1/secure` | — (headers invisible — see 4.2) |
| ep-inv | `axios.create` instance GET | inventory.js (lazy 1) | `GET /api/v2/inventory` | — |
| ep-checkout | axios instance POST body | inventory.js (lazy 1) | `POST /api/v2/checkout` | body:`token` |
| ep-session | `axios.delete` | inventory.js (lazy 1) | `DELETE /api/v1/session` | — |
| ep-config | `$.getJSON` | social.js (lazy 2) | `GET /api/v1/config` | — |
| ep-feedback | `$.ajax` POST body | social.js (lazy 2) | `POST /api/v1/feedback` | body:`msg` |
| ep-ws | `new WebSocket` | live.js (lazy 3) | `WSS /ws/live` (host on occurrence) | — |
| ep-ga | third-party `fetch` | thirdparty.js (main) | match `occurrences[].host == www.google-analytics.com` | — |
| ep-stripe | third-party `fetch` POST | thirdparty.js (main) | match host `api.stripe.com`, op `POST /v1/tokens` | — |
| ep-sentry | third-party `fetch` POST | thirdparty.js (main) | match host `o0.ingest.sentry.io` | — |

Matching rule: endpoints match on **method + operation** (value split on space, path split on `?`); third-party rows match on **host**; params match on a `param`-type finding with `attributes.location`+`attributes.name`. Endpoints unique to lazy chunks (ep-inv, ep-checkout, ep-session, ep-config, ep-feedback, ep-ws) double as the chunk-capture proof.

### 4.2 `known_blind_spots` — planted to prove limits; **expected-missing, not failures**
| id | construct | source | expected platform behavior |
|---|---|---|---|
| bs-eventsource | `new EventSource('/api/v1/stream')` | blindspots.js | no finding at all |
| bs-concat | `fetch('/api/v1/'+resource)` | blindspots.js | increments `coverage.unattributed` |
| bs-variable | `const u = pickUrl(); fetch(u)` | blindspots.js | increments `coverage.unattributed` |
| bs-wrapper | `makeClient().get('/api/v1/hidden')` | blindspots.js | no trace (untaught wrapper) |
| bs-headers | `Authorization` + `X-Signature`(HMAC) + `X-Timestamp` on ep-secure | secure.js | the URL (`ep-secure`) is found; the **headers/signing are invisible** — documented, not scored |

### 4.3 `secrets` — real Kingfisher classes (values FAKE + well-formed, non-live)
| id | class | placement | gate |
|---|---|---|---|
| sec-stripe | Stripe secret key `sk_live_…` | secrets.js const | **must-find** (verified class) |
| sec-aws | AWS access key id `AKIA…` + secret | secrets.js const | **must-find** (verified class) |
| sec-comment | 2nd Stripe key `sk_live_…` inside a `/*! … */` legal comment | secrets.js comment | **must-find** — proves whole-file scan (legal comment survives minification; secrets scan the minified bundle, not recovered sources) |
| sec-github | GitHub token `ghp_…` | secrets.js const | informational (unverified class) |
| sec-slack | Slack token `xoxb-…` | secrets.js const | informational (unverified class) |
| sec-hmac | HMAC signing secret const | secrets.js const | informational (stretch) |

All secrets are synthetic, non-live, local-only fixtures. `--no-validate` flags on pattern without contacting providers. Distinct fake tokens per row so each is its own finding.

## 5. Scoring (`scripts/score.mjs`)

Args: `--run <run_id>` `--tenant <tenant_uuid>` `--base http://localhost:8000`. Steps:
1. `GET {base}/runs/{run_id}/findings` with header `X-Tenant-Id: <tenant_uuid>`. **The header must be the tenant UUID, not the name** — `get_tenant_id` 400s a non-UUID (`deps.py:24-34`); the capture tenant is named `capture-spike` (`config.py:93`) with a random UUID (`capture_router.py:80-86`) that no endpoint returns, so it is resolved out-of-band (see §6) and passed in.
2. Partition `findings[]` explicitly by `type` into `endpoint` / `param` / `secret` (all three exist — `domain.py:64-71`).
3. **Endpoints**: for each `should_find`, match method + operation (value before `?`); for third-party rows match `occurrences[].host`. Report found / missed. Endpoint findings not in the key → `unexpected` (informational).
4. **Params**: for each expected `param`, require a `param`-type finding with matching `attributes.location` + `attributes.name`.
5. **Secrets**: collect providers from each secret finding's `attributes.rule`/`attributes.name` (fallback: the `value` `provider:` prefix). Require the **must-find** classes (Stripe ×2 incl. the comment one, AWS). GitHub/Slack/HMAC informational.
6. **Source-map re-extraction** (Phase 3 proof): assert `coverage.source_map == "capture"` (accept `"inline"`; `analyze.py:224,469-470`), `coverage.sources_recovered > 0`, and that ≥1 **endpoint or param** finding has an `occurrences[].source_path` ≠ `input.js` (secrets always `input.js`, so never assert on them — `analyze.py:364,540`).
7. **Blind spots**: assert no endpoint finding matches bs-eventsource / bs-wrapper, and `coverage.unattributed >= 2` (concat + variable). A blind spot that *did* resolve is surfaced as a note, not a failure.
8. Print a table + verdict. **PASS = all `should_find` endpoints found + all expected `param` findings present + must-find secrets present + source-map re-extraction observed (step 6).** Blind spots, informational secrets, and `unexpected` do not fail. Exit non-zero on FAIL.

The same key scores both the Vite run and the Webpack run (two invocations); divergence between bundlers is itself a reported signal.

## 6. Verify runbook (README.md, user-side in real Chrome)

The in-app browser can't drive an MV3 extension, so the capture is user-side; the build + scoring are automatable.
1. `npm run build:vite && npm run serve:vite` (→ http://localhost:4173).
2. Real Chrome + the extension: popup → Settings → Workspace URL = `http://localhost:8000`; add `localhost` to capture scope (fail-closed); Start capture.
3. Load the target, scroll to the bottom to trigger every lazy `import()`; Stop capture.
4. Trigger analysis (`POST /api/sessions/{ext_id}/analyze/start`, or the popup's Analyze); note the returned `run_id`; wait for progress → done.
5. Resolve the capture tenant UUID once (documented one-liner):
   `docker compose -f apps/platform/docker-compose.yml exec -T postgres psql -U recon -d recon -tAc "select id from tenant where name='capture-spike'"`
6. `npm run score -- --run <run_id> --tenant <tenant_uuid>` → read the verdict.
7. Repeat 1–6 with `build:webpack` / `serve:webpack` (:4174).

(Fast-follow candidate, out of scope here: a flag-gated `GET /api/capture/tenant` on the platform so step 5's UUID lookup isn't manual.)

## 7. Testing (colocated, TDD)

- `score.test.mjs` — fixture findings payloads (endpoint hit, missed, third-party by host, `param` hit/miss, secret-by-provider, `unattributed>=2`, a recovered `source_path`, `source_map=="capture"`) + fixture key → assert the diff buckets and the PASS/FAIL verdict, including the source-map gate and the three-type partition.
- `build-invariants.test.mjs` — after each build, assert dist has ≥3 external chunk files, a `.map` per chunk, a `//# sourceMappingURL=` comment in each chunk, **and non-empty `sourcesContent` in each `.map`** (the invariant §2.2 re-extraction depends on).
- `answer-key.test.mjs` — assert `answer-key.json` is internally consistent (unique ids, every `should_find` names a real `src` file, no id collisions across buckets, every third-party row carries a `host`).

Run scoped, not the whole repo suite.

## 8. Out of scope / YAGNI

- No live backend, DB, or auth on the target; no TypeScript, no framework.
- No infinite-scroll beyond enough scroll steps to load all 3 lazy chunks.
- No attempt to make blind-spot constructs detectable — they exist to document limits.
- Not wired into CI (needs a real browser + extension); an on-demand gate.
- No platform code changes (the tenant-lookup endpoint is a noted fast-follow, not built here).

## 9. Risks / debt

- **Bundler minification of call shapes.** A bundler could mangle a detected shape under minification. Primary mitigation: `sourcesContent` maps → analysis runs on the *original* source (§2.2). Backstop: `build-invariants` + the first run reveal it; if a shape breaks, lower that build's mangling for that chunk or record it as a documented bundler-specific blind spot.
- **`sourcesContent` absence** would silently downgrade to minified-bundle analysis and can fail multiple should-finds + the source-map gate — hence the explicit build-invariant.
- **axios-instance shadowing** silently drops the base-join (`extract.py:254-255`) — mitigated by the unique-name decision (§3); the answer key assumes `/api/v2` is joined.
- **Kingfisher class coverage** for GitHub/Slack/HMAC asserted from the ruleset, not verified in-repo — already informational.
- **Tenant UUID is resolved out-of-band** (manual psql step) — acceptable for an on-demand gate; endpoint is a fast-follow.
- **`localhost` in capture scope** could capture unrelated localhost scripts — mitigated because the extension skips its own workspace origin and the target runs on dedicated ports.
- **Minification strips `//` comments and tree-shakes unused exports.** Secrets are scanned on the minified bundle (not recovered sources), so: the comment secret must be a preserved `/*! … */` legal comment; and the secret constants must be *referenced* (e.g. `secrets.js` exports a `KEYS` object that `main.js` pins via `window.__reconKeys = KEYS`) so their string values survive into the captured bundle.
- **Canonical vendor example keys may be whitelisted** by Kingfisher rules (e.g. the AWS docs example key) — use plausible non-example fakes and confirm detection on the first live run.
- **jQuery calls use a local `$` stub** so the target runs without loading real jQuery; the extractor is shape-based on the `$` receiver, but if a locally-declared `$` is not treated as jQuery, the fallback is a self-hosted real `$` global (a file **not** named `jquery*.min.js`, which the extension denylists).
```
