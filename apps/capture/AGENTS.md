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

## Conventions

Match the existing style; prefer small, verifiable changes over broad rewrites. The popup is Preact
+ esbuild. Settings are intentionally minimal (Connection / Capture Rules / Noise Denylist) — the
workspace URL is the single source of truth for uploads, health, and analyze.
