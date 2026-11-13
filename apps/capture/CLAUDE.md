# apps/capture — agent guide

This directory is a single **Chrome MV3 extension** ("JS Security Extractor Pro", under
`chrome-extension/`). It captures runtime, post-authentication JavaScript and pushes it to the
recon **platform** (`apps/platform/`) for analysis. It is a pure capture client — no backend, no
database live here anymore (the standalone v1 backend/SPA were removed in the platform
consolidation).

## Where truth lives

| Need | Read |
|------|------|
| What this extension is + how to run it | [`README.md`](README.md) |
| System architecture + the ingest contract | repo-root [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) |
| Why the v1 backend/UI were dropped, and other decisions | repo-root `docs/adr/` |
| Repo engineering standards | repo-root [`CLAUDE.md`](../../CLAUDE.md) |

## Build & test

```bash
cd chrome-extension
npm ci && npm run build              # bundles src/popup/** via esbuild (build.mjs)
for t in tests/test_*.mjs; do node "$t"; done   # run the Node test suites (no npm test target)
```

`background.js`, `content-script.js`, and `modules/*` load unbundled; only `src/popup/**` is built.

## Load-bearing invariants (don't weaken)

The MV3 durability model (see `README.md`) exists because the service worker is torn down
aggressively. Preserve these when editing `background.js` / `modules/*`:

- persist state **before** the network attempt; the IndexedDB outbox and dedup set must survive a
  respawn (keyed `sessionId:contentHash`);
- keep uploads idempotent — the backend dedupes on `(session_id, content_hash)`;
- keep the per-file cap aligned with the backend (10 MB) and register MV3 listeners synchronously.
- popup<->worker replies have ONE shape, `{ ok, error?, ...detail }`, built only by
  `modules/message-reply.js` (`ok` derived from the handler's own verdict, written last); the
  popup reads `ok`, never `success`. The worker owns deadlines (every `workspace-client` timer
  covers the body read; `stopCapture` answers before its flush); the popup's single ~60 s
  safety net means "unknown outcome -> re-sync", not failure. `tests/test_popup_reply_contract.mjs`
  pins it, including that every popup action has a worker handler.
- the **worker owns the capture** (`modules/capture-lifecycle.js`; one capture = one platform run,
  Start → End). The popup only reads `getStatus().capture` / `lastEnded` and asks for transitions
  (`captureStart`, `capturePause`, `captureResume`, `captureEnd`, `captureAddScope`, …); every
  scope change goes through a worker action, never a settings write. The popup's screens are pure
  functions of that status (`src/popup/viewmodels/captureVm.js`), and it never shows a run ID.

## Conventions

Match the existing style; prefer small, verifiable changes over broad rewrites. The popup is Preact
+ esbuild. It is a **funnel**: signed-out → a first-class sign-in **gate** (`LoginView`, workspace
URL + credentials); signed-in → **Home**, which is scope-first: no scope and a disabled Start, then
"Capture into" (Continue a session that holds the scope, or a New session with an optional
engagement), then the live capture card (Pause / Resume / End). Settings keep the signed-in
**Account** summary, Connection, Capture Rules (incl. "Capture every tab" behind its confirm), and
Noise Denylist. There is no scope setting: the scope belongs to one capture. The workspace URL is the single
source of truth for uploads, health, and analyze.

Two load-bearing rules the funnel added (don't regress): the sign-in gate must key off REAL
worker-provided settings (never a fallback), so a slow cold worker shows "Loading…" not a wrong
screen; and a **tenant change** (logout, or login as a different tenant) must reset the capture
session — rotate the id, drop the engagement binding, and drop the outbox — so one tenant's captures
can never stamp/flush under another's token. Switching engagement rotates the session because the
platform binds a session to one engagement immutably.
