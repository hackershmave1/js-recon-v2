# UI Catch-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the three headless backend routes (OpenAPI export, manual-probe requests, run controls) a UI in the React SPA, and drain the deferred live-walkthrough debt.

**Architecture:** Thin React+Vite components in the existing single-scroll `RunWorkspace`, over already-shipped, RLS-scoped API routes. One shared enabler: lift the run `state` out of `RunProgress` so `RunWorkspace` can gate the export button and probe panel on a terminal run. No backend changes.

**Tech Stack:** React 19, Vite, TypeScript, Vitest + @testing-library/react + user-event. Colocated `*.test.tsx`. Design spec: `docs/superpowers/specs/2026-07-30-ui-catch-up-design.md`.

## Global Constraints

- **Branch:** `ui-catch-up` (already created; the spec is committed there).
- **Tenant:** components read it via `useTenant()` (`web/src/tenant/TenantContext.tsx`); tests set `localStorage.setItem("recon.tenantId", TENANT)` and wrap in `<TenantProvider>`.
- **API layer:** JSON calls go through `request<T>(path, init, tenantId)` in `web/src/api/apiClient.ts` (forces `Accept: application/json`, throws `ApiError(status, detail)` on non-2xx). The export download is a **blob** and must bypass `request<T>`.
- **Styling:** reuse `web/src/styles.css` classes only (`.card`, `.chip`, `.muted`, `.sev-high`); no new stylesheet (thin fidelity, mockup-informed layout).
- **Do NOT modify the backend.** Contracts are shipped and verified (spec §4).
- **Type-check:** `cd web && npx tsc -b --noEmit` stays clean.
- **Test command:** `cd web && npx vitest run <path>`.
- **Commits:** Conventional, multi-line; end every commit message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Dev loop for live checks:** `docker compose build && docker compose up -d` (~2 min warm) then open `http://localhost:8000`.

## File Structure

| File | Responsibility |
|---|---|
| `web/src/api/types.ts` (modify) | +`ReconstructedRequest`, `RequestsResponse`, `RunControlResult` |
| `web/src/api/apiClient.ts` (modify) | +`getRequests`, `pauseRun`, `cancelRun`, `resumeRun`, `exportOpenApi` (blob) |
| `web/src/api/apiClient.test.ts` (modify) | tests for the 5 new functions |
| `web/src/features/progress/RunProgress.tsx` (modify) | `onState` lift + `applyState` helper; mount `RunControls` |
| `web/src/features/progress/RunProgress.test.tsx` (modify) | onState-lift + controls-render tests |
| `web/src/features/progress/RunControls.tsx` (create) | pause/cancel/resume, state-gated |
| `web/src/features/progress/RunControls.test.tsx` (create) | gating + POST + confirm tests |
| `web/src/features/export/ExportSpecButton.tsx` (create) | "Export spec" json/yaml blob download |
| `web/src/features/export/ExportSpecButton.test.tsx` (create) | download + format + error tests |
| `web/src/features/probe/ProbePanel.tsx` (create) | reconstructed requests, copy curl / raw-HTTP |
| `web/src/features/probe/ProbePanel.test.tsx` (create) | list + copy + non-probeable + empty tests |
| `web/src/app.tsx` (modify) | track run state; terminal-gate `ExportSpecButton` + `ProbePanel` |
| `web/src/app.test.tsx` (modify) | terminal-gating tests |
| `web/src/setupTests.ts` (modify) | jsdom stubs for `URL.createObjectURL` + `navigator.clipboard` |

---

### Task 1: API layer — types + five apiClient functions

**Files:**
- Modify: `web/src/api/types.ts`
- Modify: `web/src/api/apiClient.ts`
- Test: `web/src/api/apiClient.test.ts`

**Interfaces:**
- Consumes: `request<T>`, `ApiError` (existing, `apiClient.ts:5-28`).
- Produces:
  - `ReconstructedRequest`, `RequestsResponse`, `RunControlResult` (types)
  - `getRequests(tenantId, runId): Promise<RequestsResponse>`
  - `pauseRun/cancelRun/resumeRun(tenantId, runId): Promise<RunControlResult>`
  - `exportOpenApi(tenantId, runId, format: "json"|"yaml"): Promise<Blob>`

- [ ] **Step 1: Add the failing tests** to `web/src/api/apiClient.test.ts` — extend the import on line 2 to include the new functions, then append inside the `describe`:

```ts
// import line 2 becomes:
// import { ApiError, getFindings, createSession, startRun, getAssets, attachSpec, getRequests, pauseRun, cancelRun, resumeRun, exportOpenApi } from "./apiClient";

  it("GETs /runs/{id}/requests for getRequests", async () => {
    const f = mockFetch(200, { run_id: "r1", count: 0, requests: [] });
    vi.stubGlobal("fetch", f);
    await getRequests("t", "r1");
    const [path, init] = f.mock.calls[0];
    expect(path).toBe("/runs/r1/requests");
    expect((init.headers as Record<string, string>)["X-Tenant-Id"]).toBe("t");
  });

  it("POSTs /runs/{id}/pause and returns the state + flag", async () => {
    const f = mockFetch(200, { run_id: "r1", state: "paused", pause_requested: true });
    vi.stubGlobal("fetch", f);
    const res = await pauseRun("t", "r1");
    const [path, init] = f.mock.calls[0];
    expect(path).toBe("/runs/r1/pause");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Tenant-Id"]).toBe("t");
    expect(res.state).toBe("paused");
  });

  it("POSTs /runs/{id}/cancel and /resume", async () => {
    const f = mockFetch(200, { run_id: "r1", state: "cancelled", cancel_requested: true });
    vi.stubGlobal("fetch", f);
    await cancelRun("t", "r1");
    expect(f.mock.calls[0][0]).toBe("/runs/r1/cancel");
    const g = mockFetch(200, { run_id: "r1", state: "analyzing" });
    vi.stubGlobal("fetch", g);
    await resumeRun("t", "r1");
    expect(g.mock.calls[0][0]).toBe("/runs/r1/resume");
  });

  it("exportOpenApi returns a Blob and hits the format query + tenant header", async () => {
    const blob = new Blob(["{}"], { type: "application/json" });
    const f = vi.fn().mockResolvedValue({ ok: true, status: 200, blob: async () => blob });
    vi.stubGlobal("fetch", f);
    const out = await exportOpenApi("t", "r1", "yaml");
    const [path, init] = f.mock.calls[0];
    expect(path).toBe("/runs/r1/export/openapi?format=yaml");
    expect((init.headers as Record<string, string>)["X-Tenant-Id"]).toBe("t");
    expect(out).toBeInstanceOf(Blob);
  });

  it("exportOpenApi throws ApiError with detail on non-2xx", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 404, json: async () => ({ detail: "run not found" }),
    }));
    await expect(exportOpenApi("t", "missing", "json")).rejects.toMatchObject({ status: 404, message: "run not found" });
  });
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd web && npx vitest run src/api/apiClient.test.ts`
Expected: FAIL (`getRequests`/`exportOpenApi` etc. not exported).

- [ ] **Step 3: Add the types** to `web/src/api/types.ts` (append before the `TERMINAL_STATES` const on line 70):

```ts
// One reconstructed request from GET /runs/{id}/requests (probe_router::_request_dict).
// `artifacts` is null when `probeable` is false.
export interface ReconstructedRequest {
  operation: string; method: string; path: string; hosts: string[];
  query_params: { name: string; example: string | null }[];
  body_params: string[]; content_type: string | null; example_url: string | null;
  probeable: boolean; endpoint_hashes: string[];
  artifacts: { curl: string; http: string } | null;
}
export interface RequestsResponse { run_id: string; count: number; requests: ReconstructedRequest[]; }
// Result of POST pause/cancel/resume. pause returns pause_requested; cancel returns
// cancel_requested; resume returns neither — all three return the authoritative state.
export interface RunControlResult { run_id: string; state: string; pause_requested?: boolean; cancel_requested?: boolean; }
```

- [ ] **Step 4: Add the functions** to `web/src/api/apiClient.ts`. Extend the type import on line 1-3 to add `RequestsResponse, RunControlResult`, then append at the end of the file:

```ts
export function getRequests(tenantId: string, runId: string): Promise<RequestsResponse> {
  return request(`/runs/${encodeURIComponent(runId)}/requests`, {}, tenantId);
}

export function pauseRun(tenantId: string, runId: string): Promise<RunControlResult> {
  return request(`/runs/${encodeURIComponent(runId)}/pause`, { method: "POST" }, tenantId);
}
export function cancelRun(tenantId: string, runId: string): Promise<RunControlResult> {
  return request(`/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }, tenantId);
}
export function resumeRun(tenantId: string, runId: string): Promise<RunControlResult> {
  return request(`/runs/${encodeURIComponent(runId)}/resume`, { method: "POST" }, tenantId);
}

// Blob variant: the export route streams a file (Content-Disposition), not JSON, so it
// bypasses request<T> (which forces Accept: application/json + res.json()). A bare
// <a href> can't carry X-Tenant-Id, so we fetch + trigger the download in JS.
export async function exportOpenApi(tenantId: string, runId: string, format: "json" | "yaml"): Promise<Blob> {
  const res = await fetch(
    `/runs/${encodeURIComponent(runId)}/export/openapi?format=${format}`,
    { headers: { "X-Tenant-Id": tenantId } },
  );
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail ?? detail; } catch { /* non-JSON body */ }
    throw new ApiError(res.status, detail);
  }
  return res.blob();
}
```

- [ ] **Step 5: Run tests + type-check**

Run: `cd web && npx vitest run src/api/apiClient.test.ts && npx tsc -b --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 6: Commit**

```bash
git add web/src/api/types.ts web/src/api/apiClient.ts web/src/api/apiClient.test.ts
git commit -m "feat(ui-catch-up): add apiClient fns for requests, run controls, openapi export" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Lift run state out of RunProgress (§4 gate F1/F2 enabler)

**Files:**
- Modify: `web/src/features/progress/RunProgress.tsx`
- Modify: `web/src/app.tsx`
- Test: `web/src/features/progress/RunProgress.test.tsx`

**Interfaces:**
- Consumes: existing `RunProgress` internals (`getStatus`, `streamRunEvents`).
- Produces: `RunProgress` gains an optional `onState?: (state: string) => void` prop, called whenever the run state is (re)resolved; a stable `applyState(state)` helper. `RunWorkspace` tracks `state` in local state.

- [ ] **Step 1: Add the failing test** to `web/src/features/progress/RunProgress.test.tsx` (append inside `describe`):

```ts
  it("lifts the run state to onState when status resolves", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "done", stage: null, done: 2, total: 2, pct: 100, eta_seconds: null, heartbeat_at: null, stalled: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
    const onState = vi.fn();
    render(<TenantProvider><RunProgress runId="r" onFindings={() => {}} onState={onState} /></TenantProvider>);
    await waitFor(() => expect(onState).toHaveBeenCalledWith("done"));
  });
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd web && npx vitest run src/features/progress/RunProgress.test.tsx -t "lifts the run state"`
Expected: FAIL (`onState` not a prop / not called).

- [ ] **Step 3: Implement the lift** in `web/src/features/progress/RunProgress.tsx`:
  - Line 1 import: add `useCallback` → `import { useCallback, useEffect, useRef, useState } from "react";`
  - Change the signature (line 7) to accept `onState`:

```tsx
export function RunProgress(
  { runId, onFindings, onState }: { runId: string; onFindings: (f: FindingsResponse) => void; onState?: (state: string) => void },
) {
```

  - After the `onFindingsRef` lines (around line 14-15), add:

```tsx
  const onStateRef = useRef(onState);
  onStateRef.current = onState;
  const applyState = useCallback((s: string) => { setState(s); onStateRef.current?.(s); }, []);
```

  - In `refresh()` replace `setState(s.state);` (line 24) with `applyState(s.state);`
  - In `checkTerminal` replace `setState(s.state);` (line 39) with `applyState(s.state);`

- [ ] **Step 4: Track state in RunWorkspace** — `web/src/app.tsx`, replace the `RunWorkspace` function body:

```tsx
export function RunWorkspace() {
  const { id } = useParams();
  const { tenantId } = useTenant();
  const [findings, setFindings] = useState<FindingsResponse | null>(null);
  const [state, setState] = useState<string | null>(null);
  if (!id) return null;
  return (
    <div>
      <RunProgress runId={id} onFindings={setFindings} onState={setState} />
      {tenantId && <AssetsInventory tenantId={tenantId} runId={id} />}
      {findings && <FindingsView data={findings} runId={id} />}
    </div>
  );
}
```

- [ ] **Step 5: Run the RunProgress + app suites + type-check**

Run: `cd web && npx vitest run src/features/progress/RunProgress.test.tsx src/app.test.tsx && npx tsc -b --noEmit`
Expected: PASS (existing tests still green; new onState test passes).

- [ ] **Step 6: Commit**

```bash
git add web/src/features/progress/RunProgress.tsx web/src/features/progress/RunProgress.test.tsx web/src/app.tsx
git commit -m "feat(ui-catch-up): lift run state out of RunProgress for terminal gating" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Unit A — ExportSpecButton

**Files:**
- Create: `web/src/features/export/ExportSpecButton.tsx`
- Create: `web/src/features/export/ExportSpecButton.test.tsx`
- Modify: `web/src/setupTests.ts`
- Modify: `web/src/app.tsx`
- Modify: `web/src/app.test.tsx`

**Interfaces:**
- Consumes: `exportOpenApi` (Task 1); the `state` tracking (Task 2).
- Produces: `<ExportSpecButton runId={string} />`, rendered terminal-gated in `RunWorkspace`.

- [ ] **Step 1: Add the jsdom stub** for downloads to `web/src/setupTests.ts`:

```ts
import "@testing-library/jest-dom/vitest";

// jsdom's URL.createObjectURL throws "Not implemented"; the export-download test needs it.
URL.createObjectURL = () => "blob:mock";
URL.revokeObjectURL = () => {};
```

- [ ] **Step 2: Write the failing test** — `web/src/features/export/ExportSpecButton.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExportSpecButton } from "./ExportSpecButton";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function ui() { return render(<TenantProvider><ExportSpecButton runId="r" /></TenantProvider>); }

describe("ExportSpecButton", () => {
  it("downloads the spec via a blob anchor on click", async () => {
    vi.spyOn(api, "exportOpenApi").mockResolvedValue(new Blob(["{}"], { type: "application/json" }));
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    ui();
    await userEvent.click(screen.getByRole("button", { name: /export spec/i }));
    expect(api.exportOpenApi).toHaveBeenCalledWith(TENANT, "r", "json");
    expect(clickSpy).toHaveBeenCalled();
  });

  it("exports yaml when the format is switched", async () => {
    vi.spyOn(api, "exportOpenApi").mockResolvedValue(new Blob(["a: 1"]));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    ui();
    await userEvent.selectOptions(screen.getByLabelText(/export format/i), "yaml");
    await userEvent.click(screen.getByRole("button", { name: /export spec/i }));
    expect(api.exportOpenApi).toHaveBeenCalledWith(TENANT, "r", "yaml");
  });

  it("shows an inline error when export fails", async () => {
    vi.spyOn(api, "exportOpenApi").mockRejectedValue(new api.ApiError(500, "failed to build a valid OpenAPI document"));
    ui();
    await userEvent.click(screen.getByRole("button", { name: /export spec/i }));
    expect(await screen.findByText(/couldn't export spec/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run to confirm it fails**

Run: `cd web && npx vitest run src/features/export/ExportSpecButton.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 4: Implement** `web/src/features/export/ExportSpecButton.tsx`:

```tsx
import { useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { exportOpenApi, ApiError } from "../../api/apiClient";

export function ExportSpecButton({ runId }: { runId: string }) {
  const { tenantId } = useTenant();
  const [format, setFormat] = useState<"json" | "yaml">("json");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    if (!tenantId || busy) return;
    setBusy(true); setError(null);
    try {
      const blob = await exportOpenApi(tenantId, runId, format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `openapi-${runId}.${format}`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof ApiError ? `Couldn't export spec: ${err.message}` : "Couldn't export spec");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <button type="button" onClick={download} disabled={busy}>{busy ? "Exporting…" : "Export spec"}</button>
      <select value={format} onChange={(e) => setFormat(e.target.value as "json" | "yaml")} aria-label="Export format">
        <option value="json">JSON</option>
        <option value="yaml">YAML</option>
      </select>
      {error && <span className="sev-high"> {error}</span>}
    </div>
  );
}
```

- [ ] **Step 5: Run test + type-check**

Run: `cd web && npx vitest run src/features/export/ExportSpecButton.test.tsx && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 6: Wire it into RunWorkspace, terminal-gated** — `web/src/app.tsx`:
  - Add imports: `import { ExportSpecButton } from "./features/export/ExportSpecButton";` and extend the types import with `TERMINAL_STATES`: `import { TERMINAL_STATES, type FindingsResponse } from "./api/types";`
  - Inside `RunWorkspace`, after `if (!id) return null;` add: `const terminal = state != null && TERMINAL_STATES.has(state);`
  - Render before `FindingsView`: `{terminal && <ExportSpecButton runId={id} />}`

- [ ] **Step 7: Update app.test.tsx** — extend the mocked `RunProgress` to lift a terminal state, and assert the button appears. Change the mock (lines 14-24) so the effect also calls `onState`, and add a test:

```tsx
// in the vi.mock factory, widen the prop type and add onState to the effect:
//   RunProgress: ({ onFindings, onState }: { runId: string; onFindings: (f: FindingsResponse) => void; onState?: (s: string) => void }) => {
//     useEffect(() => { onFindings({ ...same fixture... }); onState?.("done"); }, [onFindings, onState]);
//     return <div>PROGRESS</div>;
//   },

  it("shows the Export spec button once the run is terminal", async () => {
    renderAt("/runs/r1");
    expect(await screen.findByRole("button", { name: /export spec/i })).toBeInTheDocument();
  });
```

- [ ] **Step 8: Run the app suite + type-check**

Run: `cd web && npx vitest run src/app.test.tsx src/features/export/ExportSpecButton.test.tsx && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add web/src/features/export/ web/src/setupTests.ts web/src/app.tsx web/src/app.test.tsx
git commit -m "feat(ui-catch-up): OpenAPI export-spec download button (Unit A)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Unit B — ProbePanel (manual-probe handoff)

**Files:**
- Create: `web/src/features/probe/ProbePanel.tsx`
- Create: `web/src/features/probe/ProbePanel.test.tsx`
- Modify: `web/src/setupTests.ts`
- Modify: `web/src/app.tsx`
- Modify: `web/src/app.test.tsx`

**Interfaces:**
- Consumes: `getRequests` (Task 1); the `terminal` gate (Task 3).
- Produces: `<ProbePanel runId={string} />`, terminal-gated after `FindingsView`.

- [ ] **Step 1: Add the clipboard stub** to `web/src/setupTests.ts` (append after the URL stubs from Task 3):

```ts
// jsdom has no navigator.clipboard; the probe copy tests spy on writeText.
Object.defineProperty(navigator, "clipboard", {
  value: { writeText: async () => {} },
  configurable: true,
});
```

- [ ] **Step 2: Write the failing test** — `web/src/features/probe/ProbePanel.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProbePanel } from "./ProbePanel";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import type { ReconstructedRequest } from "../../api/types";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

const REQ: ReconstructedRequest = {
  operation: "GET /api/users", method: "GET", path: "/api/users", hosts: ["api.acme.io"],
  query_params: [{ name: "page", example: "1" }], body_params: [], content_type: null,
  example_url: "https://api.acme.io/api/users?page=1", probeable: true, endpoint_hashes: ["h1"],
  artifacts: { curl: "curl 'https://api.acme.io/api/users?page=1'", http: "GET /api/users?page=1 HTTP/1.1" },
};

function ui(reqs: ReconstructedRequest[]) {
  vi.spyOn(api, "getRequests").mockResolvedValue({ run_id: "r", count: reqs.length, requests: reqs });
  return render(<TenantProvider><ProbePanel runId="r" /></TenantProvider>);
}

describe("ProbePanel", () => {
  it("lists reconstructed requests and copies curl", async () => {
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    ui([REQ]);
    expect(await screen.findByText("/api/users")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /copy curl/i }));
    expect(writeText).toHaveBeenCalledWith(REQ.artifacts!.curl);
  });

  it("marks a non-probeable request", async () => {
    ui([{ ...REQ, probeable: false, artifacts: null }]);
    expect(await screen.findByText(/not probeable/i)).toBeInTheDocument();
  });

  it("shows an empty message when there are no requests", async () => {
    ui([]);
    expect(await screen.findByText(/no probeable requests/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run to confirm it fails**

Run: `cd web && npx vitest run src/features/probe/ProbePanel.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 4: Implement** `web/src/features/probe/ProbePanel.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { getRequests, ApiError } from "../../api/apiClient";
import type { ReconstructedRequest, RequestsResponse } from "../../api/types";

function ProbeRequestCard({ req }: { req: ReconstructedRequest }) {
  const [copied, setCopied] = useState<"curl" | "http" | null>(null);
  async function copy(kind: "curl" | "http", text: string) {
    await navigator.clipboard.writeText(text);
    setCopied(kind); setTimeout(() => setCopied(null), 1200);
  }
  return (
    <div className="card">
      <span className="chip">{req.method}</span> <code>{req.path}</code>
      {req.query_params.length > 0 && <p className="muted">query: {req.query_params.map((q) => q.name).join(", ")}</p>}
      {req.body_params.length > 0 && <p className="muted">body: {req.body_params.join(", ")}</p>}
      {req.artifacts ? (
        <div>
          <button type="button" onClick={() => copy("curl", req.artifacts!.curl)}>{copied === "curl" ? "Copied ✓" : "Copy curl"}</button>
          <button type="button" onClick={() => copy("http", req.artifacts!.http)}>{copied === "http" ? "Copied ✓" : "Copy raw-HTTP"}</button>
        </div>
      ) : <p className="muted">not probeable</p>}
    </div>
  );
}

export function ProbePanel({ runId }: { runId: string }) {
  const { tenantId } = useTenant();
  const [data, setData] = useState<RequestsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    getRequests(tenantId, runId)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load requests"));
  }, [tenantId, runId]);

  if (error) return <div className="card"><h3>Manual probe</h3><p className="sev-high">{error}</p></div>;
  if (!data) return null;
  if (data.count === 0) return <div className="card"><h3>Manual probe</h3><p className="muted">No probeable requests reconstructed.</p></div>;
  return (
    <div className="card">
      <h3>Manual probe <span className="muted">({data.count})</span></h3>
      {data.requests.map((r) => <ProbeRequestCard key={r.operation} req={r} />)}
    </div>
  );
}
```

- [ ] **Step 5: Run test + type-check**

Run: `cd web && npx vitest run src/features/probe/ProbePanel.test.tsx && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 6: Wire it into RunWorkspace, terminal-gated** — `web/src/app.tsx`:
  - Add import: `import { ProbePanel } from "./features/probe/ProbePanel";`
  - Render after `FindingsView`: `{terminal && <ProbePanel runId={id} />}`

- [ ] **Step 7: Update app.test.tsx** — because `ProbePanel` now mounts on the terminal path and fetches, mock `getRequests` and assert the panel renders. Add `import * as api from "./api/apiClient";` at the top, then in `beforeEach` (or the new test) stub it, and add:

```tsx
  it("shows the manual-probe panel once the run is terminal", async () => {
    vi.spyOn(api, "getRequests").mockResolvedValue({ run_id: "r1", count: 0, requests: [] });
    renderAt("/runs/r1");
    expect(await screen.findByText(/no probeable requests/i)).toBeInTheDocument();
  });
```

Note: add `import { vi } from "vitest";` if not already imported (it is, line 1).

- [ ] **Step 8: Run the app + probe suites + type-check**

Run: `cd web && npx vitest run src/app.test.tsx src/features/probe/ProbePanel.test.tsx && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add web/src/features/probe/ web/src/setupTests.ts web/src/app.tsx web/src/app.test.tsx
git commit -m "feat(ui-catch-up): manual-probe panel with curl + raw-HTTP copy (Unit B)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Unit C — RunControls (pause/cancel/resume)

**Files:**
- Create: `web/src/features/progress/RunControls.tsx`
- Create: `web/src/features/progress/RunControls.test.tsx`
- Modify: `web/src/features/progress/RunProgress.tsx`
- Modify: `web/src/features/progress/RunProgress.test.tsx`

**Interfaces:**
- Consumes: `pauseRun`, `cancelRun`, `resumeRun` (Task 1); `TERMINAL_STATES` (types); `applyState` (Task 2).
- Produces: `<RunControls runId={string} state={string} onStateChange={(s: string) => void} />`, mounted inside `RunProgress`.

- [ ] **Step 1: Write the failing test** — `web/src/features/progress/RunControls.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RunControls } from "./RunControls";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function ui(state: string, onStateChange = vi.fn()) {
  render(<TenantProvider><RunControls runId="r" state={state} onStateChange={onStateChange} /></TenantProvider>);
  return onStateChange;
}

describe("RunControls", () => {
  it("renders nothing for a terminal run", () => {
    const { container } = render(<TenantProvider><RunControls runId="r" state="done" onStateChange={() => {}} /></TenantProvider>);
    expect(container.querySelector("button")).toBeNull();
  });

  it("shows Pause + Cancel for an active run and pauses", async () => {
    vi.spyOn(api, "pauseRun").mockResolvedValue({ run_id: "r", state: "paused", pause_requested: true });
    const onStateChange = ui("analyzing");
    await userEvent.click(screen.getByRole("button", { name: /pause/i }));
    expect(api.pauseRun).toHaveBeenCalledWith(TENANT, "r");
    expect(onStateChange).toHaveBeenCalledWith("paused");
  });

  it("shows Resume (not Pause) for a paused run", () => {
    ui("paused");
    expect(screen.getByRole("button", { name: /resume/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^pause$/i })).not.toBeInTheDocument();
  });

  it("confirms before cancelling and lifts the new state", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(api, "cancelRun").mockResolvedValue({ run_id: "r", state: "cancelled", cancel_requested: true });
    const onStateChange = ui("analyzing");
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(window.confirm).toHaveBeenCalled();
    expect(onStateChange).toHaveBeenCalledWith("cancelled");
  });

  it("does not cancel when the confirm is dismissed", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const cancel = vi.spyOn(api, "cancelRun");
    ui("analyzing");
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(cancel).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd web && npx vitest run src/features/progress/RunControls.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement** `web/src/features/progress/RunControls.tsx`:

```tsx
import { useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { pauseRun, cancelRun, resumeRun, ApiError } from "../../api/apiClient";
import { TERMINAL_STATES } from "../../api/types";
import type { RunControlResult } from "../../api/types";

export function RunControls(
  { runId, state, onStateChange }: { runId: string; state: string; onStateChange: (s: string) => void },
) {
  const { tenantId } = useTenant();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (TERMINAL_STATES.has(state)) return null;

  async function act(fn: (t: string, r: string) => Promise<RunControlResult>, confirmMsg?: string) {
    if (!tenantId || busy) return;
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setBusy(true); setError(null);
    try {
      const res = await fn(tenantId, runId);
      onStateChange(res.state);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      {state === "paused"
        ? <button type="button" onClick={() => act(resumeRun)} disabled={busy}>Resume</button>
        : <button type="button" onClick={() => act(pauseRun)} disabled={busy}>Pause</button>}
      <button type="button" onClick={() => act(cancelRun, "Cancel this run? This cannot be undone.")} disabled={busy}>Cancel</button>
      {error && <span className="sev-high"> {error}</span>}
    </div>
  );
}
```

- [ ] **Step 4: Run the RunControls test + type-check**

Run: `cd web && npx vitest run src/features/progress/RunControls.test.tsx && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 5: Mount RunControls inside RunProgress** — `web/src/features/progress/RunProgress.tsx`:
  - Add import: `import { RunControls } from "./RunControls";`
  - In the returned JSX, inside the `.card` `<div>`, after the closing `</p>` of the state line (around line 65) and before `{error && ...}`, add (guarding the pre-load `"…"` sentinel so controls don't flash before the first status resolves):

```tsx
      {state !== "…" && <RunControls runId={runId} state={state} onStateChange={applyState} />}
```

- [ ] **Step 6: Add a RunProgress integration test** — `web/src/features/progress/RunProgress.test.tsx` (append):

```ts
  it("shows run controls (Pause) for an active run", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "analyzing", stage: "analyze", done: 1, total: 2, pct: 50, eta_seconds: null, heartbeat_at: null, stalled: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
    render(<TenantProvider><RunProgress runId="r" onFindings={() => {}} /></TenantProvider>);
    expect(await screen.findByRole("button", { name: /pause/i })).toBeInTheDocument();
  });
```

- [ ] **Step 7: Run the progress suite + full FE suite + type-check**

Run: `cd web && npx vitest run src/features/progress/ && npx vitest run && npx tsc -b --noEmit`
Expected: PASS (full suite green — was 51, now higher).

- [ ] **Step 8: Commit**

```bash
git add web/src/features/progress/
git commit -m "feat(ui-catch-up): pause/cancel/resume run controls (Unit C)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Unit D — Live in-container walkthrough (verification; no code)

This task discharges the deferred live-walkthrough debt (spec §5-D). It is verification, not TDD: no commit unless a defect surfaces (then fix in the owning unit's files and re-run its tests).

**Interfaces:** Consumes the built image; exercises every new + previously-unwalked surface against the running stack.

- [ ] **Step 1: Rebuild + restart the stack**

Run: `docker compose build && docker compose up -d`
Expected: `~1-2 min`; `api` becomes healthy (`docker compose ps` shows `healthy`).

- [ ] **Step 2: Open the app** — use the preview/browser tools to open `http://localhost:8000`. In the app, set the tenant (the UI's tenant gate) and create a session scoped to a host, or use the upload flow.

- [ ] **Step 3: Drive a single-file run to terminal** — upload a JS file (e.g. a small file containing a `fetch("/api/...")` call, or `web/fixtures/app.js` content). Watch `RunProgress` reach a terminal state (`done`).

- [ ] **Step 4: Verify Unit A (Export)** — the **Export spec** button appears; click it (json), confirm a file downloads; switch to yaml and confirm. Screenshot.

- [ ] **Step 5: Verify Unit B (Probe)** — the **Manual probe** panel lists reconstructed requests with method chips; **Copy curl** and **Copy raw-HTTP** copy to the clipboard; non-probeable requests read "not probeable". Screenshot.

- [ ] **Step 6: Verify Unit C (Controls)** — start a longer run (a domain crawl, see Step 7) and confirm **Pause**/**Cancel** show for an active run, **Cancel** prompts a confirm and moves the run to `cancelled`, and a paused run shows **Resume**. Screenshot.

- [ ] **Step 7: Drain the walkthrough debt** — drive a **domain crawl** run (bare in-scope domain → katana discovers `.js` → `AssetsInventory` populates; multi-asset fetch/analyze), attach a spec via **SpecUpload** (shadow buckets update), and add a **BaseUrlPanel** prefix rule (a relative op re-resolves; `matched_operation` shows). Screenshot each.

- [ ] **Step 8: Capture proof + update the debt ledger** — attach the screenshots; in `docs/slice2-deferred-debt.md`, mark the "Live in-UI walkthrough" rows (UI-0 / Slice X / Slice Y) as done (dated), then commit:

```bash
git add docs/slice2-deferred-debt.md
git commit -m "docs(ui-catch-up): mark deferred live UI walkthroughs done" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** Unit A → Tasks 1,3. Unit B → Tasks 1,4. Unit C → Tasks 1,2,5. Unit D → Task 6. The §4 gate folds: F1 (control gating off POST `state`, not SSE) → Task 5 `onStateChange(res.state)`; F2 (terminal lift) → Task 2; F3 (export terminal gate reuses the lift) → Task 3 Step 6; F4 (jsdom stubs) → Tasks 3,4 setupTests. All spec §5 behaviors have a task.

**Placeholder scan:** every code step contains full code; commands have expected outcomes; no "TBD"/"handle errors" left abstract (error paths are shown inline).

**Type consistency:** `exportOpenApi(tenantId, runId, format)`, `getRequests(tenantId, runId)`, `pauseRun/cancelRun/resumeRun(tenantId, runId)`, `RunControlResult.state`, `ReconstructedRequest.artifacts`, and `RunControls`'s `{runId, state, onStateChange}` props are used identically across tasks. `applyState` (Task 2) is the exact callback passed as `onStateChange` (Task 5).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-30-ui-catch-up.md`.
