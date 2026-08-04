# Capture → Upload pipeline refactor — notes

MV3 capture/upload/analyze pipeline rebuilt for **durability, backpressure, and a decoupled
analysis flow**. Goal: browsing an authenticated app captures hundreds of JS files reliably —
without losing uploads when the service worker is torn down, without re-doing work, and without
the single-worker backend choking on synchronous analysis mid-capture.

Nothing here is committed (repo convention: the working tree already carries the prior redesign
uncommitted). The extension's `background.js` + `modules/` load unbundled; only popup source
(`src/popup/**`) needs `npm run build`.

## What changed (by concern)

### Durability (survive the MV3 service-worker teardown)
- **Stable session id** — `modules/session-store.js` persists `reconSessionId` in
  `chrome.storage.local`, restored in `initialize()`, rotated only on an explicit New Session.
  Previously a new id was minted on every worker respawn, fragmenting one browse into many
  backend sessions.
- **Durable upload outbox** — `modules/idb-store.js` (IndexedDB) backs `BatchUploader`. Files are
  persisted **before** the network attempt (keyed `sessionId:contentHash`) and forgotten only
  after the server accepts (or permanently rejects) them. On boot the outbox is rehydrated and
  drained, so a teardown mid-flight loses nothing (re-upload is idempotent: backend dedupes on
  `(session_id, content_hash)`).
- **Durable dedup set** — captured hashes persist to IndexedDB and rehydrate on boot, so a
  respawn doesn't re-fetch/re-hash/re-upload already-captured files.
- **Flush alarm** — `chrome.alarms` (`flushOutbox`, 1-min) is a cold-respawn drain net, armed only
  while unsent files exist; the 5s in-memory timer still handles the hot path during active capture.
- Added `alarms` + `unlimitedStorage` permissions.

### Correctness
- **Size cap aligned to the backend** — client per-file cap 50MB → **10MB** (server rejects >10MB),
  popup slider 25→10, legacy `maxAssetMb` clamped. A rejected file used to poison the batch into
  an infinite retry.
- **Non-retriable uploads are dropped, not retried forever** — 4xx (except 429) → drop + record;
  network/5xx/429/timeout → re-queue.
- **Upload timeout** — `upload()` fetch now has a 30s `AbortController` (Chrome has no default),
  so a blackholed workspace can't hang the pipeline.
- **MV3 listeners registered synchronously** at bootstrap (first worker turn) with handlers gated
  on `this.ready`, so the event that woke the worker (a page's first script, or the flush alarm)
  isn't dropped.

### Decoupled analysis (capture fast, analyze on demand)
- Bulk uploads send `metadata.disableAnalysis: true` when "Analyze on upload" is off, so the POST
  is a fast store on the single-worker backend (this also suppresses the backend's smart-triggers,
  which `performAnalysis:false` alone does not).
- **Analyze on demand** — the popup "Analyze N scripts" button flushes then calls the backend's
  existing async threaded job `POST /api/sessions/{id}/analyze/start`; progress is polled from
  `GET /api/sessions/{id}/analyze/progress` and drives the captures feed's
  `ingested → analyzing → analyzed` lifecycle.

## Files
`background.js`, `modules/batch-uploader.js`, `modules/session-store.js` (new),
`modules/idb-store.js` (new), `manifest.json`, `src/popup/api.js`, `src/popup/app.jsx`,
`src/popup/components/HomeView.jsx`, `src/popup/components/SettingsView.jsx`, `dist/popup.js` (built),
`tests/test_{mv3_listeners,upload_timeout,s0_upload_retry_and_caps,s1_session_persistence,s2_durable_outbox}.mjs` (new).

## Test / verify
```
cd chrome-extension
for t in mv3_listeners upload_timeout s0_upload_retry_and_caps s1_session_persistence \
         s2_durable_outbox t007_batch_uploader_payload t027_scope_and_dedupe \
         t028_export_payload asset_classifier; do node tests/test_$t.mjs; done
node build.mjs                 # rebuild the popup after src/popup/** edits
```
All 9 suites pass; the service-worker module graph compiles under esbuild. Load unpacked in Chrome
to exercise the live capture → analyze flow against the workspace.

## Minimalist cleanup pass (follow-up)
Removed ~1,000+ lines of dead weight and cut settings the backend doesn't back:
- Deleted `enhanced-analyzer.js` (dead) and the entire rep+ integration (`rep-plus-bridge.js` + wiring + test).
- Removed the **API Key** field (backend enforces no auth), the **SCAN TYPE** panel (3 of 6 toggles
  were silently dropped by the analyze endpoint's normalizer — including jsluice; analysis depth is
  configured in the workspace), and inert settings (`autoStart`, `useLocalApi`, `exportIncludeContent`,
  `allowSourceMapFallback`, `authContextDomains`).
- Fixed: **Workspace URL is now the single source of truth** — uploads + health/analyze all derive
  from it (before, uploads always went to `localhost:3000` regardless).
- Modularized `background.js` **1372 → 1003 lines**: extracted `modules/auth-context.js`
  (`AuthContextTracker`) and `modules/workspace-client.js` (`WorkspaceClient`).
- Popup 46.9 → 42.6 kb; settings now show only Connection / Capture Rules / Noise Denylist.

## Deliberately deferred (debt)
- `background.js` is still 1003 lines — the message router + `processFile` could extract further.
- Optional: a workspace-SPA "Analyze" button (the popup already triggers analysis).
- Sourcemap reconstruction still runs synchronously at upload for sourcemap-bearing files (the
  uploaded sourcemap content is ephemeral, so deferring it risks losing it). Turn off "capture
  source maps" for maximum bulk-capture speed.
- Legacy removed-setting keys linger unread in `chrome.storage.local` (no migration — harmless).
