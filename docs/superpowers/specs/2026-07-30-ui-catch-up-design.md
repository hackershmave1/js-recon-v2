# UI catch-up — make the SPA cover the shipped backend (design)

- **Date:** 2026-07-30
- **Status:** approved (brainstorming). **§4 adversarial design gate PASSED (2026-07-30): BUILD WITH CHANGES**
  — four FE-wiring folds recorded in §9 and applied in §3/§5/§6. Higher-model per-unit code review owed during
  the build.
- **Slice:** a **decomposed** front-end catch-up. Three headless backend routes get a UI, and every
  already-built-but-never-walked surface gets its first live in-container walkthrough. Four sequenced units,
  each a thin vertical slice (apiClient fn + component + colocated Vitest + ~2-min rebuild + live walkthrough).
- **Primary REQs:** REQ-P1 (manual-probe handoff — copy-as-request / curl / raw-HTTP), the OpenAPI export
  SHOULD (`GET /export/openapi`, shipped `b66cfba`), REQ-A4 (pause/cancel/resume run controls).
- **Design references:** `Javascript recon app redesign/Recon Workspace.dc.html` ("Export spec" button `:430`,
  finding drawer + copy affordance `:772-773`), `Developer Requirements.dc.html:381` (REQ-P1 wording),
  `Security Engineer Journey.dc.html` (journeys 06/09). Fidelity = **thin current CSS, mockup-informed layout**.

## 1. Context

The React SPA (`web/`, "UI-0" + slices X/Y/REQ-C2) covers 13 of the 18 backend routes. Auditing the live
`openapi.json` against `web/src/api/apiClient.ts` found **three route clusters with no UI**, plus a backlog of
surfaces that were built and Vitest-green but never driven live in a browser:

- **Missing FE (3 clusters):** the manual-probe handoff (`GET /runs/{id}/requests` → curl + raw-HTTP,
  REQ-P1); the OpenAPI export download (`GET /runs/{id}/export/openapi`); run controls
  (`POST /runs/{id}/pause|cancel|resume`, REQ-A4).
- **Walkthrough debt (built, never walked live):** crawl/`AssetsInventory` (Slice X), multi-asset (Slice Y),
  `SpecUpload` (shadow spec-attach), `BaseUrlPanel` + `matched_operation` (REQ-C2). All four are already
  mounted (`FindingsView.tsx` renders `SpecUpload` + `BaseUrlPanel`; `RunWorkspace` renders `AssetsInventory`).

**Key operational finding (corrects every prior handoff):** the "perennial multi-hour bake" is a **cold-cache
artifact**, not a real cost. With a warm BuildKit cache, `docker compose build` re-runs only the changed
layers — `COPY web/` → `npm run build` (~4s) for a FE change, `COPY src` → `pip install .` (~30s) for a
backend change — while the expensive Go (katana/sourcemapper), apt-chromium, and `npm ci` stages stay cached.
A full rebuild measured **~1–2 minutes** on this host (2026-07-30). The dev loop is therefore: build the FE →
`docker compose build && docker compose up -d` (~2 min) → walk it in the container. The api/worker now run the
current-`main` image (`de95ab8e0566`), serving `/export/openapi`, `/requests`, and `/base-url`.

**Binding platform constraints (unchanged):** the SPA is served **baked** into the api image
(`RECON_SPA_DIST_DIR=/app/web/dist`, mounted via `app.py::_mount_spa`, SPA fallback registered last so real
API routes match first). Auth to the API is the `X-Tenant-Id` header only (the UI supplies it via
`TenantContext`). No new backend, no new egress — this slice is pure front-end over already-shipped routes.

## 2. Settled decisions (user-approved in brainstorming)

- **Decompose** into per-surface units, not one mega-slice (CLAUDE.md §10; each unit independently reviewable).
- **All three missing clusters are in scope** (export + manual-probe + run controls) — the user chose the most
  inclusive scope; run controls are **not** deferred.
- **Fidelity = thin current CSS + mockup-informed layout.** Render in the app's existing dark stylesheet
  (`web/src/styles.css`: `--bg:#0d1117`, `card`/`chip`), but take field/affordance layout from the mockups
  (method chips, copy buttons, params, a lime "Export spec" action). A full restyle toward the rich
  mockup (lime/`Space Grotesk` drawers+modals) is explicitly a **separate future "UI polish" slice**.
- **Placement = single-scroll sections** (no tabs, no drawer). The app is already one vertical scroll; new
  pieces slot in with zero layout refactor.
- **Dev-loop = full image rebuild** (user-chosen), which is ~2 min warm — not a blocker.

## 3. Architecture & components

`RunWorkspace` (`web/src/app.tsx`) stays a single vertical scroll. Additions (new = ✚):

```
RunWorkspace (single-scroll)
├── RunProgress            (existing)  ✚ RunControls        — pause / cancel / resume, state-gated
│                                      ✚ lifts run state    — onState/onTerminal (see below)
├── AssetsInventory        (existing)
├── ✚ ExportSpecButton                 — "Export spec ↓" (json|yaml), run-level toolbar row
├── FindingsView           (existing)  — SpecUpload · BaseUrlPanel · FindingDetail
└── ✚ ProbePanel                       — reconstructed requests, curl + raw-HTTP, copy
```

New files (each with a colocated `.test.tsx`):

- `web/src/features/progress/RunControls.tsx` — rendered inside/below `RunProgress` (which already holds run
  state + the SSE stream).
- `web/src/features/export/ExportSpecButton.tsx` — a small toolbar button, mounted in `RunWorkspace`.
- `web/src/features/probe/ProbePanel.tsx` — a new section, mounted in `RunWorkspace` after `FindingsView`.

`web/src/api/apiClient.ts` — four new functions. `web/src/api/types.ts` — new response types.

**Shared change to `RunProgress` (§4 gate folds F1/F2/F3).** Today `RunProgress` lifts only `onFindings`
(`RunProgress.tsx:7,25`) and sets run `state` **solely** in `refresh()`/`checkTerminal` — SSE events are
*appended*, not applied to `state` (`RunProgress.tsx:34`). This slice adds an `onState`/`onTerminal` lift so
`RunWorkspace` can gate `ProbePanel` (and the export enable) on a **terminal** state. `RunControls` sits inside
`RunProgress` so it has `state` directly, **but** must refresh it from each control POST's response — not from
SSE, which never moves a non-terminal `state`.

## 4. Backend contract (confirmed against source; §4 gate verified accurate)

- **Export** — `GET /runs/{id}/export/openapi?format=json|yaml`. Returns raw bytes with
  `Content-Disposition: attachment; filename="openapi-{id}.{fmt}"` and media type `application/json` /
  `application/yaml`. `422` bad format, `404` run not found, `500` invalid document. (`export_router.py`.)
- **Requests** — `GET /runs/{id}/requests` → `{ run_id, count, requests: [ {operation, method, path,
  hosts[], query_params:[{name,example}], body_params[], content_type, example_url, probeable,
  endpoint_hashes[], artifacts:{curl,http} | null} ] }`. `artifacts` is `null` when `probeable` is false.
  `404` run not found. (`probe_router.py::_request_dict`.)
- **Controls** — `POST /runs/{id}/pause` → `{run_id, state, pause_requested}`; `/cancel` →
  `{run_id, state, cancel_requested}`; `/resume` → `{run_id, state}`. All routed through `_guard`, which maps
  an invalid-state transition to a 409 `TransitionConflict`; the FE already surfaces `ApiError(status, detail)`
  for any non-2xx, so the UI is status-code-agnostic and shows the server's `detail`. No UI-triggerable path
  reaches the uncaught `sm.InvalidTransition` (§9). (`runs_router.py:231-268`.)

## 5. Per-unit design

### Unit A — ExportSpecButton (smallest; ship first)

- **apiClient:** `exportOpenApi(tenantId, runId, format: "json"|"yaml"): Promise<Blob>` — a **blob variant**
  (not the JSON `request<T>` helper, which forces `Accept: application/json` + `res.json()`,
  `apiClient.ts:14-27`): `fetch` with `X-Tenant-Id`, on `!ok` throw `ApiError`, else `res.blob()`. A bare
  `<a href>` cannot carry the tenant header, so download must go through `fetch`→blob.
- **Component:** a button labelled "Export spec" + a json/yaml selector (default json). Enabled once the run is
  terminal via the shared `RunProgress` state lift (§3), else **always-enabled** (a zero-op run still exports a
  valid empty-paths document, so the enable-gate is a nicety, not a correctness requirement — F3). On click:
  blob → `URL.createObjectURL` → temporary `<a download="openapi-…">` → click → revoke. Disable while in-flight.
- **Errors/empty:** `404`/`500` → inline "Couldn't export spec: {detail}".
- **Fidelity:** lime accent + download glyph (mockup `Recon Workspace:430`), rendered with existing button CSS.
- **Tests:** blob download path (mock `fetch`/`createObjectURL`/anchor click — see §6 jsdom stubs), format
  toggle, error path.

### Unit B — ProbePanel (manual-probe handoff, REQ-P1)

- **apiClient:** `getRequests(tenantId, runId): Promise<RequestsResponse>`.
- **Component:** gated on a **terminal** run state via the new `RunProgress` `onState`/`onTerminal` lift (§3) —
  **not** the findings trigger, which fires on stream-open mid-run (`RunProgress.tsx:25,33`) and would fetch
  `/requests` before endpoints are reconstructed (F2). Fetch `getRequests` when the state becomes terminal. One
  card per reconstructed request: method chip + `path`, `query_params` / `body_params`, `content_type`, and
  **Copy curl** / **Copy raw-HTTP** buttons reading `artifacts.curl` / `artifacts.http` (via
  `navigator.clipboard.writeText`, with a "copied" tick).
- **Errors/empty:** non-probeable (`artifacts === null`) → render the shape with a muted "not probeable" note;
  `count === 0` → "No probeable requests reconstructed."; `404` → panel hidden.
- **Fidelity:** JetBrains-mono method chip + copy buttons echo the mockup finding-drawer copy affordance
  (`Recon Workspace:772-773`); rendered in existing CSS (reuse `.chip`, add minimal styles).
- **Tests:** render N requests, method/path/params; copy buttons call `clipboard.writeText` with the right
  artifact (see §6 stub); non-probeable branch; empty branch.

### Unit C — RunControls (REQ-A4)

- **apiClient:** `pauseRun` / `cancelRun` / `resumeRun (tenantId, runId): Promise<RunControlResult>`.
- **Component:** inside `RunProgress` (already renders `state`). Gating (robust — keys off terminal + paused,
  not an enumeration of active states): **terminal** (`TERMINAL_STATES`) → no controls; `state === "paused"`
  → **Resume** + **Cancel**; otherwise (active) → **Pause** + **Cancel**. In-flight → disable all. **Cancel is
  confirmed** (a `window.confirm` guard — it is effectively irreversible). After a successful POST, drive
  gating from the **response's `state`** (all three return `{state}`, `runs_router.py:238/248/258`) plus an
  explicit `getStatus` refetch — **do not** rely on SSE, which appends events but sets `state` only in
  `refresh()`/`checkTerminal` (`RunProgress.tsx:24,34,39`), so a non-terminal Pause/Resume would otherwise
  leave gating stale until reconnect (F1).
- **Errors:** invalid transition → `ApiError` (409) → inline "{detail}". Pause is **cooperative**
  (orchestrator-level, `pause_requested`; per the run-pause model only crawls truly checkpoint) — a fast
  single-file run may reach a terminal state before it pauses; the UI reflects whatever the next status/SSE
  says, with no client-side assertion that pause "took".
- **Tests:** gating per state (terminal/paused/active), each POST fires the right call + refetch + updates
  gating from the response `state`, disabled while in-flight, confirm-guard on cancel.

### Unit D — Debt walkthroughs (no new build)

Drive every surface live against the container (already on current `main`), capturing a screenshot per surface:
the A/B/C surfaces, plus the never-walked **crawl/`AssetsInventory`** (needs katana — container ✓),
**multi-asset** inventory, **`SpecUpload`** (attach a spec → shadow buckets), and **`BaseUrlPanel`** +
`matched_operation`. This discharges the "live in-UI walkthrough deferred" debt logged across
`docs/slice2-deferred-debt.md` (UI-0 / Slice X / Slice Y). Uses the preview/browser tools; passing Vitest is
not "done" for UI (CLAUDE.md §2).

## 6. Testing

Colocated Vitest per new component (matching the existing `*.test.tsx` pattern) + `apiClient.test.ts`
additions for the four new functions. `web/src/api/types.ts` gains: `ReconstructedRequest`, `RequestsResponse`,
`RunControlResult`. Type-check via the existing `tsc -b --noEmit`. FE suite stays green (currently 51).

**jsdom stubs (§4 gate F4):** jsdom provides neither `navigator.clipboard` nor a working
`URL.createObjectURL`, and there is no precedent in `web/` today. `navigator.clipboard` is read-only, so stub
`navigator.clipboard.writeText` via `Object.defineProperty` / `vi.stubGlobal`, and stub `URL.createObjectURL`
(+ anchor click) — in `setupTests.ts` or per-test — for the ProbePanel copy tests and the ExportSpecButton
download test.

## 7. Sequencing

`A · ExportSpecButton` → `B · ProbePanel` → `C · RunControls` → `D · debt walkthroughs`. Each of A/B/C: build
FE + Vitest → `docker compose build && up -d` (~2 min) → live walkthrough with screenshot proof → per-unit
higher-model review (CLAUDE.md §4 gate 2) → commit. B and C both touch the `RunProgress` state lift; since B
introduces it, C builds on it (order A→B→C keeps the lift landing before its second consumer). D is a
verification-only pass at the end.

## 8. Out of scope (named so they aren't forgotten)

- The mockup's full **"Export & Report" modal** (MD/JSON/CSV/HTML report, Postman collection, mitmproxy-addon
  export) — no backend today; belongs to the Slice-5 report work. This unit ships only the OpenAPI **spec**
  export, which is fully backed.
- **Sources/source-viewer**, **Threat-Model orchestration**, **Replay/fuzz** — future slices (4/5), no backend
  to catch up to.
- A **full visual restyle** toward the rich mockup fidelity — a separate "UI polish" slice.
- Run-controls status flags in `GET /status` — the status payload does not include `pause_requested`/
  `cancel_requested` (`runs_router.py:137-147`); the UI relies on `state` + the POST responses. Adding the
  flags to status is a fast-follow (§10) if the gating needs to survive a page reload mid-pause.

## 9. §4 adversarial design gate

**PASSED (2026-07-30): BUILD WITH CHANGES.** A subagent tasked to disprove the design confirmed the backend
contracts, the export-blob rationale, and — the biggest risk area — that run-controls gating is structurally
safe against the state machine: the `ALLOWED` table (`state_machine.py:23-58`) makes every UI-triggerable
transition valid (QUEUED→PAUSED/CANCELLED, PAUSED→active/CANCELLED), a cooperative flag-set
(`service.py:185-201,218-234`), or a 409 `TransitionConflict` caught by `_guard` (`runs_router.py:261-268`) —
no path reaches the uncaught `sm.InvalidTransition`, so the status-code-agnostic UI holds. `GET /status` does
omit `pause_requested`/`cancel_requested` (as §8/§10 state). Four FE-wiring folds, all applied above:

- **F1 (Unit C, important):** gate off each control POST's `state` + an explicit `getStatus` refetch, not SSE —
  `RunProgress` appends events but moves `state` only in `refresh()`/`checkTerminal` (`RunProgress.tsx:34`), so
  a non-terminal Pause/Resume would otherwise leave gating stale.
- **F2 (Unit B, important):** the findings trigger fires on stream-open mid-run (`RunProgress.tsx:25,33`), not
  at terminal; add an `onState`/`onTerminal` lift to `RunProgress` and gate `ProbePanel` on terminal (§3, §5-B).
- **F3 (Unit A, minor):** the terminal enable-gate reuses F2's lift, else always-enable.
- **F4 (tests, minor):** stub `navigator.clipboard` / `URL.createObjectURL` under jsdom (§6).

Citation fix: the mockup "Export spec" button is `Recon Workspace.dc.html:430` (was cited `:429`).

## 10. Fast-follows

- Add `pause_requested`/`cancel_requested` to `GET /status` so control gating survives a reload.
- Postman/Burp/mitmproxy export buttons once those serializers get routes (pairs with the Export & Report modal).
- Promote the manual-probe panel into a per-endpoint finding drawer if/when the UI moves to the richer mockup.
