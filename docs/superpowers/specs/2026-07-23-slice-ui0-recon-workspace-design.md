# Slice UI-0 — Recon Workspace core loop (design)

> First UI vertical slice. A thin React + Vite front-end over the **already-done**
> backend, realizing the Recon Workspace core loop (Security Engineer Journey steps
> **05 analyze → 06 orient → 07 triage**, including just-in-time secret reveal). No new
> backend REQ is discharged; this surfaces existing ones (REQ-A1 enqueue, REQ-R2 SSE /
> REQ-R4 status, REQ-D2/D3 findings, REQ-C2 coverage, REQ-P1 triage, REQ-S2 reveal).

## 1. Context

Slices 1–3 built the backend only (data → service → API), stopping at tested endpoints.
On 2026-07-23 the user resolved the working mode: **Mode A — every vertical slice now
runs through to a UI and ends with a visual walkthrough** (global CLAUDE.md §2), and
chose **React + Vite** as the stack. This slice back-fills the UI for the done backend
so the core recon loop is usable end-to-end, and establishes the front-end foundation
every later slice builds on.

The backend today exposes 11 routes; auth is a single `X-Tenant-Id` header (UUID),
validated in `src/recon/api/deps.py` (401 missing / 400 malformed). There is **no**
per-user auth, no "list tenants" and no "list runs" endpoint. Starting a run requires an
**authorized session** first: `POST /sessions` needs ≥1 `scope_hosts` entry and a
non-empty `authorized_by`; supplying both sets `authorization_ack=true`
(`src/recon/sessions/service.py`). `POST /runs/upload` (multipart JS + optional map)
rejects an unauthorized session with 403. This authorization gate is a deliberate safety
control for an offensive-recon tool and MUST be honored by the UI, not bypassed.

## 2. Settled decisions (do not re-litigate)

| # | Decision | Choice |
|---|---|---|
| D1 | Working mode | Mode A — UI in every slice; finish with a visual walkthrough |
| D2 | Front-end stack | React + Vite + TypeScript |
| D3 | Slice scope | Full loop: upload → watch live → orient → triage → reveal |
| D4 | Live progress | **fetch-based SSE** consuming `GET /runs/{id}/events`; keeps the single `X-Tenant-Id` header-auth model; **zero backend auth change** |
| D5 | Serving | **One origin.** Dev: Vite dev server proxies API to FastAPI :8000. Prod: `npm run build` → `web/dist` mounted by FastAPI `StaticFiles`. No CORS, ever |
| D6 | Routing | `react-router-dom`, two routes (`/`, `/runs/:id`) |
| D7 | SSE reader | Hand-rolled ~30-line fetch + `ReadableStream` parser (YAGNI — no `@microsoft/fetch-event-source` dependency; we control both ends of the frame format) |
| D8 | Styling | Plain CSS Modules, dark security-tool baseline, no UI kit |

The rejected live-progress alternatives are recorded so they stay closed: **polling
`/status`** (simplest, but near-live only and no per-step feed) and **native
`EventSource` + backend auth change** (rejected — the browser `EventSource` API cannot
attach custom headers, so it would force tenant auth into the URL/query or a cookie,
widening the auth surface for the whole app to satisfy one screen).

## 3. Architecture & module layout

New `web/` directory at repo root, sibling to `src/`. Feature-grouped (§9), each module
one responsibility (§10).

```
js-extractor-v2/
├─ src/recon/api/app.py        # + serve SPA (assets mount + catch-all) after routers
├─ src/recon/api/sessions_router.py  # + map unknown-tenant FK error → 400 (not 500)
├─ web/                        # NEW — Vite + React + TS
│  ├─ index.html
│  ├─ package.json
│  ├─ package-lock.json         # committed — `npm ci` in CI requires it
│  ├─ vite.config.ts           # server.proxy: /sessions /runs /healthz → http://localhost:8000
│  ├─ tsconfig.json
│  └─ src/
│     ├─ main.tsx, app.tsx      # router: "/" → NewRunPanel · "/runs/:id" → workspace
│     ├─ tenant/
│     │  ├─ TenantContext.tsx    # X-Tenant-Id (UUID) held + persisted to localStorage
│     │  └─ TenantGate.tsx       # blocks the app until a valid tenant UUID is set
│     ├─ api/
│     │  ├─ apiClient.ts         # fetch wrapper; injects badge; throws ApiError{status,detail}
│     │  ├─ sseClient.ts         # fetch-based SSE reader (badge-aware, Last-Event-ID)
│     │  └─ types.ts             # Finding, Coverage, RunStatus, RunEvent, SessionView
│     └─ features/
│        ├─ newRun/NewRunPanel.tsx
│        ├─ progress/RunProgress.tsx
│        └─ findings/{FindingsView,FindingDetail,TriageControls,RevealButton}.tsx
├─ Dockerfile                  # + node build stage → COPY web/dist into the Python image
└─ .github/workflows/ci.yml    # + `frontend` job (node): npm ci → lint → test → build
```

**Backend edits** (`create_app()` in `src/recon/api/app.py`) — corrected by the
adversarial gate (§9/HIGH-1, MED-3):

1. **Serve the SPA** after all `include_router(...)` calls:
   - Mount hashed assets: `app.mount("/assets", StaticFiles(directory=<dist>/assets))`
     (Vite fingerprints files under `/assets`).
   - Register **one** catch-all `@app.get("/{full_path:path}")` **last** that returns
     `index.html` **only** for `GET` paths not under the API prefixes
     `{/runs, /sessions, /healthz}`; any unknown path under those prefixes **re-raises a
     JSON 404** so the API contract (JSON errors) is preserved. This is the SPA deep-link
     fallback — `StaticFiles(html=True)` alone does **not** do it (it 404s, never serves
     `index.html`), which is why a bare mount would break a hard refresh on `/runs/:id`.
   - The whole block is guarded on `<dist>` existing (`os.path.isdir`) → a **no-op when
     absent** (unit tests / dev without a build). The guard is **required**: `StaticFiles`
     defaults to `check_dir=True` and raises at construction if the directory is missing.
   - `<dist>` is an **absolute** path from a known root/setting, never CWD-relative
     `"web/dist"` (which only resolves from the repo root).
2. **Unknown-tenant mapping** (`sessions_router.create_session`): catch the FK
   `IntegrityError` from a well-formed-but-unprovisioned tenant UUID and return a clean
   **400** ("unknown tenant"), instead of the current raw 500 (it only catches
   `AuthorizationRequired`). Small, additive.

**Docker:** multi-stage — a `node:20` stage runs `npm ci && npm run build`; the Python
image `COPY`s `web/dist`. The existing `uvicorn recon.api.app:app` command serves both
API and UI unchanged.

**CI:** a third job `frontend` (Node, no infra): `npm ci → npm run lint → npm run test
(vitest, non-watch) → npm run build`. Independent of the two Python lanes.

## 4. Runtime data flow (the 05→07 loop)

1. **Set tenant** — user pastes tenant UUID; validated, stored in `localStorage`;
   attached as `X-Tenant-Id` on every request. `TenantGate` blocks until set.
2. **Create session** — `POST /sessions` with `scope_hosts` (≥1) + `authorized_by`
   (this pairing *is* the authorization acknowledgment) → `session_id`.
3. **Upload** — `POST /runs/upload` (multipart JS file + optional source map + the
   `session_id`) → `202 {run_id, state}`; navigate to `/runs/:id`.
4. **Watch** — fetch-based SSE on `GET /runs/{id}/events` (badge attached, `Last-Event-ID`
   tracked); render the event feed + current `state/stage/pct`. On a terminal
   `run.transition` event, stop the stream.
5. **Orient** — `GET /runs/{id}/findings` → findings (grouped by type/severity) + the
   honest coverage panel (REQ-C2; `coverage` may be null until analyze completes).
6. **Triage / reveal** — `POST /runs/{id}/findings/{hash}/triage` (status + note);
   `POST /runs/{id}/findings/{hash}/reveal` for a `revealable` secret (shown once).

## 5. Auth & the SSE/header resolution

The browser native `EventSource` cannot set request headers, colliding with header-only
tenant auth. Resolution (D4): a hand-rolled reader issues a normal `fetch()` (which *can*
set `X-Tenant-Id`) to `/runs/{id}/events`, then reads `response.body` as a stream and
parses SSE frames (`id:` / `event:` / `data:` / `: keep-alive`). It records the last
event id and, on network drop, reconnects with the `Last-Event-ID` header; after 3
consecutive failures it falls back to polling `GET /runs/{id}/status` (ETag). Same-origin
serving (D5) means no CORS and no preflight in either dev or prod.

**SSE lifecycle contract (corrected by the gate — §9/MED-1, MED-2):** SSE *advances*
progress; it is **not** a complete event history.
- The client treats a `run.transition` whose `to` is in the **full terminal set**
  `{done, partial, failed, cancelled}` as authoritative: on it, **stop and do not
  reconnect**. (Stopping on any transition would stop at `queued→discovering`.)
- A **clean** end-of-stream is not a drop: the client fetches `GET /runs/{id}/status`; if
  terminal, it stops. Only a genuine network error triggers reconnect (3 tries → poll
  fallback). This avoids the busy-reconnect loop against an already-terminal run.
- Because Redis replay is capped and gap-replay is deferred (the `_event_stream` NOTE in
  `runs_router.py`), the client fetches `GET /runs/{id}/findings` **+** `/status` on
  **stream-open and on every clean close** — so a client attaching after a
  trimmed/terminal run still reaches findings even if it never sees the terminal event.

## 6. Component responsibilities

- **`apiClient.ts`** — one `request()` core: prepends nothing (same origin), injects the
  badge, parses JSON, and on non-2xx throws `ApiError{status, detail}`. Typed methods:
  `createSession`, `uploadRun`, `getStatus`, `getFindings`, `triageFinding`,
  `revealSecret`. No React here.
- **`sseClient.ts`** — `streamRunEvents(runId, {tenantId, onEvent, onStatusFallback,
  signal})`; pure async, testable without React; owns reconnect + fallback logic.
- **`TenantContext` / `TenantGate`** — the only place the badge lives; a settings control
  lets the user change/clear it.
- **`NewRunPanel`** — form state + validation (submit disabled until ≥1 scope host and a
  non-empty `authorized_by` and a chosen JS file); orchestrates `createSession` then
  `uploadRun`.
- **`RunProgress`** — subscribes to `sseClient`, renders feed + progress; on terminal,
  surfaces `FindingsView`.
- **`FindingsView` / `FindingDetail` / `TriageControls` / `RevealButton`** — read-model
  rendering, triage write, and one-shot reveal respectively. Secret `evidence` arrives
  already redacted from the server; `RevealButton` shows only for `revealable` findings.

## 7. Error-handling model (§5 — no silent failures)

| Code | Origin | UI behavior |
|---|---|---|
| 401 / 400 | tenant missing / not a UUID | Route to Tenant gate; inline "must be a UUID" |
| 400 | `POST /sessions` (no scope host / no `authorized_by`) | Form validation blocks submit; message per field |
| 403 | session not authorized | Explain the authorization gate (should not fire — we create the session acked) |
| 413 / 400 | upload too large / empty | Inline file error |
| 404 | run / session not found for tenant | "Run not found for this tenant" |
| 400 | `POST /sessions` unknown tenant (after backend maps the FK error, §3) | "Unknown tenant — check the ID or provision it via the bootstrap CLI" |
| reveal 409 | integrity mismatch | "The stored secret no longer matches (source changed)" |
| reveal 410 | blob purged / gone | "Evidence has been purged — cannot reveal" |
| reveal 422 | finding has no stored offsets | "No stored location for this secret" |
| reveal 500 | reveal infra error (audited server-side) | "Reveal failed — try again" (attempt is audited value-free) |
| SSE network drop | stream | Reconnect w/ `Last-Event-ID`; after 3 fails → status polling |

## 8. Security invariants

- The tenant badge is the only credential; it lives in `localStorage` + the
  `TenantContext`, never logged, never placed in a URL/query string.
- Secret values are never rendered by default: the findings read redacts secret
  `evidence` server-side; the client shows a reveal affordance only when `revealable`,
  and the revealed value is shown once in the UI (button disabled after) as a
  **client-side affordance**. NOTE (corrected by the gate — §9/LOW-3): the backend reveal
  is **repeatable** and audits every attempt value-free; it is *not* one-shot server-side.
- The authorization gate (scope hosts + `authorized_by`) is enforced by the UI form; the
  UI never fabricates authorization or hits `POST /runs*` for an unauthorized session.
- Same-origin serving keeps the badge out of cross-origin exposure and needs no CORS
  relaxation.

## 9. Adversarial design-review gate (§4, gate 1) — findings folded in

Ran 2026-07-23. **Verdict: sound with required fixes.** All fixes below are folded into
the sections cited. Proof was required for every objection (docs or exact repo lines).

**Confirmed safe (attacks that failed, with proof):**
- No secret reaches the DOM/logs before reveal: secret `finding.value` is
  `provider:sha256(token)` (`normalize.py:281`), analyze writes no secret `evidence`
  (`analyze.py:230`), the read model nulls it (`queries.py:159`), and the only analyze SSE
  payload is count-only coverage (`analyze.py:121`). Plaintext only via `/reveal`.
- The upload→findings loop works: an uploaded file sets `input_ref`, so FETCHING no-ops
  (`fetch.py:141`) and the run reaches ANALYZING→findings (`coordinator.py:106`).
- Mounting after `include_router` keeps API routes winning (Starlette first-match); the
  Vite dev proxy pipes `text/event-stream` (docs: proxied requests are not transformed).

**Required fixes (folded):**
- **HIGH-1 (§3):** `StaticFiles(html=True)` + "SPA fallback mounted last" does not compose —
  `StaticFiles` 404s (never serves `index.html`), and `/runs` is shared by API + client
  routes. → assets under `/assets` + one catch-all `GET` last returning `index.html` only
  for non-API paths, re-raising JSON 404 under API prefixes; `isdir` guard is mandatory
  (StaticFiles `check_dir=True` raises if missing). Proof: `starlette/staticfiles.py:147-152`.
- **MED-1 (§5):** terminal detection must use the full terminal set and stop-without-reconnect,
  else it stops at the first transition or busy-loops. Proof: `runs_router.py:171,191-194`.
- **MED-2 (§5):** `Last-Event-ID` replay is not gap-free (capped Redis + deferred gap-replay,
  `runs_router.py:161-164`); a client attaching post-trim never sees terminal → fetch
  findings/status on open + clean close.
- **MED-3 (§3, §7):** a valid-format but unprovisioned tenant UUID → raw 500 (FK
  `IntegrityError`; only `AuthorizationRequired` caught, `sessions_router.py:29-37`). →
  map to 400 + first-run guidance.
- **LOW-1 (§3):** commit `package-lock.json` or `npm ci` fails. **LOW-2 (§3):** anchor the
  dist dir absolute (not CWD-relative). **LOW-3 (§8):** backend reveal is repeatable +
  audited, not one-shot (`reveal.py:54-83`) — wording corrected. **LOW-4 (§10):** SSE test
  must split mid-line.

## 10. Testing (colocated, §11) & gates (§4)

Vitest + React Testing Library; `*.test.tsx`/`*.test.ts` beside each source; `fetch`
mocked (MSW or a thin stub). Coverage:

- `apiClient.test.ts` — badge injection; non-2xx → `ApiError` with status+detail.
- `sseClient.test.ts` — multi-frame parse including a split **mid-line** (bytes split
  inside a field, e.g. `da`|`ta: {…}`), not only at `\n\n`; `: keep-alive` ignored;
  `Last-Event-ID` advanced; terminal (full terminal set) stops with no reconnect; clean
  close → status check; fallback triggers after 3 network errors.
- `NewRunPanel.test.tsx` — submit gated on ack fields + file; calls `createSession` then
  `uploadRun` in order.
- `FindingsView.test.tsx` — coverage rendered; findings grouped; secret evidence redacted;
  reveal affordance only when `revealable`.
- `RevealButton.test.tsx` — one-shot reveal; 409/410/422 → distinct messages.

Gates: **adversarial design review** (§9) before the plan; **higher-model code review**
of the diff after build. **Visual walkthrough** (§2) against the real backend
(`docker compose up`): set tenant → create session → upload a real JS file → watch it
stream → orient on findings+coverage → triage → reveal — driven page-by-page with the
preview tools. Green unit tests are **not** "done" for this UI slice.

## 11. Out of scope (deferred to their owning slices)

Steps 08–12 (source viewer, OpenAPI export, replay/fuzz UI, threat model, report/diff);
pause/cancel/resume controls (the API exists — a cheap follow-up, left out to keep this
thin); run history/list (no list endpoint exists); real auth/login; multi-file/crawl
ingestion; styling polish beyond a clean dark baseline.

## 12. Open questions (confirm during build, do not block design)

- Exact triage **status vocabulary** and any note constraints — read from
  `src/recon/api/probe_router.py` / the triage service before wiring `TriageControls`.
- Whether `RunProgress` should auto-navigate to findings on terminal or reveal them
  inline on the same route (UX detail; default: inline on `/runs/:id`).
- Node version pin for CI (default: 20 LTS).
- **Verify in the §10 walkthrough (gate flagged as unverified):** that the installed Vite
  http-proxy does not *buffer* `text/event-stream` in dev (transform-free ≠ buffer-free);
  and that client UUID validation is no stricter than the server's `uuid.UUID()` (which
  accepts un-hyphenated/braced/urn forms) so valid ids aren't rejected client-side.
