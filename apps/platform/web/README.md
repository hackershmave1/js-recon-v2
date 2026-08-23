# Recon Workspace (web)

The React SPA front-end for the JS API-recon platform — a thin UI over the platform API. The whole
app sits behind central login: an `AuthGate` renders a `LoginScreen` until you sign in, then the
token rides as `Authorization: Bearer` on every API/SSE call (see the auth section in
[`../../../docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md)).

Key surfaces (a cross-run **Sessions** route, plus per-run pages under `/runs/:id`):

- **Sessions / runs** — list sessions, start a new run, follow live progress (SSE + ETag/304 fallback).
- **Overview** — run summary with host/finding metric cards.
- **Findings** — the reconstructed API surface (endpoints, params) + secret findings, with
  attributed/unattributed coverage and cross-run sightings.
- **API Spec** — the tag-grouped operation list; export the reconstructed OpenAPI (JSON/YAML).
- **Probe** — inspect and issue single requests against a resolved endpoint.
- **Tech stack** — per-host technology detection.
- **Hosts** — every host the run discovered, badged in/out of scope (+ a suspected-backend column).
- **Sources viewer** — read the analyzed JS (fetched chunks + source-map-recovered originals) with
  syntax highlighting; jump to the source line behind a finding.
- **Threat Model** — marked **SOON** (not built yet).

## Stack

React 19 + Vite, routing via react-router. See `package.json` for exact versions.

## Develop

```bash
npm ci            # install (reproducible)
npm run dev       # Vite dev server with HMR
npm run lint      # oxlint + tsc (type-check)
npm test          # vitest (colocated *.test.tsx)
npm run build     # tsc -b && vite build
```

## See also

- [`../README.md`](../README.md) — the platform backend (FastAPI + worker).
- [`../../../docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md) — system architecture.
- [`../../../docs/OPERATING.md`](../../../docs/OPERATING.md) — stand-up + how to read the output.
