# Tech debt register

Known, deliberate debt - written down so it's visible to the next contributor (and
the next session) instead of living only in an AI's memory. Add here when you defer
something on purpose; link the code with a `# NOTE(DEBT):` comment where it helps.
Effort: S (hours) · M (a day-ish) · L (multi-day).

> **History (2026-09-04):** the resolved and won't-fix records for the pre-2026-09-03 register
> (D1-D39 - the D31-D37 dogfood fixes, the earlier D1-D30 items, and the D17/D18/D19/D23 residual-risk
> acceptances) were removed from this file to keep the live register lean. They are preserved in git
> history - `git log -p -- DEBT.md` recovers the full "why isn't X done" trail. This register now tracks
> only the currently-open items.

## Open debt

### 2026-09-03 review swarm — OPEN (D40–D54)

Uncovered by a 7-agent review focused on the chrome-extension capture journey (4 agents) plus the
platform (3 agents). **Cross-cutting theme (extension):** the capture core (MV3 durability, tenant
isolation, auth-context) is well-built, but the operator cannot *trust* it — its two worst outcomes,
"I captured nothing" and "my captures never uploaded," are the two the UI hides or misrepresents. The
Tier-1 items below are mostly small, verified-in-code fixes; several were flagged independently by
multiple agents (noted `◆N`). Evidence is `file:line` at time of review.

#### D40 · Silent no-op capture: fail-closed scope + `*.` wildcard mismatch + "CAPTURING" lie [S]  ·  correctness — Tier 1  ◆3
A first-time / Solo operator can sign in, press the single most prominent button, browse, and capture
**zero** with no error. Three compounding causes: (a) `normalizeRootDomains` never strips a leading
`*.`, so `*.target.com` — the syntax the popup's own placeholder suggests — is stored literally and
matches no host (`background.js:27`, `isInScope` exact/suffix-only at `background.js:711`; misleading
placeholder `src/popup/components/HomeView.jsx:137`, `src/popup/components/SettingsView.jsx:139`
"auto (active tab domain)"); (b) `isInScope` fails closed (captures nothing) when no scope is set, but
the Pause/Resume toggle has no scope guard and flips the card to a green pulsing "CAPTURING"
(`src/popup/app.jsx:136-149`); (c) the only counter-signal is a small amber "no scope · capturing
nothing" string (`src/popup/app.jsx:330-335`). **Fix:** strip `*.` in `normalizeRootDomains` (one
`.replace(/^\*\./,'')`); block the capture toggle when `scopeMode==='none'` with an inline "set a scope
or enable capture-every-tab" prompt (offer a one-tap "capture this site" → `activeHost`); fix the
placeholder. See also [[D41]] (delivery visibility), [[D44]] (scope safety).

#### D41 · Auth-token expiry mid-capture permanently drops captured JS [M]  ·  reliability — Tier 1
The login token TTL is 8h (`apps/platform/src/recon/config.py:226`). On a long/overnight engagement it
expires; `save-files` then returns 401 (`apps/platform/src/recon/api/capture_router.py:186-196`), which
the uploader classifies as non-retriable (`retriable=false` for any 4xx except 429,
`modules/batch-uploader.js:297`) and **drops the batch from the durable outbox** via `forget()`
(`modules/batch-uploader.js:154-169`). Capture keeps running, the popup's counts keep climbing, and
`lastPaired` is untouched on a throw so Settings still shows "paired ✓" (`modules/batch-uploader.js:306`)
— the operator ships a fraction of the surface and cannot recover the dropped files by re-login. **Fix:**
treat 401/403 as retriable (re-queue, don't drop), pause capture into a "session expired — sign in
again" state, resume the outbox after re-auth; keep 400/422 as the only permanent drops.

#### D42 · Capture pipeline has no operator-facing delivery / skip / failure visibility [M]  ·  correctness — Tier 1  ◆5
The worker already computes everything needed to answer "did it all land, and what was skipped?" —
`processingStats` (failedFiles, lastFailureReason/Url/Message) and uploader health (`lastError`,
`pendingQueueLength`, `droppedFiles`, `failedBatches`, `paired`) are returned by `getStatus`
(`background.js:1210-1222`) and `getStats` (`modules/batch-uploader.js:385-397`) — but **none of it is
rendered.** Home shows only js/maps/secrets counts (`src/popup/components/HomeView.jsx:300-305`); the
header "connected to workspace" dot is fake-green, mutated only by a manual Test, never by upload
outcomes (`src/popup/app.jsx:48,267-277`); per-file skips (`asset_too_large` `background.js:433-440`,
`fetch_failed` `:347-357`, `decompress_failed` `:370`, denylist/exclude `:273-279`) are silent; and every
error renders as a green success Toast that auto-dismisses in ~2.2s (`src/popup/components/ui.jsx:43-59`,
error call-sites `src/popup/app.jsx:222,235,246,250,276,408`). **Fix (mostly wiring existing data):** a
Home status strip "N uploaded · M pending · X failed/skipped (with reasons)"; drive the connection dot
from real upload results; add an error/warning Toast variant. The highest-leverage single item in this
batch.

#### D43 · Capture-pipeline reliability edges (four small correctness bugs) [S]  ·  reliability — Tier 1
Four independent, verified holes, each S-effort: (a) **no fetch timeout** — the uploader added an
`AbortController` because "Chrome has no default," but `ContentFetcher.fetch` has none
(`modules/content-fetcher.js:13-18`) and the queue is strictly serial (`background.js:315-317`), so one
blackholed in-scope asset (or its map) hangs `fetch()` and stalls *all* capture; (b) **8 MB under-cap**
— default `maxAssetMb` is 8 while the backend accepts 10, so 8–10 MB main bundles are silently skipped
(`background.js:432,808`); (c) **analyze-before-drain** — `analyzeSession` flushes with an 8s cap that
early-breaks on one transient failure (`modules/workspace-client.js:95-105`,
`modules/batch-uploader.js:214-226`), so Analyze can run on a partial set yet report "complete ✓"; (d)
**dedup-before-outbox race** — `processFile` awaits `dedupStore.put(hash)` (`background.js:506`) before
`batchUploader.enqueue` persists the outbox record (`background.js:541`), so a teardown in that gap marks
the file "seen" but never sends it. **Fix:** (a) 30s `AbortController` on the content fetch + bounded
concurrency; (b) default to 10; (c) block Analyze on `pendingQueueLength>0` (already exposed); (d)
persist the outbox entry before the dedup entry.

#### D44 · Scope-safety gaps: dependency-child bypass, `captureEverything` footgun, no CDN-apex discovery [S]  ·  supply-chain/security — Tier 1
Three scope-enforcement / rules-of-engagement gaps: (a) **dependency-child chunks bypass the scope
gate** — resolved child URLs are captured/uploaded with `isInScope` deliberately skipped (only denylist
+ exclude-mode-only third-party filtering apply), and the default `outOfScopeMode` is `tag`, so an
absolute cross-origin `import` fetches and uploads out-of-scope third-party JS
(`background.js:519-527,805`, `modules/dependency-extractor.js:83-85`); (b) **`captureEverything` is a
one-tap cross-tenant footgun** — it makes `isInScope` return true for every tab/window with no per-tab
binding (`background.js:711-721`, toggle `src/popup/app.jsx:414`), so left on it uploads unrelated
sites/tenants into the current engagement; (c) **CDN-apex bundles are dropped with no discovery aid** —
app JS served from a separate apex (`d123.cloudfront.net`) doesn't match the target root and is silently
dropped (`background.js:711-742`, dropped requests just `return` at `:292-296`) with no "out-of-scope
script host seen — add?" hint. **Fix:** apply `isInScope` to children (or gate behind an explicit
"follow cross-scope deps" opt-in); gate `captureEverything` behind confirm + auto-expire and/or a
per-tab "armed" binding; surface out-of-scope hosts that served `type:script` with one-click add.

#### D45 · Capture coverage gaps: inline/eval, XHR/JSON/GraphQL bodies, source-map header/`.map` probe [M]  ·  correctness — Tier 1
Capture is script-response-only, missing high-value post-auth recon the platform is otherwise trying to
reconstruct: (a) **inline & `eval`'d scripts are never captured** — `webRequest` filters
`types:["script"]` (`background.js:194-201`) and the content-script enumerates only `script[src]` +
resource-timing (`content-script.js:69-85,113-121`); inline `<script>` `.textContent` is never read —
and `docs/OPERATING.md:57` *falsely claims* inline/eval coverage; (b) **XHR/fetch/JSON/GraphQL response
bodies are ignored** (`manifest.json:22-29`, `isLikelyScriptResource` `background.js:689-709`) — passive
observation of the app's real calls would convert *suspected* endpoints (D29/D30 static ceiling) into
confirmed ones; (c) **source-map recovery is inline-comment-only** — `SourceMapDetector` reads only
`//# sourceMappingURL=` (`modules/sourcemap-detector.js:10-23`) and never consults the captured
`SourceMap:`/`X-SourceMap` header (`background.js:561-573` captured but not passed at `:382-383`) or
probes the conventional `<file>.js.map`. **Fix:** collect inline `<script>` bodies as synthetic files;
add an opt-in "capture JSON/config + XHR" mode; read the map header + attempt `url+'.map'`. At minimum,
correct the OPERATING.md claim.

#### D46 · Extension value-loop + activation affordances [M]  ·  maintainability — Tier 2  ◆2
The extension is a one-way uploader: value never returns to where the operator works, and first-run
activation is unguided. Bundle of feature gaps: (a) **no results in the popup** — after Analyze only
progress counts return; endpoints/secrets/OpenAPI require leaving to the web workspace
(`modules/workspace-client.js`/`src/popup/api.js:36-37` expose no findings call; label
`src/popup/components/HomeView.jsx:213-219`); (b) **no toolbar badge** — `chrome.action.setBadgeText` is
never called, so capture state/progress is invisible while the popup is closed; (c) **no capture
history** — `newSession` clears all state (`background.js:1089-1097`) and `handleMessage`
(`background.js:900-917`) has no list/re-open/re-analyze for past sessions; (d) **export omits code &
"clear captures" is unreachable** — `getExportData` uses `includeContent:false` (`background.js:1256`,
`modules/export-builder.js:1-30`) and the `clearFiles` handler (`background.js:905,1181`) is wired to no
UI; (e) **no Burp/Caido/HAR interchange** and **no first-run onboarding** (`src/popup/components/LoginView.jsx`
has no help). **Fix (incremental):** post-analyze summary card via a `sessions/{id}/findings/summary`
fetch; a live badge; a persisted per-session summary list; an "include code" export toggle + reachable
"clear"; HAR export; a 3-step first-run coach.

#### D47 · "Delete" doesn't delete object-storage blobs — REQ-S4 purge unmet [M–L]  ·  supply-chain/security — Tier 2
`docs/REQUIREMENTS.md:88` mandates retention/purge as a **MUST**. `DELETE /sessions/{id}` hard-deletes
Postgres rows only; the S3/MinIO blobs (raw JS, source maps, recovered sources — the bytes secrets /
config-GUIDs / internal IPs live in) are never touched — no `delete_blob` exists in `storage.py`, and
`apps/platform/src/recon/sessions/service.py:223-226` states outright that blobs "are not swept here; a
GC pass is future work." Deleting a session at engagement close (or on a compliance request) leaves the
sensitive bytes orphaned in the bucket forever: unbounded storage growth + a breach/compliance liability
for a tool whose premise is trustworthy secret handling. Not previously tracked. **Fix:** blob sweep on
session/run delete (or a scheduled GC diffing live `finding`/`run_asset` refs against the bucket) + a
documented default retention window.

#### D48 · Sensitive-action audit trail isn't bound to verified identity [S]  ·  supply-chain/security — Tier 2
The reveal audit mechanism is durable and denial-inclusive (`apps/platform/src/recon/probe/reveal.py:111-142`),
but the `actor` it records is a client-supplied, optional free-text request field
(`apps/platform/src/recon/api/probe_router.py:23,27,63,85`), never derived from the verified
`get_principal()` identity auth already resolves (`apps/platform/src/recon/api/deps.py:68-80`);
pause/cancel/resume record no actor at all (`apps/platform/src/recon/api/runs_router.py:364-391`). In a
tenant shared by multiple operators (docs already note "no RBAC — everyone is effectively an operator")
the reveal log is spoofable/blank and run-control actions are unattributed — quietly defeating the
accountability guarantee. **Fix:** when auth is on, derive `actor` server-side from `get_principal()`
(ignore the client field); add it to pause/cancel/resume/delete event payloads.

#### D49 · Findings prioritization is absent end-to-end [M]  ·  correctness — Tier 1  ◆2
Nothing tells an operator what to look at first on a 500+/2000-asset run. The `severity` column exists
but no pipeline path ever populates it (`apps/platform/src/recon/db/models.py:324`,
`apps/platform/src/recon/findings/store.py:119,139`; the Overview widget even hardcodes a heuristic and
comments that "findings carry severity = null" `apps/platform/web/src/features/overview/OverviewPanel.tsx:12-26`).
The one real signal that *is* computed — param `risk_tags` (auth/admin/idor/flag,
`apps/platform/src/recon/findings/analyze.py:1253-1258`) — reaches the browser in `finding.attributes`
but is never rendered anywhere in `apps/platform/web/src` (and path-segment params, the common IDOR
shape, are excluded even from the OpenAPI `x-recon-risk` at `apps/platform/src/recon/probe/openapi.py:149-159`).
The Findings page has facets + search but **no sort control**. **Fix:** a deterministic, explainable
priority score (shadow status + risk tags + secret/internal-IP type + unattributed) surfaced as a real
sort key + badge; render `attributes.risk_tags` as a badge + 5th facet (near-free, data is client-side).

#### D50 · Findings triage & reporting don't scale [M]  ·  performance — Tier 1  ◆2
Breaks at exactly the scale the tool just built for (crawl cap 500→2000; an E2E already hit 567
findings): (a) **triage is one-at-a-time** — `TriageControls` takes a single hash
(`apps/platform/web/src/features/findings/TriageControls.tsx:6`, `apps/platform/src/recon/api/probe_router.py:48-73`),
no multi-select/bulk-dismiss; (b) **the only export is OpenAPI** — no findings CSV/JSON, no report
(`apps/platform/web/src/features/export/ExportSpecButton.tsx`,
`apps/platform/src/recon/api/export_router.py` json/yaml only); (c) **the findings list is unpaginated
and unwindowed** — `GET /runs/{id}/findings` takes no limit/offset
(`apps/platform/src/recon/api/findings_router.py:18-27`, `apps/platform/src/recon/findings/queries.py:178-183`)
and the UI renders every row into the DOM (`apps/platform/web/src/features/findings/FindingsPage.tsx:165-166`)
— the same main-thread-freeze class [[D35]] fixed for Sources, now unaddressed for a multi-thousand-row
findings response; no sort control either. **Fix:** limit/offset (or cursor) on the API + D35's spacer
virtualization; multi-select + bulk triage looping the existing endpoint; a client-side findings
CSV/JSON download from already-fetched data.

#### D51 · Probe artifacts aren't ready-to-fire: auth header omitted, WebSocket dead-ends [S]  ·  correctness — Tier 2
The Probe panel's whole promise is a one-step runnable request, but: (a) **`to_curl`/`to_http` never emit
the observed auth** — they print a static `# add auth/headers here` comment
(`apps/platform/src/recon/probe/serialize.py:73-96,99-113`) even though `request.auth` (header + scheme)
is known and already used to build OpenAPI `securitySchemes` (`apps/platform/src/recon/probe/openapi.py:179,343`),
so every authenticated endpoint's copied curl 401s with no hint; (b) **WebSocket findings dead-end** —
WS/WSS ops are `probeable=False` (`apps/platform/src/recon/probe/reconstruct.py:22,217`) and both
serializers `return None` (`serialize.py:59-60,99-100`), giving "not probeable" instead of a one-line
`websocat`/`wscat` scaffold. **Fix:** emit a real placeholder header per `request.auth`; generate a
`websocat` command for WS/WSS analogous to `to_curl`.

#### D52 · Recon coverage: no source full-text search; postMessage/storage sinks aren't a finding type [M]  ·  correctness — Tier 2
Two coverage gaps that lose real attack surface: (a) **no full-text search across recovered sources** —
`apps/platform/src/recon/probe/sources.py` has no grep endpoint, `SourcesPage` filters file *names*
only, and `CodeViewer` windowing (`apps/platform/web/src/features/sources/CodeViewer.tsx:73-74,117-134`)
means even the browser's native Ctrl-F silently misses matches outside the viewport — "grep the whole
target for X" is impossible in-tool on a 500-file run; (b) **postMessage / Web-Storage / cookie sinks are
not a `FindingType`** — the enum has 9 members and none for a `postMessage` listener or
`localStorage`/`sessionStorage`/cookie access (`apps/platform/src/recon/domain.py:65-128`), though the
tree-sitter pass is already positioned to catch the call sites (common high-signal client-side bug
classes). **Fix:** a run-scoped source-search endpoint over stored blobs; a new detector + FindingType
for postMessage/storage sinks mirroring the recent internal-IP pattern ([[D33]]).

#### D53 · Platform observability & ops gaps [M]  ·  reliability — Tier 2
The async spine is solid but under-instrumented for operation: (a) **no metrics/tracing** — `REQ-S3`
(`docs/REQUIREMENTS.md:87`) requires per-stage metrics + traces; there is no metrics lib, `/metrics`
route, or OTel span anywhere (`apps/platform/src/recon/observability.py` is logging-only); (b) **no
queue/DLQ visibility** — `/healthz` checks only Redis ping + `SELECT 1` (`apps/platform/src/recon/api/app.py:83-87`),
`pending_count()` exists but is called only from tests (`apps/platform/src/recon/queue/streams.py:159-163`),
and there's no admin/ops route; (c) **the worker container has no healthcheck** — `docker-compose.yml`
gives `api` one but not `worker`, so a *hang* (not a crash) is never detected/restarted and runs sit
`stalled` forever; (d) **no backup/restore** procedure for Postgres or MinIO anywhere in the repo; (e)
**no horizontal worker scaling** — the Redis consumer name is the hardcoded literal `"worker-1"`
(`apps/platform/src/recon/worker/main.py:275,287`), so replicas are indistinguishable in Redis
bookkeeping. **Fix:** `prometheus_client` counters/histograms at `/metrics`; extend `/healthz` with S3 +
per-queue pending/DLQ; a worker liveness healthcheck; a documented `pg_dump` + `mc mirror` recipe;
derive the consumer name from hostname/PID.

#### D54 · Continuous-use & collaboration features [L]  ·  maintainability — Tier 2
The product is built to re-run a target over time and be used by a team, but the payoff features are
absent: (a) **no run-to-run diff** — `REQ-D5` specifies it; only a same-hash *sightings count* exists
(`apps/platform/src/recon/findings/queries.py:84-268`), no diff route, so "what's new/gone since last
scan" (the point of re-running) doesn't exist; (b) **no run-finished notification** — progress is
SSE-only, nothing fires on terminal state if the tab is unfocused (`apps/platform/web/src/features/progress/RunProgress.tsx`;
runs can be ~19 min per [[D38]]); (c) **no in-product user invite** — the only way to add an operator is
the `seed-admin` CLI (`docs/ARCHITECTURE.md:173-177`), and `role` is never enforced outside tests, so a
2-person team can't self-serve a second seat at all; (d) **global search is a decorative placeholder** —
the TopBar box is an inert `<div title="Search — coming soon">` (`apps/platform/web/src/shell/TopBar.tsx:5-6,18-22`).
**Fix (independent slices):** an MVP two-run diff (partial-aware per REQ-D5); a browser `Notification`
on state→terminal; an admin-only invite endpoint reusing `seed-admin`; a client-side session search to
start.
