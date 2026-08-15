# Recon Workspace (web)

The React SPA front-end for the JS API-recon platform. It's a thin UI over the
platform API: browse capture/crawl **sessions** and their **runs**, watch run
**progress**, and review reconstructed **findings** (endpoints, params, secrets).

Key surfaces:

- **Sessions / runs** — list sessions, start a new run, follow live progress.
- **Sources viewer** — read the analyzed JS with syntax highlighting and jump to
  the source line behind a finding.
- **Findings** — the reconstructed API surface and secret findings for a run.
- **Manual probe** — inspect and issue single requests against a resolved endpoint.
- **OpenAPI export** — download the run's reconstructed OpenAPI spec.

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
