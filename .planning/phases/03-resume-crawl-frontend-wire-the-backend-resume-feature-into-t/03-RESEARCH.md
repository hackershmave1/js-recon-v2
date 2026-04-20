# Phase 3: Resume Crawl Frontend — Research

**Researched:** 2026-04-20
**Domain:** Vanilla JS dashboard (class-based), Bootstrap 5, axios; session row rendering patterns
**Confidence:** HIGH (all findings verified directly from codebase)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Button placement: "Continue Crawl" inline in the existing session row action bar (same `d-flex gap-2` container as Analyze All / Open Session / View Summary / Delete)
- No separate click-to-expand detail panel
- Button label: "Continue Crawl"
- Icon: `fas fa-play` or `fas fa-redo` (planner's discretion)
- Visibility conditions (ALL must be true):
  - `session.fileCount > 0`
  - `reconSessionProgress.get(session.id)` exists
  - Prior job status is NOT `queued`, `running`, or `cancelling`
- URL source: `reconState.targets?.[0]` from prior job in `reconSessionProgress`
- No modal, no user input for URL
- Options source: `reconState.options` from prior job; fall back to `collectCreateSessionPayload` defaults if absent
- Sessions with no tracked job (e.g., Chrome extension sessions) do NOT show the button
- Clicking immediately fires the POST — no confirmation modal
- Show spinner/disabled state on the button while the job starts
- On success: start polling, show recon progress badges, show alert
- Button disabled while any recon job is active (`queued`/`running`/`cancelling`)
- Button disabled while session analysis is running (same `analysisBusy` guard)

### Claude's Discretion
- Exact button color (suggest `btn-outline-info` or `btn-secondary`)
- Icon choice
- Exact ordering relative to other buttons (suggest after "Analyze All" / before "Open Session")

### Deferred Ideas (OUT OF SCOPE)
- Resume for extension-created sessions (no tracked job / no URL)
- "Resumed" asset count displayed separately in progress badges
- Resume modal with configurable options
</user_constraints>

---

## Summary

This phase adds a single "Continue Crawl" button to the session row action bar in `dashboard.js`. It is pure frontend work: no backend changes, no new API endpoints, no new data models.

The dashboard is a single `SecurityDashboard` class in `api/app/static/dashboard.js`. Session rows are rendered as template-literal HTML strings inside `renderSessionsList` (around line 2770). Button state is controlled by local variables computed before the template string (`analysisBusy`, `reconBusy`, `rowBusy`). Live DOM patches during polling go through `patchSessionReconProgressRow` and `patchSessionProgressRow`.

The backend already accepts `resume: true` in `POST /api/recon/jobs/start` (`ReconJobStartRequest.resume: bool = False`). The response shape from that endpoint is identical whether `resume` is true or false. The frontend flow for the new button is nearly identical to `submitCreateSessionFromModal`, minus session creation and modal management.

**Primary recommendation:** Add `continueCrawl(sessionId)` as a new async method, add a `data-session-continue-id` attribute for DOM patching, and extend `patchSessionReconProgressRow` to also toggle the new button's disabled state.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Button visibility logic | Frontend Client (dashboard.js) | — | Computed from `reconSessionProgress` Map and `session.fileCount` at render time |
| POST resume payload | Frontend Client (dashboard.js) | API (recon.py, read-only) | Frontend assembles and fires; backend already handles resume logic |
| Busy/disabled state during POST | Frontend Client (dashboard.js) | — | Button disabled inline; no server state needed |
| Polling after resume start | Frontend Client (dashboard.js) | — | Reuses existing `startReconJobPolling` — no changes needed |
| Badge update during polling | Frontend Client (dashboard.js) | — | Reuses `patchSessionReconProgressRow` with minor extension |

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Bootstrap | 5.1.3 | Button classes, layout utilities | Already used throughout dashboard |
| Font Awesome | 6.0.0 | Icons (`fas fa-redo`, `fas fa-play`) | Already used for all row action button icons |
| axios | CDN latest | HTTP POST to `/api/recon/jobs/start` | Already the HTTP client for all dashboard calls |

No new dependencies. This phase adds zero new libraries.

**Installation:** none required

---

## Architecture Patterns

### System Architecture Diagram

```
User clicks "Continue Crawl"
        |
        v
continueCrawl(sessionId)
        |
        +-- Guard: reconState = reconSessionProgress.get(sessionId)
        |          if missing or active status → return early
        |
        +-- Build payload: { sessionId, url: reconState.targets[0],
        |                    resume: true, ...reconState.options }
        |
        +-- Disable button (inline DOM patch via querySelector)
        |
        +-- axios.POST /api/recon/jobs/start
        |        |
        |        +-- success → reconSessionProgress.set(sessionId, job)
        |        |             startReconJobPolling(jobId, sessionId)
        |        |             showAlert("Crawl resumed...", "success")
        |        |             loadSessions() [to re-render with new state]
        |        |
        |        +-- error  → showAlert("Failed to resume...", "danger")
        |
        +-- Re-enable button (in finally block via loadSessions re-render)
```

### Recommended Project Structure

No new files needed. All changes are to:
```
api/app/static/dashboard.js   — new method + row template + patch function update
```

The `dashboard.html` template does not contain the session row HTML directly — rows are rendered via JavaScript template literals in `renderSessionsList`. No HTML file changes are required.

---

## Key Pattern Documentation (verified from source)

### Pattern 1: Session Row Button Bar Structure

**Location:** `dashboard.js` lines 2770–2860 (`renderSessionsList`)

The session row template builds these variables before the template literal:
```javascript
// [VERIFIED: dashboard.js:2774-2779]
const analysisBusy = this.runningSessionAnalyses.has(session.id) || isProgressActive;
const stopping = progressStatus === 'cancelling';
const reconState = this.reconSessionProgress.get(session.id) || null;
const reconStatus = String(reconState?.status || '').toLowerCase();
const reconBusy = ['queued', 'running', 'cancelling'].includes(reconStatus);
const rowBusy = analysisBusy || reconBusy;
```

The button bar container (line 2833):
```html
<!-- [VERIFIED: dashboard.js:2833] -->
<div class="d-flex align-items-center flex-wrap gap-2" style="flex-shrink:0">
  <button class="btn btn-success btn-sm" data-session-analyze-id="${session.id}" ...>
  <button class="btn btn-warning btn-sm ${analysisBusy ? '' : 'd-none'}" data-session-stop-id="${session.id}" ...>
  <button class="btn btn-primary btn-sm" onclick="dashboard.openSessionFiles(...)">
  <button class="btn btn-outline-primary btn-sm" ...>View Summary</button>
  <!-- OpenAPI download button, conditional -->
  <button class="btn btn-outline-danger btn-sm" data-session-delete-id="${session.id}" ...>Delete</button>
</div>
```

The "Continue Crawl" button inserts into this same `d-flex gap-2` container.

### Pattern 2: Visibility Condition for the New Button

The button must be hidden when:
- `session.fileCount <= 0` — no captured assets
- No prior recon job: `!reconState`
- Job is currently active: `reconBusy` is true (status is `queued`/`running`/`cancelling`)

Combined in template literal:
```javascript
// [VERIFIED: derived from dashboard.js:2774-2779 + CONTEXT.md decisions]
const showContinueCrawl = (Number(session.fileCount) > 0)
    && reconState
    && !reconBusy;
```

Then in template:
```javascript
${showContinueCrawl ? `
  <button class="btn btn-outline-info btn-sm"
          data-session-continue-id="${session.id}"
          onclick="dashboard.continueCrawl('${session.id}')">
    <i class="fas fa-redo me-1"></i>Continue Crawl
  </button>` : ''}
```

### Pattern 3: POST Payload Construction

`collectCreateSessionPayload` builds a full payload from form DOM inputs. For the resume case there is no form — the payload is assembled programmatically from `reconState`:

```javascript
// [VERIFIED: dashboard.js:149-170 build_job_state; dashboard.js:798-833 collectCreateSessionPayload]
// reconState.options keys exactly match ReconJobStartRequest field names (camelCase):
// { sameOriginOnly, maxAssets, maxDepth, discoveryEngine, includeSourceMaps,
//   performAnalysis, waitAfterLoadMs, timeoutSeconds, maxResponseBytes }
// reconState.targets[0] is the URL string.

const payload = {
    sessionId: sessionId,           // existing session — no session creation
    url: reconState.targets?.[0],   // required by backend
    resume: true,                   // the new field
    // spread saved options (or fall back to collectCreateSessionPayload defaults)
    discoveryEngine:  reconState.options?.discoveryEngine  || 'katana',
    sameOriginOnly:   reconState.options?.sameOriginOnly   ?? true,
    includeSourceMaps: reconState.options?.includeSourceMaps ?? true,
    performAnalysis:  reconState.options?.performAnalysis  ?? true,
    maxAssets:        reconState.options?.maxAssets        || 500,
    maxDepth:         reconState.options?.maxDepth         ?? 3,
    timeoutSeconds:   reconState.options?.timeoutSeconds   || 20,
    waitAfterLoadMs:  reconState.options?.waitAfterLoadMs  ?? 2500,
    maxResponseBytes: reconState.options?.maxResponseBytes || (12 * 1024 * 1024),
};
```

Note: `sessionName` is intentionally omitted — the session already exists and has a name.

### Pattern 4: One-Click Async Action with Inline Busy State

The dashboard does NOT have a generalized per-row busy Set for "start recon" actions. Existing one-click actions (`deleteSession`, `stopSessionAnalysis`) do not track their own busy state in a Set — they rely on the row re-rendering from `loadSessions()` after completion.

For `continueCrawl`, the cleanest approach matching the codebase style:

1. **On click:** Immediately disable the button via `querySelector` before awaiting the POST.
2. **In finally:** Call `loadSessions()` which re-renders the entire row (button is re-enabled or hidden, depending on the new `reconBusy` state from the now-active job).
3. No dedicated `continueCrawlBusy` Set is needed — the job becoming `queued` in `reconSessionProgress` naturally hides/disables the button on the next render.

```javascript
// [VERIFIED: pattern derived from deleteSession (dashboard.js:3504) and
//            submitCreateSessionFromModal (dashboard.js:835)]
async continueCrawl(sessionId) {
    if (!sessionId) return;
    const reconState = this.reconSessionProgress.get(sessionId) || null;
    if (!reconState) return;
    const reconStatus = String(reconState.status || '').toLowerCase();
    if (['queued', 'running', 'cancelling'].includes(reconStatus)) return;
    const url = reconState.targets?.[0];
    if (!url) {
        this.showAlert('Cannot resume: no target URL in prior crawl record.', 'warning');
        return;
    }

    // Disable button immediately (inline DOM patch)
    const btn = document.querySelector(`[data-session-continue-id="${sessionId}"]`);
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Starting...'; }

    try {
        const payload = {
            sessionId,
            url,
            resume: true,
            discoveryEngine:   reconState.options?.discoveryEngine   || 'katana',
            sameOriginOnly:    reconState.options?.sameOriginOnly    ?? true,
            includeSourceMaps: reconState.options?.includeSourceMaps ?? true,
            performAnalysis:   reconState.options?.performAnalysis   ?? true,
            maxAssets:         reconState.options?.maxAssets         || 500,
            maxDepth:          reconState.options?.maxDepth          ?? 3,
            timeoutSeconds:    reconState.options?.timeoutSeconds    || 20,
            waitAfterLoadMs:   reconState.options?.waitAfterLoadMs   ?? 2500,
            maxResponseBytes:  reconState.options?.maxResponseBytes  || (12 * 1024 * 1024),
        };
        const response = await axios.post(`${this.apiBase}/api/recon/jobs/start`, payload);
        const data = response.data || {};
        const jobId = data.jobId;
        const job = data.job || null;
        if (job && sessionId) {
            this.reconSessionProgress.set(sessionId, job);
        }
        if (jobId) {
            this.startReconJobPolling(jobId, sessionId);
        }
        this.showAlert(`Crawl resumed for session ${this.shortId(sessionId)}.`, 'success');
    } catch (error) {
        const detail = error?.response?.data?.detail;
        const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
        this.showAlert(`Failed to resume crawl: ${message}`, 'danger');
    } finally {
        if (this.activeTab === 'sessions') {
            await this.loadSessions();   // re-renders row; button visibility corrects itself
        }
    }
}
```

### Pattern 5: `patchSessionReconProgressRow` — Extension Required

`patchSessionReconProgressRow` is called during polling ticks to update badge HTML and toggle the delete button disabled state. It does NOT currently touch a "Continue Crawl" button because none exists.

**The new button must be added to this patch function** so that:
- When polling moves a job to `queued`/`running`, the "Continue Crawl" button is hidden/disabled (without waiting for a full `loadSessions()` re-render).
- When polling moves a job to a terminal state, the "Continue Crawl" button may become visible again (requires `loadSessions()` since it conditionally renders the button).

```javascript
// [VERIFIED: dashboard.js:3428-3443 — current patchSessionReconProgressRow]
// Add this block after line 3440 (inside the existing reconBusy check):
const continueBtn = document.querySelector(`[data-session-continue-id="${sessionId}"]`);
if (continueBtn) {
    continueBtn.disabled = reconBusy;
    if (reconBusy) {
        continueBtn.classList.add('d-none');
    }
    // Note: making it visible again requires a full re-render via loadSessions()
    // because the button's existence is conditional in the template literal.
    // The polling tick already calls loadSessions() on terminal states.
}
```

### Pattern 6: `renderReconProgressBadges` — Behavior for Stopped Jobs

`renderReconProgressBadges` at line 2911 shows badges for ALL non-idle/non-empty states including `failed` and `cancelled`:
- `failed` → `<span class="badge bg-danger ...">Crawl failed</span>` + coverage counts
- `cancelled` → `<span class="badge bg-secondary ...">Crawl stopped</span>` + coverage counts

So when `reconState` exists with `status === 'failed'` or `status === 'cancelled'`, the recon badges already show useful historical information. The "Continue Crawl" button appears alongside those badges — this is intentional (you can resume a failed or stopped crawl).

### Pattern 7: `refreshActiveReconJobs` — What Fields Land in `reconSessionProgress`

`refreshActiveReconJobs` (line 1983) calls `GET /api/recon/jobs`, gets a flat `jobs` array, and uses the most-recently-updated job per `sessionId`. The job object stored in the Map comes from `get_public_job_snapshot` (recon.py:182) which returns `state_json` with these top-level keys:

```javascript
// [VERIFIED: recon.py:149-179 build_job_state + get_public_job_snapshot]
{
  jobId:       string,
  status:      'queued' | 'running' | 'cancelling' | 'completed' | 'failed' | 'cancelled',
  sessionId:   string,
  targets:     string[],   // array of URLs; targets[0] is the crawl start URL
  options: {
    sameOriginOnly:    bool,
    maxAssets:         int,
    maxDepth:          int,
    discoveryEngine:   string,
    includeSourceMaps: bool,
    performAnalysis:   bool,
    waitAfterLoadMs:   int,
    timeoutSeconds:    int,
    maxResponseBytes:  int,
  },
  assets:      object[],   // sorted by discoveredAt; patched to array by get_public_job_snapshot
  assetCount:  int,        // len(assets) added by get_public_job_snapshot
  coverage:    { discovered_js, fetched_js, map_detected, map_fetched, ... },
  summary:     { stored, fileIds, cancelled },
  error:       string | null,
  createdAt:   string,
  startedAt:   string | null,
  finishedAt:  string | null,
}
```

Key observation: `targets` (plural) is the array field name. `reconState.targets?.[0]` correctly retrieves the first target URL. `reconState.options` contains all the crawl settings in camelCase matching `ReconJobStartRequest` field names exactly.

### Pattern 8: `setCreateSessionModalBusy` — How Modal Busy Works (for contrast)

`setCreateSessionModalBusy` (line 770) disables the submit button and all modal close buttons, then shows a spinner in the submit button label. This pattern is for modal-based flows only.

For the inline "Continue Crawl" button (no modal), the appropriate pattern is the direct `querySelector` approach shown in Pattern 4, with `loadSessions()` in `finally` to restore the row to correct state.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Job polling after resume | Custom polling loop | `startReconJobPolling(jobId, sessionId)` | Already exists, handles dedup, 2s interval, terminal-state cleanup |
| Badge rendering after resume | Custom badge update | `renderReconProgressBadges(reconState)` + `patchSessionReconProgressRow` | Already handles all status classes and coverage counts |
| Error message extraction | Custom error string | `error?.response?.data?.detail` pattern | Used consistently at every axios catch site in dashboard.js |
| Session reload after action | Custom DOM manipulation | `loadSessions()` | Re-renders entire session list cleanly; row state corrects itself |

---

## Common Pitfalls

### Pitfall 1: `reconSessionProgress` Cleared on Terminal State
**What goes wrong:** `reconSessionProgress.delete(sessionId)` is called in `startReconJobPolling` when the job reaches `completed`/`failed`/`cancelled`. After that, `reconSessionProgress.get(sessionId)` returns `undefined`. The "Continue Crawl" button must only be shown when `reconState` is truthy.
**Why it happens:** `refreshActiveReconJobs` is called at page load and repopulates the Map from the DB. But between a terminal event firing and the next `refreshActiveReconJobs` call, the entry may be absent.
**How to avoid:** The button's visibility condition already requires `reconState` to be truthy. After a terminal state, `loadSessions()` is called which calls `refreshActiveReconJobs` first (line 1972-1973), so the Map is repopulated before the next render. This is safe.

### Pitfall 2: Stale `reconState` After Button Click
**What goes wrong:** User clicks "Continue Crawl", but between reading `reconState` and posting, the job state changed (e.g., another tab started a new crawl).
**Why it happens:** `reconSessionProgress` is a local in-memory Map. It can be stale.
**How to avoid:** The guard `if (['queued', 'running', 'cancelling'].includes(reconStatus)) return;` at the start of `continueCrawl` prevents double-starting. The backend will also reject a second concurrent job gracefully.

### Pitfall 3: Missing `data-session-continue-id` in `patchSessionReconProgressRow`
**What goes wrong:** Polling ticks update badges and disable delete button, but the "Continue Crawl" button stays enabled while a crawl is running (it was rendered enabled before the poll fired).
**Why it happens:** `patchSessionReconProgressRow` only knows about `[data-session-recon-id]` and `[data-session-delete-id]`. The new button needs its own `data-session-continue-id` attribute and a disable/hide step in the patch function.
**How to avoid:** Add `data-session-continue-id="${session.id}"` to the button HTML and extend `patchSessionReconProgressRow` as shown in Pattern 5.

### Pitfall 4: Button Ordering vs. Conditional Rendering
**What goes wrong:** The button is conditionally rendered (`showContinueCrawl ? ... : ''`). If the condition evaluates to `false` on initial render but becomes `true` during polling, the button never appears until `loadSessions()` is called.
**Why it happens:** Template literals don't re-evaluate conditions when state changes; only a full row re-render (via `loadSessions()`) can make the button appear.
**How to avoid:** On terminal-state polling tick, `startReconJobPolling` already calls `loadSessions()` (line 914-916). This ensures the button appears after a crawl finishes.

### Pitfall 5: `options` Keys May Be Absent on Old Job Records
**What goes wrong:** `reconState.options` may be missing certain keys if the job was created before the current schema.
**Why it happens:** Job state_json was written at job creation time; older records may lack newer option fields.
**How to avoid:** Use `?? defaultValue` or `|| defaultValue` fallbacks for every option field, exactly as `collectCreateSessionPayload` does for form defaults.

### Pitfall 6: `targets[0]` Absent
**What goes wrong:** `reconState.targets` is an empty array or undefined.
**Why it happens:** Unlikely but possible if state_json was malformed or migrated.
**How to avoid:** Guard explicitly: `const url = reconState.targets?.[0]; if (!url) { this.showAlert(...); return; }`. Already included in Pattern 4 example.

---

## Code Examples

### Full `reconSessionProgress` job object shape
```javascript
// [VERIFIED: recon.py:149-179, get_public_job_snapshot:182-196]
{
  jobId: "550e8400-...",
  status: "failed",               // terminal — eligible for resume
  sessionId: "abc123...",
  targets: ["https://example.com"],
  options: {
    sameOriginOnly: true,
    maxAssets: 500,
    maxDepth: 3,
    discoveryEngine: "katana",
    includeSourceMaps: true,
    performAnalysis: true,
    waitAfterLoadMs: 2500,
    timeoutSeconds: 20,
    maxResponseBytes: 12582912,
  },
  assets: [...],
  assetCount: 47,
  coverage: { discovered_js: 12, fetched_js: 10, map_detected: 3, map_fetched: 2, ... },
  summary: { stored: 47, fileIds: [...], cancelled: false },
  error: "connection timeout",
  createdAt: "2026-04-20T10:00:00",
  startedAt: "2026-04-20T10:00:01",
  finishedAt: "2026-04-20T10:05:22",
}
```

### Completed button bar template (showing new button in context)
```javascript
// [VERIFIED: dashboard.js:2833-2856 for existing buttons; new button per CONTEXT.md decisions]
const showContinueCrawl = (Number(session.fileCount) > 0) && reconState && !reconBusy;

// Inside the d-flex gap-2 container:
`<button class="btn btn-success btn-sm" data-session-analyze-id="${session.id}" ${analysisBusy ? 'disabled' : ''} onclick="dashboard.analyzeSession('${session.id}')">
    <i class="fas fa-bolt me-1"></i>${analysisBusy ? (stopping ? 'Stopping...' : 'Analyzing...') : 'Analyze All'}
</button>
<button class="btn btn-warning btn-sm ${analysisBusy ? '' : 'd-none'}" data-session-stop-id="${session.id}" ${stopping ? 'disabled' : ''} onclick="dashboard.stopSessionAnalysis('${session.id}')">
    <i class="fas fa-stop me-1"></i>${stopping ? 'Stopping...' : 'Stop'}
</button>
${showContinueCrawl ? `
<button class="btn btn-outline-info btn-sm"
        data-session-continue-id="${session.id}"
        onclick="dashboard.continueCrawl('${session.id}')">
    <i class="fas fa-redo me-1"></i>Continue Crawl
</button>` : ''}
<button class="btn btn-primary btn-sm" onclick="dashboard.openSessionFiles('${session.id}', '${encodedName}')">
    <i class="fas fa-folder-open me-1"></i>Open Session
</button>
...`
```

---

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| In-memory RECON_JOBS dict (Phase 1) | DB-backed Job model (Phase 1 complete) | `refreshActiveReconJobs` can restore `reconSessionProgress` across page reloads — resume feature works correctly after browser refresh |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `options` keys in `reconState.options` are camelCase matching `ReconJobStartRequest` field names exactly | Pattern 3, Code Examples | If key names differ (e.g., `include_source_maps` vs `includeSourceMaps`), payload would send undefined/null. Verified by cross-checking `build_job_state` (recon.py:160-170) with `ReconJobStartRequest` (recon.py:41-55) — they match. | [VERIFIED: recon.py:41-55, 160-170] |

**If this table is empty of un-verified items:** All claims were verified directly from source code in this session.

---

## Open Questions

1. **Should the button label change to "Resuming..." while the POST is in flight?**
   - What we know: `continueCrawl` disables the button and patches its innerHTML to `<i class="fas fa-spinner fa-spin me-1"></i>Starting...` before awaiting the POST.
   - What's unclear: User has not specified a loading label — "Starting..." is inferred from the pattern.
   - Recommendation: Use "Starting..." or "Resuming..." — planner's discretion; user said show spinner/disabled state.

2. **Should `continueCrawl` call `loadSessions()` on success regardless of active tab?**
   - What we know: `submitCreateSessionFromModal` always calls `loadSessions()` after success and `this.switchTab('sessions')`. `deleteSession` calls `loadSessions()` only when `activeTab === 'sessions'`.
   - What's unclear: Since the button only appears on the sessions tab, `activeTab` will always be `'sessions'` when clicked.
   - Recommendation: Always call `loadSessions()` in `finally` — the tab guard is unnecessary here but harmless either way.

---

## Environment Availability

Step 2.6: SKIPPED — this phase is purely frontend JS/HTML changes. No external dependencies beyond the existing Node/browser environment.

---

## Validation Architecture

No `config.json` found in `.planning/` — treating `nyquist_validation` as enabled.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | None detected (no jest.config, vitest.config, pytest for JS) |
| Config file | None — see Wave 0 |
| Quick run command | Open dashboard in browser, inspect session row |
| Full suite command | Manual smoke test: start crawl, let it finish, verify "Continue Crawl" appears |

This is a UI-only phase with no existing JS test harness. All validation is manual browser testing.

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| — | Button visible when session has files + prior stopped job | manual-only | N/A — no JS test harness | N/A |
| — | Button hidden when job is active | manual-only | N/A | N/A |
| — | POST fires with correct payload including `resume: true` | manual-only (browser network tab) | N/A | N/A |
| — | Spinner shown while POST in flight | manual-only | N/A | N/A |
| — | Polling starts after success | manual-only | N/A | N/A |
| — | Alert shown on success and failure | manual-only | N/A | N/A |

### Wave 0 Gaps
No automated test infrastructure exists for dashboard.js. The planner may wish to add a brief smoke-test checklist to the plan's verification step rather than building a JS test framework.

---

## Security Domain

This phase adds no new endpoints, no authentication, no user input beyond a button click. The `url` sent in the POST comes from `reconState.targets[0]` which was already validated by `SecurityValidator.validate_url` when the original job was created. No new security controls are required.

ASVS V5 (Input Validation): The `url` value originates from a DB-backed job record, not from user DOM input. The backend re-validates it via `SecurityValidator.validate_url` on every `POST /api/recon/jobs/start` call regardless. No additional frontend sanitization needed.

---

## Sources

### Primary (HIGH confidence)
- `api/app/static/dashboard.js` — Full file read; lines 1-55 (constructor), 798-874 (payload + submit), 876-957 (polling), 1983-2013 (refreshActiveReconJobs), 2770-2866 (renderSessionsList), 2911-2951 (renderReconProgressBadges), 3400-3443 (patchSessionProgressRow + patchSessionReconProgressRow)
- `api/app/api/routes/recon.py` — Lines 41-55 (ReconJobStartRequest), 149-196 (build_job_state + get_public_job_snapshot), 344-460 (start_recon_job handler)
- `.planning/phases/03-*/03-CONTEXT.md` — Locked decisions, discretion areas, deferred items

### Secondary (MEDIUM confidence)
- `api/app/templates/dashboard.html` — Confirmed session rows are JS-rendered (template has no static session row HTML)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies; all libraries verified in existing HTML/JS
- Architecture: HIGH — all patterns verified directly from source code line numbers
- Pitfalls: HIGH — derived from actual code paths, not speculation
- Payload field names: HIGH — cross-verified `build_job_state` (recon.py) against `ReconJobStartRequest` fields

**Research date:** 2026-04-20
**Valid until:** 2026-05-20 (stable codebase; no fast-moving dependencies)
