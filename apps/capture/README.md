# RECON Capture — MV3 extension

The `apps/capture/` surface is a single **Chrome MV3 extension** ("JS Security Extractor Pro").
It captures the JavaScript a browser actually loads **behind authentication** — including
lazy-loaded chunks and their source maps — and pushes it to the recon **platform**
(`apps/platform/`) for the same static analysis a crawl would get. It is a pure client: it
captures and uploads; all reconstruction, secret scanning, and OpenAPI export happen on the
platform.

> This directory used to be a full standalone app (its own FastAPI backend + SPA). Those were
> removed in the platform consolidation — the extension is the one capability that survived. See
> the repo-root [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) ("the surviving v1 client")
> for the extension ↔ platform ingest contract, and the ADR trail for why the v1 backend/UI were
> dropped.

## Quick start

1. Bring up the platform (it serves the ingest API on `:8000`):
   ```bash
   cd apps/platform && docker compose up -d --build
   ```
2. Build the popup (Preact, bundled by esbuild):
   ```bash
   cd apps/capture/chrome-extension && npm ci && npm run build
   ```
   `background.js`, `content-script.js`, and `modules/*` load unbundled; only `src/popup/**` needs
   the build step (`npm run watch` rebuilds on change).
3. Load it: open `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and
   select `apps/capture/chrome-extension`.
4. The default backend is `http://localhost:8000` and capture ingest is on by default, so captured
   JS flows straight into the platform's run → analyze pipeline. Point the popup's **Connection**
   setting at a different workspace URL if needed — it is the single source of truth for uploads,
   health, and analyze.

## How it works

Browse an authenticated app with capture on → the service worker intercepts script responses
(`webRequest` + a `document_start` content script), hashes and de-dupes them, fetches any
`//# sourceMappingURL` maps, and batches everything to `POST /api/save-files`. Analysis is
decoupled: bulk uploads are a fast store, and the popup's **Analyze** action then triggers the
platform's async job (`POST /api/sessions/{id}/analyze/start`) and polls progress.

## MV3 durability model

A Manifest V3 service worker is torn down aggressively, so the pipeline is built to lose nothing
across a respawn. These invariants are load-bearing — don't weaken them for convenience:

- **Stable session id** — `modules/session-store.js` persists `reconSessionId` in
  `chrome.storage.local` and restores it on init, rotating only on an explicit *New Session*. A
  respawn must not mint a new id, or one browse fragments into many backend sessions.
- **Durable upload outbox** — `modules/idb-store.js` (IndexedDB) backs the uploader. Files are
  persisted **before** the network attempt (keyed `sessionId:contentHash`) and forgotten only once
  the server accepts (or permanently rejects) them; the outbox is rehydrated and drained on boot.
  Re-upload is safe because the backend dedupes on `(session_id, content_hash)`.
- **Durable dedup set** — captured hashes persist to IndexedDB and rehydrate on boot, so a respawn
  doesn't re-fetch / re-hash / re-upload already-captured files.
- **Cold-respawn flush net** — a `chrome.alarms` alarm (`flushOutbox`, 1 min) drains the outbox
  after a cold respawn, armed only while unsent files exist; a 5 s in-memory timer handles the hot
  path during active capture.
- **Correctness caps** — per-file cap is **10 MB** (aligned to the backend, which rejects larger);
  non-retriable `4xx` (except `429`) is dropped, not retried forever; `upload()` carries a 30 s
  `AbortController` (Chrome has no default) so a blackholed workspace can't hang the pipeline; MV3
  listeners are registered synchronously at bootstrap so the event that woke the worker isn't lost.

## Layout

```
chrome-extension/
├── manifest.json          MV3 manifest (service worker + content script)
├── background.js          service worker — capture, hashing, outbox orchestration
├── content-script.js      document_start injector (all frames)
├── modules/               session-store, idb-store, batch-uploader, content-fetcher,
│                          sourcemap-detector, auth-context, workspace-client, …
├── src/popup/             Preact popup (RECON Capture UI) — built by build.mjs
├── tests/                 Node test suites (*.mjs)
└── build.mjs              esbuild bundler for the popup
```

## Tests

The extension suites are plain Node scripts (no `npm test` target). Run them per file:

```bash
cd apps/capture/chrome-extension
node tests/test_s2_durable_outbox.mjs      # one suite
for t in tests/test_*.mjs; do node "$t"; done   # all suites (bash/zsh)
```

They cover the durability model above (session persistence, outbox, retry/caps, timeout), scope +
dedupe, the upload/export payload shapes, project config, and the workspace client.
