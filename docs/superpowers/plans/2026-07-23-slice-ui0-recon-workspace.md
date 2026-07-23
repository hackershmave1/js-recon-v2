# Slice UI-0 — Recon Workspace core loop · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a thin React+Vite UI over the already-done FastAPI backend that drives the Recon Workspace core loop end-to-end: upload a JS file → watch it analyze live → orient on findings + coverage → triage → reveal a secret.

**Architecture:** A new `web/` React+TS SPA calls the existing API same-origin (Vite dev proxy in dev; FastAPI serves the built `dist/` in prod). Live progress uses a hand-rolled fetch-based SSE reader so the `X-Tenant-Id` header still rides every request (native `EventSource` can't set headers). Two small additive backend edits: serve the SPA, and turn an unknown-tenant DB error into a clean 400.

**Tech Stack:** React 18 + TypeScript + Vite; `react-router` v7; Vitest + React Testing Library (jsdom); Python/FastAPI backend (unchanged pipeline).

Source spec: `docs/superpowers/specs/2026-07-23-slice-ui0-recon-workspace-design.md` (commit `7599c74`). Read spec §5 (SSE lifecycle) and §8 (security invariants) before coding.

## Global Constraints

- **Node 20 LTS**, package manager **npm**, and a **committed `web/package-lock.json`** (CI runs `npm ci`, which fails without it).
- **`react-router` v7** — the package is `react-router` (NOT `react-router-dom`); import `createBrowserRouter`/`useParams`/`useNavigate` from `"react-router"` and `RouterProvider` from `"react-router/dom"`.
- **TypeScript strict mode** on. Tests **colocated** beside source as `*.test.ts` / `*.test.tsx`.
- **Only two backend files change:** `src/recon/api/app.py` (serve SPA) and `src/recon/api/sessions_router.py` (unknown-tenant → 400), plus the additive `spa_dist_dir` setting in `src/recon/config.py`. No change to the pipeline, auth model, or existing routes.
- **SSE lifecycle contract (spec §5):** a `run.transition` whose `to ∈ {done, partial, failed, cancelled}` is terminal → **stop, no reconnect**; a clean close checks `/status` and only stops if terminal (else reconnect — the server caps a stream at 300s); after 3 consecutive network errors, fall back to polling `/status`. Because Redis replay is not gap-free, the client fetches findings + status **on stream-open and clean close**.
- **Security (spec §8):** the tenant UUID lives only in `localStorage` + context, never in a URL/query/log. Secret values are never rendered before an explicit reveal; the reveal value is shown once (client affordance only — the backend reveal is repeatable + audited).
- **Commits:** Conventional Commits, and every commit ends with the trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **API contracts (verified against code):** triage statuses are exactly `{"open","confirmed","dismissed"}` (invalid→400); reveal denials map `no_offsets→422`, `source_gone→410`, `integrity→409`, unexpected infra error→500; `POST /sessions` needs `scope_hosts` (≥1) + `authorized_by`; `POST /runs/upload` is multipart (`file`, `session_id`, optional `map`, `target`) → `202 {run_id, state}`.

---

## File structure

```
src/recon/config.py                         # MODIFY: + spa_dist_dir setting
src/recon/api/app.py                         # MODIFY: serve SPA after routers
src/recon/api/spa_serving_test.py            # CREATE: SPA-serving tests
src/recon/api/sessions_router.py             # MODIFY: unknown-tenant → 400
src/recon/api/sessions_router_test.py        # CREATE: unknown-tenant test
web/package.json, package-lock.json          # CREATE (scaffold)
web/vite.config.ts                           # CREATE: dev proxy + vitest config
web/tsconfig*.json, index.html               # CREATE (scaffold)
web/src/main.tsx, app.tsx                     # CREATE: router wiring
web/src/setupTests.ts                        # CREATE: RTL matchers
web/src/api/types.ts                         # CREATE
web/src/api/apiClient.ts (+ .test.ts)        # CREATE
web/src/api/sseClient.ts (+ .test.ts)        # CREATE
web/src/tenant/TenantContext.tsx (+ .test.tsx)  # CREATE
web/src/tenant/TenantGate.tsx                # CREATE
web/src/features/newRun/NewRunPanel.tsx (+ .test.tsx)
web/src/features/progress/RunProgress.tsx (+ .test.tsx)
web/src/features/findings/FindingsView.tsx (+ .test.tsx)
web/src/features/findings/FindingDetail.tsx
web/src/features/findings/TriageControls.tsx (+ .test.tsx)
web/src/features/findings/RevealButton.tsx (+ .test.tsx)
web/src/styles.css                           # CREATE: dark baseline
Dockerfile                                   # MODIFY: node build stage + COPY dist
docker-compose.yml                           # MODIFY: RECON_SPA_DIST_DIR env
.github/workflows/ci.yml                     # MODIFY: + frontend job
web/.dockerignore                            # CREATE: ignore node_modules/dist
```

---

### Task 1: Backend — serve the built SPA from `create_app`

Serve hashed assets under `/assets`; a catch-all `GET` (registered last) returns `index.html` for browser navigations (Accept contains `text/html`) and re-raises a JSON 404 otherwise. This Accept-based rule is deliberately chosen over a path-prefix rule: the client's own deep link `/runs/:id` shares the `/runs` API prefix, so a prefix rule would 404 it — refining spec §3.

**Files:**
- Modify: `src/recon/config.py` (add `spa_dist_dir`)
- Modify: `src/recon/api/app.py`
- Test: `src/recon/api/spa_serving_test.py` (create)

**Interfaces:**
- Produces: SPA served at `/` when `settings.spa_dist_dir` (or the default repo-root `web/dist`) exists; no-op otherwise.

- [ ] **Step 1: Add the setting.** In `src/recon/config.py`, inside `Settings`, after `env`/`log_level`:

```python
    # Absolute path to the built front-end (web/dist). When set and present, the
    # API serves the SPA (assets + client-route fallback); absent → API-only.
    # Docker sets RECON_SPA_DIST_DIR=/app/web/dist (the package is pip-installed,
    # so __file__ can't locate the repo tree there). Default suits editable/dev.
    spa_dist_dir: str | None = None
```

- [ ] **Step 2: Write the failing tests.** Create `src/recon/api/spa_serving_test.py`:

```python
"""SPA serving: assets + Accept-based client-route fallback, no-op when absent."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.config import get_settings


def _client_with_dist(tmp_path: Path, monkeypatch) -> TestClient:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("RECON_SPA_DIST_DIR", str(dist))
    get_settings.cache_clear()
    return TestClient(create_app())


def test_browser_navigation_to_client_route_gets_index_html(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    # Deep link that shares the /runs API prefix must still serve the SPA shell.
    r = client.get("/runs/2b1c", headers={"accept": "text/html"})
    assert r.status_code == 200
    assert "<div id=root>" in r.text


def test_unknown_api_path_stays_json_404(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    r = client.get("/runs/2b1c/bogus", headers={"accept": "application/json"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_assets_are_served(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    assert client.get("/assets/app.js").status_code == 200


def test_no_dist_is_noop(monkeypatch):
    monkeypatch.setenv("RECON_SPA_DIST_DIR", "/nonexistent/dist")
    get_settings.cache_clear()
    client = TestClient(create_app())
    # Catch-all not registered → default Starlette 404 for an unknown path.
    assert client.get("/", headers={"accept": "text/html"}).status_code == 404


def test_existing_api_route_still_wins(monkeypatch):
    monkeypatch.setenv("RECON_SPA_DIST_DIR", "/nonexistent/dist")
    get_settings.cache_clear()
    client = TestClient(create_app())
    assert client.get("/healthz").status_code == 200
```

- [ ] **Step 3: Run tests to verify they fail.** Run: `./.venv/Scripts/python.exe -m pytest src/recon/api/spa_serving_test.py -v`. Expected: FAIL (no SPA serving yet; client routes 404 even with Accept text/html).

- [ ] **Step 4: Implement SPA serving.** In `src/recon/api/app.py`, add imports at top:

```python
import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
```

Add a helper and mount block at the end of `create_app()` (after `app.include_router(...)` calls and the `/healthz` route, before `return app`):

```python
    _mount_spa(app, settings)
    ...
    return app


def _default_dist() -> Path:
    # Editable/dev layout: src/recon/api/app.py → repo_root/web/dist.
    return Path(__file__).resolve().parents[3] / "web" / "dist"


def _mount_spa(app: FastAPI, settings) -> None:
    dist = Path(settings.spa_dist_dir) if settings.spa_dist_dir else _default_dist()
    if not dist.is_dir():
        return  # API-only; StaticFiles(check_dir=True) would otherwise raise here
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    index = dist / "index.html"

    # Registered last → real API routes match first. Browser navigations (Accept
    # includes text/html) get the SPA shell so client-side routes like /runs/:id
    # deep-link; anything else (e.g. a typo'd API path from fetch) stays JSON 404.
    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str, accept: str = Header(default="")) -> FileResponse:
        if "text/html" in accept:
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="not found")
```

Add `Header` to the existing `from fastapi import ...` line (it currently imports `FastAPI`; extend to `from fastapi import FastAPI, Header, HTTPException`). Keep `HTTPException` import de-duplicated.

- [ ] **Step 5: Run tests to verify they pass.** Run: `./.venv/Scripts/python.exe -m pytest src/recon/api/spa_serving_test.py -v`. Expected: PASS (5 tests). Then run `./.venv/Scripts/python.exe -m pytest src/recon/api/app_test.py -v` to confirm no regression.

- [ ] **Step 6: Commit.**

```bash
git add src/recon/config.py src/recon/api/app.py src/recon/api/spa_serving_test.py
git commit -m "feat(api): serve the built SPA with an Accept-based client-route fallback"
```

---

### Task 2: Backend — unknown tenant returns 400, not a raw 500

A well-formed but unprovisioned tenant UUID currently raises a DB `IntegrityError` (FK) that escapes `create_session` as a 500. Map it to a clean 400 so a first-time user who mistypes a tenant gets a usable message.

**Files:**
- Modify: `src/recon/api/sessions_router.py`
- Test: `src/recon/api/sessions_router_test.py` (create)

- [ ] **Step 1: Write the failing test.** Create `src/recon/api/sessions_router_test.py`:

```python
"""Unknown-tenant mapping: a valid-format but unprovisioned tenant → 400."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from recon.api import sessions_router
from recon.api.app import create_app


def test_unknown_tenant_returns_400(monkeypatch):
    def _raise(*args, **kwargs):
        raise IntegrityError("insert", {}, Exception("FK violation"))

    monkeypatch.setattr(sessions_router.service, "create_session", _raise)
    client = TestClient(create_app())
    r = client.post(
        "/sessions",
        headers={"X-Tenant-Id": str(uuid.uuid4())},
        json={"scope_hosts": ["example.com"], "authorized_by": "tester"},
    )
    assert r.status_code == 400
    assert "tenant" in r.json()["detail"].lower()
```

- [ ] **Step 2: Run test to verify it fails.** Run: `./.venv/Scripts/python.exe -m pytest src/recon/api/sessions_router_test.py -v`. Expected: FAIL (IntegrityError → 500, not 400).

- [ ] **Step 3: Implement the mapping.** In `src/recon/api/sessions_router.py`, add `from sqlalchemy.exc import IntegrityError` and extend the `try/except` in `create_session`:

```python
    try:
        view = service.create_session(
            tenant_id,
            name=body.name,
            scope_hosts=body.scope_hosts,
            authorized_by=body.authorized_by,
        )
    except service.AuthorizationRequired as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        # A syntactically-valid tenant id that isn't provisioned violates the FK.
        raise HTTPException(status_code=400, detail="unknown tenant") from exc
```

- [ ] **Step 4: Run test to verify it passes.** Run: `./.venv/Scripts/python.exe -m pytest src/recon/api/sessions_router_test.py -v`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/recon/api/sessions_router.py src/recon/api/sessions_router_test.py
git commit -m "fix(api): map unknown-tenant FK error to 400 instead of a raw 500"
```

---

### Task 3: Scaffold the `web/` React+Vite+TS app with Vitest

Stand up the front-end project, dev proxy, test runner, and a committed lockfile. Deliverable: `npm test` green on a smoke test and `npm run build` produces `dist/`.

**Files:** create the `web/` tree (see file structure).

- [ ] **Step 1: Scaffold + install.** From the repo root:

```bash
npm create vite@latest web -- --template react-ts
cd web
npm install
npm install react-router
npm install -D vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
```

- [ ] **Step 2: Configure Vite dev proxy + Vitest.** Replace `web/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Same-origin in dev: proxy the API prefixes to FastAPI on :8000 so the browser
// (and the fetch-based SSE reader) never crosses origins → no CORS, header rides.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/runs": "http://localhost:8000",
      "/sessions": "http://localhost:8000",
      "/healthz": "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
  },
});
```

- [ ] **Step 3: Test setup + scripts.** Create `web/src/setupTests.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

In `web/package.json`, set `"scripts"` to include:

```json
{
  "dev": "vite",
  "build": "tsc -b && vite build",
  "lint": "tsc -b --noEmit",
  "test": "vitest run"
}
```

- [ ] **Step 4: Smoke test.** Create `web/src/smoke.test.ts`:

```ts
import { describe, it, expect } from "vitest";

describe("toolchain", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 5: Run + build.** Run: `npm test` (Expected: 1 passing). Run: `npm run build` (Expected: writes `web/dist/index.html` + `web/dist/assets/*`).

- [ ] **Step 6: Add `.dockerignore` + base styles.** Create `web/.dockerignore`:

```
node_modules
dist
```

Create `web/src/styles.css` with a minimal dark baseline (imported by `main.tsx` in Task 12):

```css
:root { color-scheme: dark; --bg:#0d1117; --fg:#e6edf3; --muted:#8b949e; --accent:#58a6ff; --border:#30363d; --danger:#f85149; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.5 ui-sans-serif,system-ui,sans-serif; }
button { background:var(--accent); color:#0d1117; border:0; border-radius:6px; padding:6px 12px; cursor:pointer; }
button:disabled { opacity:.5; cursor:not-allowed; }
input, textarea, select { background:#010409; color:var(--fg); border:1px solid var(--border); border-radius:6px; padding:6px 8px; }
.card { border:1px solid var(--border); border-radius:8px; padding:12px; margin:8px 0; }
.sev-high{color:var(--danger)} .muted{color:var(--muted)}
```

- [ ] **Step 7: Commit (including the lockfile).**

```bash
git add web/
git commit -m "chore(web): scaffold React+Vite+TS app with Vitest, dev proxy, dark baseline"
```

---

### Task 4: `types.ts` + `apiClient.ts` (badge-aware fetch wrapper)

**Files:** create `web/src/api/types.ts`, `web/src/api/apiClient.ts`, `web/src/api/apiClient.test.ts`.

**Interfaces:**
- Produces: `ApiError`; `createSession`, `uploadRun`, `getStatus`, `getFindings`, `triageFinding`, `revealSecret` (all take `tenantId` as first arg for testability); types `Finding`, `Coverage`, `RunStatus`, `FindingsResponse`, `SessionView`.

- [ ] **Step 1: Write types.** Create `web/src/api/types.ts`:

```ts
export interface SessionView { session_id: string; scope_hosts: string[]; authorization_ack: boolean; }
export interface RunRef { run_id: string; state: string; }
export interface RunStatus {
  run_id: string; state: string; stage: string | null; done: number; total: number;
  pct: number | null; eta_seconds: number | null; heartbeat_at: string | null; stalled: boolean;
}
export interface Occurrence {
  host: string | null; raw_url: string | null; source_path: string | null;
  line: number | null; col: number | null; evidence: string | null;
  engine: string | null; confidence: string | null; verified: boolean | null;
}
export interface Triage { status: string; note: string | null; actor: string | null; updated_at: string; }
export interface Finding {
  finding_hash: string; type: string; value: string | null; path: string | null;
  severity: string | null; attributes: Record<string, unknown>; first_stage: string | null;
  revealable: boolean; triage: Triage | null; occurrences: Occurrence[];
}
export interface Coverage {
  attributed: number; unattributed: number; secrets: number; secrets_engine: string | null;
  sources_recovered: number; source_map: boolean;
  files: { path: string; attributed: number; unattributed: number }[];
}
export interface FindingsResponse { run_id: string; count: number; coverage: Coverage | null; findings: Finding[]; }
export const TERMINAL_STATES = new Set(["done", "partial", "failed", "cancelled"]);
export const TRIAGE_STATUSES = ["open", "confirmed", "dismissed"] as const;
```

- [ ] **Step 2: Write the failing tests.** Create `web/src/api/apiClient.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ApiError, getFindings, createSession } from "./apiClient";

beforeEach(() => vi.restoreAllMocks());

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

describe("apiClient", () => {
  it("injects the X-Tenant-Id badge and Accept: application/json", async () => {
    const f = mockFetch(200, { run_id: "r1", count: 0, coverage: null, findings: [] });
    vi.stubGlobal("fetch", f);
    await getFindings("tenant-1", "r1");
    const [, init] = f.mock.calls[0];
    expect((init.headers as Record<string, string>)["X-Tenant-Id"]).toBe("tenant-1");
    expect((init.headers as Record<string, string>)["Accept"]).toBe("application/json");
  });

  it("throws ApiError with status + detail on non-2xx", async () => {
    vi.stubGlobal("fetch", mockFetch(404, { detail: "run not found" }));
    await expect(getFindings("t", "missing")).rejects.toMatchObject({
      status: 404, message: "run not found",
    });
    await expect(getFindings("t", "missing")).rejects.toBeInstanceOf(ApiError);
  });

  it("sends JSON body for createSession", async () => {
    const f = mockFetch(201, { session_id: "s1", scope_hosts: ["a"], authorization_ack: true });
    vi.stubGlobal("fetch", f);
    await createSession("t", { scope_hosts: ["a"], authorized_by: "me" });
    const [path, init] = f.mock.calls[0];
    expect(path).toBe("/sessions");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ scope_hosts: ["a"], authorized_by: "me" });
  });
});
```

- [ ] **Step 3: Run tests to verify they fail.** Run: `cd web && npx vitest run src/api/apiClient.test.ts`. Expected: FAIL (module not found).

- [ ] **Step 4: Implement `apiClient.ts`.** Create `web/src/api/apiClient.ts`:

```ts
import type { FindingsResponse, RunRef, RunStatus, SessionView, Triage } from "./types";

export class ApiError extends Error {
  constructor(public status: number, detail: string) { super(detail); this.name = "ApiError"; }
}

async function request<T>(path: string, init: RequestInit, tenantId: string): Promise<T> {
  const headers: Record<string, string> = {
    "X-Tenant-Id": tenantId,
    Accept: "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail ?? detail; } catch { /* non-JSON body */ }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export function createSession(
  tenantId: string, body: { scope_hosts: string[]; authorized_by: string; name?: string },
): Promise<SessionView> {
  return request("/sessions", json("POST", body), tenantId);
}

export function uploadRun(tenantId: string, form: FormData): Promise<RunRef> {
  return request("/runs/upload", { method: "POST", body: form }, tenantId);
}

export function getStatus(tenantId: string, runId: string): Promise<RunStatus> {
  return request(`/runs/${encodeURIComponent(runId)}/status`, {}, tenantId);
}

export function getFindings(tenantId: string, runId: string): Promise<FindingsResponse> {
  return request(`/runs/${encodeURIComponent(runId)}/findings`, {}, tenantId);
}

export function triageFinding(
  tenantId: string, runId: string, hash: string,
  body: { status: string; note?: string; actor?: string },
): Promise<Triage & { finding_hash: string }> {
  return request(
    `/runs/${encodeURIComponent(runId)}/findings/${encodeURIComponent(hash)}/triage`,
    json("POST", body), tenantId,
  );
}

export function revealSecret(
  tenantId: string, runId: string, hash: string, body: { actor?: string; reason?: string } = {},
): Promise<{ finding_hash: string; value: string }> {
  return request(
    `/runs/${encodeURIComponent(runId)}/findings/${encodeURIComponent(hash)}/reveal`,
    json("POST", body), tenantId,
  );
}
```

- [ ] **Step 5: Run tests to verify they pass.** Run: `cd web && npx vitest run src/api/apiClient.test.ts`. Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add web/src/api/types.ts web/src/api/apiClient.ts web/src/api/apiClient.test.ts
git commit -m "feat(web): badge-aware API client + shared response types"
```

---

### Task 5: `sseClient.ts` (fetch-based SSE reader honoring the lifecycle contract)

**Files:** create `web/src/api/sseClient.ts`, `web/src/api/sseClient.test.ts`.

**Interfaces:**
- Produces: `streamRunEvents(runId, tenantId, handlers)` where `handlers = { onEvent(evt), onOpen?(), checkTerminal(): Promise<boolean>, onFallback?(), signal?: AbortSignal }`; type `SseEvent { id: string; event: string; data: string }`.

- [ ] **Step 1: Write the failing tests.** Create `web/src/api/sseClient.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { streamRunEvents } from "./sseClient";

beforeEach(() => vi.restoreAllMocks());

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    pull(ctrl) { if (i < chunks.length) ctrl.enqueue(enc.encode(chunks[i++])); else ctrl.close(); },
  });
}
const ok = (chunks: string[]) => ({ ok: true, status: 200, body: streamOf(chunks) });

describe("sseClient", () => {
  it("parses a frame split mid-line across chunks", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      ok(['id: 1\nevent: analyze.coverage\nda', 'ta: {"secrets":2}\n\n'])));
    const events: unknown[] = [];
    await streamRunEvents("r", "t", {
      onEvent: (e) => events.push(e),
      checkTerminal: async () => true,
    });
    expect(events).toEqual([{ id: "1", event: "analyze.coverage", data: '{"secrets":2}' }]);
  });

  it("ignores keep-alive comment lines", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok([": keep-alive\n\n"])));
    const events: unknown[] = [];
    await streamRunEvents("r", "t", { onEvent: (e) => events.push(e), checkTerminal: async () => true });
    expect(events).toEqual([]);
  });

  it("stops on a terminal transition without reconnecting", async () => {
    const f = vi.fn().mockResolvedValue(
      ok(['id: 9\nevent: run.transition\ndata: {"from":"analyzing","to":"done"}\n\n']));
    vi.stubGlobal("fetch", f);
    const checkTerminal = vi.fn(async () => false);
    await streamRunEvents("r", "t", { onEvent: () => {}, checkTerminal });
    expect(f).toHaveBeenCalledTimes(1);
    expect(checkTerminal).not.toHaveBeenCalled();
  });

  it("on clean close checks status; reconnects only if not terminal", async () => {
    const f = vi.fn()
      .mockResolvedValueOnce(ok(["id: 1\nevent: run.progress\ndata: {}\n\n"]))
      .mockResolvedValueOnce(ok([": keep-alive\n\n"]));
    vi.stubGlobal("fetch", f);
    const checkTerminal = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    await streamRunEvents("r", "t", { onEvent: () => {}, checkTerminal });
    expect(f).toHaveBeenCalledTimes(2);
  });

  it("falls back after 3 consecutive network errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network")));
    const onFallback = vi.fn();
    await streamRunEvents("r", "t", { onEvent: () => {}, checkTerminal: async () => false, onFallback });
    expect(onFallback).toHaveBeenCalledOnce();
  });

  it("sends the badge and Last-Event-ID on reconnect", async () => {
    const f = vi.fn()
      .mockResolvedValueOnce(ok(["id: 42\nevent: run.progress\ndata: {}\n\n"]))
      .mockResolvedValueOnce(ok([": keep-alive\n\n"]));
    vi.stubGlobal("fetch", f);
    const checkTerminal = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    await streamRunEvents("r", "t", { onEvent: () => {}, checkTerminal });
    expect(f.mock.calls[0][1].headers["X-Tenant-Id"]).toBe("t");
    expect(f.mock.calls[1][1].headers["Last-Event-ID"]).toBe("42");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail.** Run: `cd web && npx vitest run src/api/sseClient.test.ts`. Expected: FAIL (module not found).

- [ ] **Step 3: Implement `sseClient.ts`.** Create `web/src/api/sseClient.ts`:

```ts
import { TERMINAL_STATES } from "./types";

export interface SseEvent { id: string; event: string; data: string; }

export interface SseHandlers {
  onEvent: (e: SseEvent) => void;
  onOpen?: () => void;                    // fetch findings/status on (re)connect
  checkTerminal: () => Promise<boolean>;  // on clean close: terminal? stop : reconnect
  onFallback?: () => void;                // after 3 network errors → poll
  signal?: AbortSignal;
}

function parseFrame(frame: string): SseEvent | null {
  if (!frame.trim() || frame.startsWith(":")) return null; // keep-alive / comment
  let id = "", event = "message", data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  return { id, event, data };
}

function isTerminal(e: SseEvent): boolean {
  if (e.event !== "run.transition") return false;
  try { return TERMINAL_STATES.has(JSON.parse(e.data)?.to); } catch { return false; }
}

export async function streamRunEvents(runId: string, tenantId: string, h: SseHandlers): Promise<void> {
  let lastId: string | null = null;
  let failures = 0;
  while (!h.signal?.aborted) {
    try {
      const headers: Record<string, string> = { "X-Tenant-Id": tenantId, Accept: "text/event-stream" };
      if (lastId) headers["Last-Event-ID"] = lastId;
      const res = await fetch(`/runs/${encodeURIComponent(runId)}/events`, { headers, signal: h.signal });
      if (!res.ok || !res.body) throw new Error(`sse ${res.status}`);
      failures = 0;
      h.onOpen?.();
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let terminal = false;
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const evt = parseFrame(buf.slice(0, idx));
          buf = buf.slice(idx + 2);
          if (!evt) continue;
          if (evt.id) lastId = evt.id;
          h.onEvent(evt);
          if (isTerminal(evt)) terminal = true;
        }
        if (terminal) return; // authoritative terminal: stop, no reconnect
      }
      // clean close with no terminal event: stop iff the run is already terminal
      if (await h.checkTerminal()) return;
      // else loop → reconnect (server caps a stream at 300s; keep watching)
    } catch (err) {
      if (h.signal?.aborted) return;
      failures += 1;
      if (failures >= 3) { h.onFallback?.(); return; }
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass.** Run: `cd web && npx vitest run src/api/sseClient.test.ts`. Expected: PASS (6 tests).

- [ ] **Step 5: Commit.**

```bash
git add web/src/api/sseClient.ts web/src/api/sseClient.test.ts
git commit -m "feat(web): fetch-based SSE reader honoring the terminal/reconnect contract"
```

---

### Task 6: Tenant context + gate

**Files:** create `web/src/tenant/TenantContext.tsx`, `web/src/tenant/TenantGate.tsx`, `web/src/tenant/TenantContext.test.tsx`.

**Interfaces:**
- Produces: `TenantProvider`, `useTenant(): { tenantId: string | null; setTenantId(v: string | null): void }`, `isValidTenant(v: string): boolean`, `TenantGate` (renders children only when a valid tenant is set; else a paste-UUID form).

- [ ] **Step 1: Write the failing tests.** Create `web/src/tenant/TenantContext.test.tsx`:

```tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TenantProvider } from "./TenantContext";
import { TenantGate, isValidTenant } from "./TenantGate";

beforeEach(() => localStorage.clear());

describe("tenant", () => {
  it("accepts server-canonicalizable UUID forms and rejects junk", () => {
    expect(isValidTenant("123e4567-e89b-12d3-a456-426614174000")).toBe(true);
    expect(isValidTenant("123e4567e89b12d3a456426614174000")).toBe(true); // un-hyphenated
    expect(isValidTenant("not-a-uuid")).toBe(false);
  });

  it("gate blocks until a valid tenant is entered, then persists it", async () => {
    render(<TenantProvider><TenantGate><div>WORKSPACE</div></TenantGate></TenantProvider>);
    expect(screen.queryByText("WORKSPACE")).toBeNull();
    await userEvent.type(screen.getByLabelText(/tenant/i), "123e4567-e89b-12d3-a456-426614174000");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(screen.getByText("WORKSPACE")).toBeInTheDocument();
    expect(localStorage.getItem("recon.tenantId")).toContain("123e4567");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail.** Run: `cd web && npx vitest run src/tenant/TenantContext.test.tsx`. Expected: FAIL.

- [ ] **Step 3: Implement.** Create `web/src/tenant/TenantContext.tsx`:

```tsx
import { createContext, useContext, useState, type ReactNode } from "react";

interface TenantCtx { tenantId: string | null; setTenantId: (v: string | null) => void; }
const Ctx = createContext<TenantCtx | null>(null);
const KEY = "recon.tenantId";

export function TenantProvider({ children }: { children: ReactNode }) {
  const [tenantId, setState] = useState<string | null>(() => localStorage.getItem(KEY));
  const setTenantId = (v: string | null) => {
    if (v) localStorage.setItem(KEY, v); else localStorage.removeItem(KEY);
    setState(v);
  };
  return <Ctx.Provider value={{ tenantId, setTenantId }}>{children}</Ctx.Provider>;
}

export function useTenant(): TenantCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTenant must be used within TenantProvider");
  return v;
}
```

Create `web/src/tenant/TenantGate.tsx`:

```tsx
import { useState, type ReactNode } from "react";
import { useTenant } from "./TenantContext";

// No stricter than the server's uuid.UUID(): accept hyphenated, un-hyphenated,
// braced, and urn forms. Normalize by stripping urn:/braces/hyphens to 32 hex.
export function isValidTenant(v: string): boolean {
  const hex = v.trim().toLowerCase().replace(/^urn:uuid:/, "").replace(/[{}-]/g, "");
  return /^[0-9a-f]{32}$/.test(hex);
}

export function TenantGate({ children }: { children: ReactNode }) {
  const { tenantId, setTenantId } = useTenant();
  const [draft, setDraft] = useState("");
  if (tenantId && isValidTenant(tenantId)) return <>{children}</>;
  const valid = isValidTenant(draft);
  return (
    <form className="card" onSubmit={(e) => { e.preventDefault(); if (valid) setTenantId(draft.trim()); }}>
      <label htmlFor="tenant">Tenant ID (UUID)</label>
      <input id="tenant" value={draft} onChange={(e) => setDraft(e.target.value)} autoComplete="off" />
      {draft && !valid && <p className="sev-high">Must be a UUID.</p>}
      <button type="submit" disabled={!valid}>Continue</button>
    </form>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass.** Run: `cd web && npx vitest run src/tenant/TenantContext.test.tsx`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add web/src/tenant/
git commit -m "feat(web): tenant context + gate persisting the X-Tenant-Id badge"
```

---

### Task 7: `NewRunPanel` (session form + upload)

**Files:** create `web/src/features/newRun/NewRunPanel.tsx` + `.test.tsx`.

**Interfaces:**
- Consumes: `createSession`, `uploadRun` (Task 4), `useTenant` (Task 6), `useNavigate` (`react-router`).
- Produces: `<NewRunPanel />` — submit disabled until ≥1 scope host + non-empty authorized-by + a chosen JS file; on submit calls `createSession` then `uploadRun(session_id + file)`, then navigates to `/runs/:id`.

- [ ] **Step 1: Write the failing test.** Create `web/src/features/newRun/NewRunPanel.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { NewRunPanel } from "./NewRunPanel";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";

const navigate = vi.fn();
vi.mock("react-router", async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"); });

function renderPanel() {
  return render(<MemoryRouter><TenantProvider><NewRunPanel /></TenantProvider></MemoryRouter>);
}

describe("NewRunPanel", () => {
  it("gates submit until scope host, authorized-by, and a file are provided", async () => {
    renderPanel();
    const submit = screen.getByRole("button", { name: /analyze/i });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/scope host/i), "example.com");
    await userEvent.type(screen.getByLabelText(/authorized by/i), "tester");
    await userEvent.upload(screen.getByLabelText(/javascript file/i),
      new File(["console.log(1)"], "app.js", { type: "text/javascript" }));
    expect(submit).toBeEnabled();
  });

  it("creates a session then uploads, then navigates to the run", async () => {
    vi.spyOn(api, "createSession").mockResolvedValue({ session_id: "s1", scope_hosts: ["example.com"], authorization_ack: true });
    vi.spyOn(api, "uploadRun").mockResolvedValue({ run_id: "run-9", state: "queued" });
    renderPanel();
    await userEvent.type(screen.getByLabelText(/scope host/i), "example.com");
    await userEvent.type(screen.getByLabelText(/authorized by/i), "tester");
    await userEvent.upload(screen.getByLabelText(/javascript file/i),
      new File(["x"], "app.js", { type: "text/javascript" }));
    await userEvent.click(screen.getByRole("button", { name: /analyze/i }));
    expect(api.createSession).toHaveBeenCalledWith("123e4567-e89b-12d3-a456-426614174000",
      { scope_hosts: ["example.com"], authorized_by: "tester" });
    expect(api.uploadRun).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith("/runs/run-9");
  });
});
```

- [ ] **Step 2: Run test to verify it fails.** Run: `cd web && npx vitest run src/features/newRun/NewRunPanel.test.tsx`. Expected: FAIL.

- [ ] **Step 3: Implement `NewRunPanel.tsx`.**

```tsx
import { useState } from "react";
import { useNavigate } from "react-router";
import { useTenant } from "../../tenant/TenantContext";
import { createSession, uploadRun } from "../../api/apiClient";
import { ApiError } from "../../api/apiClient";

export function NewRunPanel() {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const [scopeHost, setScopeHost] = useState("");
  const [authorizedBy, setAuthorizedBy] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = scopeHost.trim() !== "" && authorizedBy.trim() !== "" && file !== null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || !tenantId || !file) return;
    setBusy(true); setError(null);
    try {
      const session = await createSession(tenantId, {
        scope_hosts: [scopeHost.trim()], authorized_by: authorizedBy.trim(),
      });
      const form = new FormData();
      form.append("file", file);
      form.append("session_id", session.session_id);
      const run = await uploadRun(tenantId, form);
      navigate(`/runs/${run.run_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start run");
    } finally { setBusy(false); }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>New recon run</h2>
      <p className="muted">Declaring a scope host + who authorized this is the authorization acknowledgment.</p>
      <div><label htmlFor="scope">Scope host</label>
        <input id="scope" value={scopeHost} onChange={(e) => setScopeHost(e.target.value)} placeholder="example.com" /></div>
      <div><label htmlFor="auth">Authorized by</label>
        <input id="auth" value={authorizedBy} onChange={(e) => setAuthorizedBy(e.target.value)} /></div>
      <div><label htmlFor="file">JavaScript file</label>
        <input id="file" type="file" accept=".js,.mjs,text/javascript"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
      {error && <p className="sev-high">{error}</p>}
      <button type="submit" disabled={!ready || busy}>{busy ? "Starting…" : "Analyze"}</button>
    </form>
  );
}
```

- [ ] **Step 4: Run test to verify it passes.** Run: `cd web && npx vitest run src/features/newRun/NewRunPanel.test.tsx`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add web/src/features/newRun/
git commit -m "feat(web): new-run panel with authorized-session gate + JS upload"
```

---

### Task 8: `RunProgress` (live feed + progress, findings on open/close)

**Files:** create `web/src/features/progress/RunProgress.tsx` + `.test.tsx`.

**Interfaces:**
- Consumes: `streamRunEvents` (Task 5), `getStatus`, `getFindings` (Task 4), `useTenant`.
- Produces: `<RunProgress runId={string} onFindings={(FindingsResponse) => void} />` — subscribes on mount, renders the event feed + latest `state/stage/pct`, and calls `getFindings` + `getStatus` on stream-open and clean close; stops on terminal.

- [ ] **Step 1: Write the failing test.** Create `web/src/features/progress/RunProgress.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { RunProgress } from "./RunProgress";
import { TenantProvider } from "../../tenant/TenantContext";
import * as sse from "../../api/sseClient";
import * as api from "../../api/apiClient";

beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"); });

describe("RunProgress", () => {
  it("renders streamed events and fetches findings on open", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "analyzing", stage: "analyze", done: 1, total: 2, pct: 50, eta_seconds: null, heartbeat_at: null, stalled: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      h.onOpen?.();
      h.onEvent({ id: "1", event: "run.progress", data: '{"stage":"analyze"}' });
    });
    render(<TenantProvider><RunProgress runId="r" onFindings={() => {}} /></TenantProvider>);
    await waitFor(() => expect(screen.getByText(/analyze/)).toBeInTheDocument());
    expect(api.getFindings).toHaveBeenCalledWith("123e4567-e89b-12d3-a456-426614174000", "r");
  });
});
```

- [ ] **Step 2: Run test to verify it fails.** Run: `cd web && npx vitest run src/features/progress/RunProgress.test.tsx`. Expected: FAIL.

- [ ] **Step 3: Implement `RunProgress.tsx`.**

```tsx
import { useEffect, useRef, useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { streamRunEvents, type SseEvent } from "../../api/sseClient";
import { getFindings, getStatus } from "../../api/apiClient";
import { TERMINAL_STATES, type FindingsResponse } from "../../api/types";

export function RunProgress({ runId, onFindings }: { runId: string; onFindings: (f: FindingsResponse) => void }) {
  const { tenantId } = useTenant();
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [state, setState] = useState<string>("…");
  const [stage, setStage] = useState<string | null>(null);
  const [pct, setPct] = useState<number | null>(null);
  const onFindingsRef = useRef(onFindings);
  onFindingsRef.current = onFindings;

  useEffect(() => {
    if (!tenantId) return;
    const controller = new AbortController();
    const refresh = async () => {
      const [s, f] = await Promise.all([getStatus(tenantId, runId), getFindings(tenantId, runId)]);
      setState(s.state); setStage(s.stage); setPct(s.pct);
      onFindingsRef.current(f);
    };
    streamRunEvents(runId, tenantId, {
      signal: controller.signal,
      onOpen: () => { void refresh(); },
      onEvent: (e) => setEvents((prev) => [...prev, e]),
      checkTerminal: async () => {
        const s = await getStatus(tenantId, runId);
        setState(s.state);
        if (TERMINAL_STATES.has(s.state)) { void refresh(); return true; }
        return false;
      },
      onFallback: () => { void refresh(); },
    });
    return () => controller.abort();
  }, [tenantId, runId]);

  return (
    <div className="card">
      <h2>Run {runId}</h2>
      <p>State: <strong>{state}</strong>{stage ? ` · ${stage}` : ""}{pct != null ? ` · ${pct}%` : ""}</p>
      <ul>{events.map((e, i) => <li key={i} className="muted">{e.event}: {e.data}</li>)}</ul>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes.** Run: `cd web && npx vitest run src/features/progress/RunProgress.test.tsx`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add web/src/features/progress/
git commit -m "feat(web): run-progress view with live SSE feed + findings on open/close"
```

---

### Task 9: `FindingsView` + `FindingDetail`

**Files:** create `web/src/features/findings/FindingsView.tsx`, `FindingDetail.tsx`, `FindingsView.test.tsx`.

**Interfaces:**
- Consumes: types (Task 4), `TriageControls` (Task 10 — import lazily; for this task render a placeholder slot), `RevealButton` (Task 11).
- Produces: `<FindingsView data={FindingsResponse} runId={string} />` — coverage summary + findings grouped by `type`, each `FindingDetail` showing occurrences; secret `value`/`evidence` never shown raw (already redacted server-side).

> Build order note: Tasks 10 and 11 produce `TriageControls`/`RevealButton`. To keep this task independently testable, import them; they must exist first, OR stub them. Recommended sequence: do Task 10 and Task 11 before wiring them into `FindingDetail` here — reorder if executing strictly top-to-bottom by folding 10+11 in before 9's final step. The test below does not depend on their internals.

- [ ] **Step 1: Write the failing test.** Create `web/src/features/findings/FindingsView.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FindingsView } from "./FindingsView";
import { TenantProvider } from "../../tenant/TenantContext";
import type { FindingsResponse } from "../../api/types";

const data: FindingsResponse = {
  run_id: "r", count: 2,
  coverage: { attributed: 3, unattributed: 1, secrets: 1, secrets_engine: "kingfisher", sources_recovered: 0, source_map: false, files: [] },
  findings: [
    { finding_hash: "h1", type: "endpoint", value: "/api/users", path: null, severity: "info", attributes: {}, first_stage: "analyze", revealable: false, triage: null, occurrences: [] },
    { finding_hash: "h2", type: "secret", value: "aws:sha256:abcd", path: null, severity: "high", attributes: {}, first_stage: "analyze", revealable: true, triage: null, occurrences: [{ host: null, raw_url: null, source_path: "app.js", line: 5, col: 2, evidence: null, engine: "kingfisher", confidence: "high", verified: true }] },
  ],
};

describe("FindingsView", () => {
  it("shows coverage and groups findings by type without leaking a secret value", () => {
    render(<TenantProvider><FindingsView data={data} runId="r" /></TenantProvider>);
    expect(screen.getByText(/attributed/i)).toBeInTheDocument();
    expect(screen.getByText("endpoint")).toBeInTheDocument();
    expect(screen.getByText("secret")).toBeInTheDocument();
    // The hashed identity may show, but no raw secret evidence is rendered.
    expect(screen.queryByText(/BEGIN|AKIA|secret-value/)).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails.** Run: `cd web && npx vitest run src/features/findings/FindingsView.test.tsx`. Expected: FAIL.

- [ ] **Step 3: Implement `FindingDetail.tsx`.**

```tsx
import type { Finding } from "../../api/types";
import { TriageControls } from "./TriageControls";
import { RevealButton } from "./RevealButton";

export function FindingDetail({ finding, runId }: { finding: Finding; runId: string }) {
  const isSecret = finding.type === "secret";
  return (
    <div className="card">
      <div>
        <strong className={finding.severity === "high" ? "sev-high" : ""}>{finding.type}</strong>{" "}
        <span className="muted">{finding.path ?? finding.value ?? ""}</span>
      </div>
      <ul>
        {finding.occurrences.map((o, i) => (
          <li key={i} className="muted">
            {o.source_path ?? o.host ?? "?"}{o.line != null ? `:${o.line}` : ""}
            {/* evidence is server-redacted for secrets; render only when present */}
            {o.evidence && !isSecret ? ` — ${o.evidence}` : ""}
            {o.engine ? ` [${o.engine}]` : ""}
          </li>
        ))}
      </ul>
      {isSecret && finding.revealable && <RevealButton runId={runId} hash={finding.finding_hash} />}
      <TriageControls runId={runId} hash={finding.finding_hash} current={finding.triage?.status ?? "open"} />
    </div>
  );
}
```

Implement `FindingsView.tsx`:

```tsx
import type { FindingsResponse, Finding } from "../../api/types";
import { FindingDetail } from "./FindingDetail";

function groupByType(findings: Finding[]): Record<string, Finding[]> {
  const out: Record<string, Finding[]> = {};
  for (const f of findings) (out[f.type] ??= []).push(f);
  return out;
}

export function FindingsView({ data, runId }: { data: FindingsResponse; runId: string }) {
  const groups = groupByType(data.findings);
  const c = data.coverage;
  return (
    <div>
      <div className="card">
        <h3>Coverage</h3>
        {c ? (
          <p className="muted">
            attributed {c.attributed} · unattributed {c.unattributed} · secrets {c.secrets}
            {c.secrets_engine ? ` (${c.secrets_engine})` : ""} · sources {c.sources_recovered}
          </p>
        ) : <p className="muted">Coverage not available yet.</p>}
      </div>
      {Object.entries(groups).map(([type, items]) => (
        <section key={type}>
          <h3>{type} <span className="muted">({items.length})</span></h3>
          {items.map((f) => <FindingDetail key={f.finding_hash} finding={f} runId={runId} />)}
        </section>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes.** Run: `cd web && npx vitest run src/features/findings/FindingsView.test.tsx`. Expected: PASS (after Tasks 10 & 11 exist, since `FindingDetail` imports them).

- [ ] **Step 5: Commit.**

```bash
git add web/src/features/findings/FindingsView.tsx web/src/features/findings/FindingDetail.tsx web/src/features/findings/FindingsView.test.tsx
git commit -m "feat(web): findings view grouped by type with coverage; secrets stay redacted"
```

---

### Task 10: `TriageControls`

**Files:** create `web/src/features/findings/TriageControls.tsx` + `.test.tsx`.

**Interfaces:**
- Consumes: `triageFinding` (Task 4), `useTenant`.
- Produces: `<TriageControls runId hash current />` — a status select (`open`/`confirmed`/`dismissed`) + optional note → `POST …/triage`.

- [ ] **Step 1: Write the failing test.** Create `web/src/features/findings/TriageControls.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TriageControls } from "./TriageControls";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";

beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"); });

describe("TriageControls", () => {
  it("posts the selected status", async () => {
    const spy = vi.spyOn(api, "triageFinding").mockResolvedValue({ finding_hash: "h", status: "confirmed", note: null, actor: null, updated_at: "now" });
    render(<TenantProvider><TriageControls runId="r" hash="h" current="open" /></TenantProvider>);
    await userEvent.selectOptions(screen.getByLabelText(/triage/i), "confirmed");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(spy).toHaveBeenCalledWith("123e4567-e89b-12d3-a456-426614174000", "r", "h", { status: "confirmed", note: undefined });
  });
});
```

- [ ] **Step 2: Run test to verify it fails.** Run: `cd web && npx vitest run src/features/findings/TriageControls.test.tsx`. Expected: FAIL.

- [ ] **Step 3: Implement.**

```tsx
import { useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { triageFinding, ApiError } from "../../api/apiClient";
import { TRIAGE_STATUSES } from "../../api/types";

export function TriageControls({ runId, hash, current }: { runId: string; hash: string; current: string }) {
  const { tenantId } = useTenant();
  const [status, setStatus] = useState(current);
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!tenantId) return;
    try {
      const res = await triageFinding(tenantId, runId, hash, { status, note: note || undefined });
      setSaved(res.status); setError(null);
    } catch (err) { setError(err instanceof ApiError ? err.message : "Triage failed"); }
  }

  return (
    <div>
      <label htmlFor={`tr-${hash}`}>Triage</label>
      <select id={`tr-${hash}`} value={status} onChange={(e) => setStatus(e.target.value)}>
        {TRIAGE_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
      <input aria-label="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="note (optional)" />
      <button type="button" onClick={save}>Save</button>
      {saved && <span className="muted"> saved: {saved}</span>}
      {error && <span className="sev-high"> {error}</span>}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes.** Run: `cd web && npx vitest run src/features/findings/TriageControls.test.tsx`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add web/src/features/findings/TriageControls.tsx web/src/features/findings/TriageControls.test.tsx
git commit -m "feat(web): finding triage controls (open/confirmed/dismissed + note)"
```

---

### Task 11: `RevealButton`

**Files:** create `web/src/features/findings/RevealButton.tsx` + `.test.tsx`.

**Interfaces:**
- Consumes: `revealSecret`, `ApiError` (Task 4), `useTenant`.
- Produces: `<RevealButton runId hash />` — reveals the value on click, shows it once (button then disabled), and maps 409/410/422/500 to distinct messages.

- [ ] **Step 1: Write the failing test.** Create `web/src/features/findings/RevealButton.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RevealButton } from "./RevealButton";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import { ApiError } from "../../api/apiClient";

beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"); });
const ui = () => render(<TenantProvider><RevealButton runId="r" hash="h" /></TenantProvider>);

describe("RevealButton", () => {
  it("shows the value once and disables the button", async () => {
    vi.spyOn(api, "revealSecret").mockResolvedValue({ finding_hash: "h", value: "AKIA-secret" });
    ui();
    await userEvent.click(screen.getByRole("button", { name: /reveal/i }));
    expect(await screen.findByText("AKIA-secret")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /revealed/i })).toBeDisabled();
  });

  it("maps 409 integrity to a specific message", async () => {
    vi.spyOn(api, "revealSecret").mockRejectedValue(new ApiError(409, "cannot reveal secret: integrity"));
    ui();
    await userEvent.click(screen.getByRole("button", { name: /reveal/i }));
    expect(await screen.findByText(/no longer matches/i)).toBeInTheDocument();
  });

  it("maps 410 source_gone to a purged message", async () => {
    vi.spyOn(api, "revealSecret").mockRejectedValue(new ApiError(410, "cannot reveal secret: source_gone"));
    ui();
    await userEvent.click(screen.getByRole("button", { name: /reveal/i }));
    expect(await screen.findByText(/purged/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails.** Run: `cd web && npx vitest run src/features/findings/RevealButton.test.tsx`. Expected: FAIL.

- [ ] **Step 3: Implement.**

```tsx
import { useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { revealSecret, ApiError } from "../../api/apiClient";

const MESSAGES: Record<number, string> = {
  409: "The stored secret no longer matches (source changed).",
  410: "Evidence has been purged — cannot reveal.",
  422: "No stored location for this secret.",
  500: "Reveal failed — try again.",
};

export function RevealButton({ runId, hash }: { runId: string; hash: string }) {
  const { tenantId } = useTenant();
  const [value, setValue] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function reveal() {
    if (!tenantId) return;
    try {
      const res = await revealSecret(tenantId, runId, hash);
      setValue(res.value); setDone(true); setError(null);
    } catch (err) {
      if (err instanceof ApiError) setError(MESSAGES[err.status] ?? err.message);
      else setError("Reveal failed.");
    }
  }

  return (
    <div>
      <button type="button" onClick={reveal} disabled={done}>{done ? "Revealed" : "Reveal secret"}</button>
      {value && <code>{value}</code>}
      {error && <span className="sev-high"> {error}</span>}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes.** Run: `cd web && npx vitest run src/features/findings/RevealButton.test.tsx`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add web/src/features/findings/RevealButton.tsx web/src/features/findings/RevealButton.test.tsx
git commit -m "feat(web): just-in-time secret reveal with denial-code messaging"
```

---

### Task 12: Router wiring (`app.tsx` + `main.tsx`)

**Files:** create/replace `web/src/app.tsx`, `web/src/main.tsx`, create `web/src/app.test.tsx`.

**Interfaces:**
- Consumes: everything above.
- Produces: routes `/` → `NewRunPanel`, `/runs/:id` → `RunWorkspace` (RunProgress + FindingsView), all inside `TenantProvider` + `TenantGate`.

- [ ] **Step 1: Write the failing test.** Create `web/src/app.test.tsx`:

```tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { NewRunPanel } from "./features/newRun/NewRunPanel";
import { TenantProvider } from "./tenant/TenantContext";
import { TenantGate } from "./tenant/TenantGate";

beforeEach(() => localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"));

describe("app routes", () => {
  it("renders the new-run panel at /", () => {
    render(
      <TenantProvider><TenantGate>
        <MemoryRouter initialEntries={["/"]}><Routes><Route path="/" element={<NewRunPanel />} /></Routes></MemoryRouter>
      </TenantGate></TenantProvider>);
    expect(screen.getByText(/new recon run/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails.** Run: `cd web && npx vitest run src/app.test.tsx`. Expected: FAIL (files not yet in final form).

- [ ] **Step 3: Implement `app.tsx`.**

```tsx
import { useState } from "react";
import { useParams } from "react-router";
import { NewRunPanel } from "./features/newRun/NewRunPanel";
import { RunProgress } from "./features/progress/RunProgress";
import { FindingsView } from "./features/findings/FindingsView";
import type { FindingsResponse } from "./api/types";

export function RunWorkspace() {
  const { id } = useParams();
  const [findings, setFindings] = useState<FindingsResponse | null>(null);
  if (!id) return null;
  return (
    <div>
      <RunProgress runId={id} onFindings={setFindings} />
      {findings && <FindingsView data={findings} runId={id} />}
    </div>
  );
}

export function Home() { return <NewRunPanel />; }
```

Implement `main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { TenantProvider } from "./tenant/TenantContext";
import { TenantGate } from "./tenant/TenantGate";
import { Home, RunWorkspace } from "./app";
import "./styles.css";

const router = createBrowserRouter([
  { path: "/", Component: Home },
  { path: "/runs/:id", Component: RunWorkspace },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TenantProvider>
      <TenantGate>
        <RouterProvider router={router} />
      </TenantGate>
    </TenantProvider>
  </StrictMode>,
);
```

- [ ] **Step 4: Run tests + full suite + build.** Run: `cd web && npm test` (Expected: all suites PASS). Run: `npm run build` (Expected: clean `dist/`).

- [ ] **Step 5: Commit.**

```bash
git add web/src/app.tsx web/src/main.tsx web/src/app.test.tsx
git commit -m "feat(web): wire routes (/ new run · /runs/:id workspace) with react-router v7"
```

---

### Task 13: CI frontend job + Docker multi-stage build

**Files:** modify `.github/workflows/ci.yml`, `Dockerfile`, `docker-compose.yml`.

- [ ] **Step 1: Add the CI `frontend` job.** In `.github/workflows/ci.yml`, add under `jobs:`:

```yaml
  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: web/package-lock.json
      - run: npm ci
      - run: npm run lint
      - run: npm test
      - run: npm run build
```

- [ ] **Step 2: Add the Docker node build stage.** In `Dockerfile`, add before the Python stage (after the sourcemapper stage):

```dockerfile
# Build the front-end SPA (Node), copied into the runtime image below.
FROM node:20-bookworm AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build
```

After `RUN pip install .` (before switching to the non-root user), add:

```dockerfile
# Front-end build output; RECON_SPA_DIST_DIR (compose) points the API at it.
COPY --from=web-build /web/dist ./web/dist
```

- [ ] **Step 3: Point the API at the built dist.** In `docker-compose.yml`, add to `x-app-env`:

```yaml
  RECON_SPA_DIST_DIR: /app/web/dist
```

- [ ] **Step 4: Verify the build.** Run: `docker compose build api`. Expected: builds without error (node stage runs `npm ci` + `npm run build`; dist copied). Then `docker compose up -d api` and `curl -H "accept: text/html" http://localhost:8000/` returns the SPA `index.html`.

- [ ] **Step 5: Commit.**

```bash
git add .github/workflows/ci.yml Dockerfile docker-compose.yml
git commit -m "ci(web): add frontend lint/test/build job + Docker SPA build stage"
```

---

### Task 14: Review gate (§4 gate 2) + live visual walkthrough

Not a code task — the mandated closing gates. Do not mark the slice done until both pass.

- [ ] **Step 1: Higher-model code-review gate.** Hand the full slice diff to a more capable model subagent for review (CLAUDE.md §4 gate 2). Feed it: the spec, this plan, and `git diff`. Require it to check the SSE lifecycle contract, the SPA-serving fallback correctness, no-secret-before-reveal, and test quality. Fold required fixes back in (new commits) before proceeding.

- [ ] **Step 2: Bring up the real backend.** Run: `docker compose up -d` (postgres/redis/minio/migrate/api/worker). Create a tenant: `docker compose run --rm api python -m recon.bootstrap create-tenant "UI walkthrough"` and note the tenant UUID.

- [ ] **Step 3: Visual walkthrough (preview tools).** Start the dev server (`cd web && npm run dev`) or use the built image. Drive it page-by-page with the preview/browser tools: paste the tenant UUID → gate opens → fill scope host + authorized-by → upload a real JS file (one with a known fake secret + fetch/XHR calls) → watch the live SSE feed advance through stages to a terminal state → confirm findings + coverage render → triage a finding (open→confirmed) → reveal the secret (value shows once) → force an error path (e.g. reveal a second time still works server-side; confirm a mistyped tenant shows the 400 message). Capture screenshots of each surface. Confirm no secret value appears before an explicit reveal.

- [ ] **Step 4: Verify the two gate-flagged unknowns (spec §12).** Confirm the Vite dev proxy does not buffer `text/event-stream` (the feed updates incrementally, not all-at-once on completion); confirm a valid un-hyphenated UUID is accepted by the tenant gate.

- [ ] **Step 5: Update docs + memory, then push.** Update `docs/slice2-deferred-debt.md` (new UI-0 section) and the `slice3-progress`/new `slice-ui0-progress` memory. Push `main` and confirm CI (all three jobs) green.

---

## Self-Review (author's checklist)

**Spec coverage:** §3 architecture → Tasks 1, 3, 13. §4 data flow → Tasks 7 (upload), 8 (watch), 9 (orient), 10/11 (triage/reveal). §5 SSE contract → Task 5 (terminal set, clean-close status check, 3-error fallback, Last-Event-ID). §6 components → Tasks 4–12 (one per module). §7 error table → apiClient `ApiError` (Task 4) + per-surface handling (Tasks 7, 11) + Task 2 (400). §8 security → Task 6 (badge in localStorage, no URL), Tasks 9/11 (no secret before reveal), Task 5 (Accept header, not URL). §9 gate fixes → HIGH-1 Task 1, MED-3 Task 2, MED-1/2 Task 5, LOW-1 Task 3, LOW-2 Task 1 (setting), LOW-3 Task 11 (repeatable reveal, client-only "once"), LOW-4 Task 5 (mid-line split test). §10 testing → colocated Vitest across tasks + Task 14 walkthrough. §11 out-of-scope → nothing here builds 08–12.

**Placeholder scan:** none — every step has runnable code/commands.

**Type consistency:** `streamRunEvents(runId, tenantId, handlers)`, `SseHandlers.checkTerminal`, `ApiError{status, message}`, `TERMINAL_STATES`, `TRIAGE_STATUSES`, and the `api*` function signatures are used identically across Tasks 4, 5, 7, 8, 10, 11. `FindingsResponse`/`Finding`/`Coverage` shapes match `findings_router.py`.
