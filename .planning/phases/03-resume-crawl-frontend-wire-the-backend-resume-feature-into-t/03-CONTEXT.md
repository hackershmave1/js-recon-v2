# Phase 3: Resume Crawl Frontend — Context

**Gathered:** 2026-04-20
**Status:** Ready for planning
**Source:** User discussion

<domain>
## Phase Boundary

Wire the backend resume feature into the dashboard UI. The backend already accepts
`resume: true` on `POST /api/recon/jobs/start` and handles all skip logic internally.
This phase is pure frontend — no backend changes needed.

</domain>

<decisions>
## Implementation Decisions

### Button placement
- **Locked:** Add "Continue Crawl" button inline in the existing session row action bar
  (same `d-flex gap-2` container as Analyze All / Open Session / View Summary / Delete)
- User explicitly does not want a separate click-to-expand detail panel
- Button lives next to the other action buttons, not in a modal or separate view

### Button label
- **Locked:** "Continue Crawl" (user phrasing: "Continue session run")
- Icon: `fas fa-play` or `fas fa-redo` (planner's discretion)

### Visibility conditions (ALL must be true to show the button)
- `session.fileCount > 0` — session has captured assets
- `reconSessionProgress.get(session.id)` exists — there is a prior tracked recon job
- Prior job's status is NOT one of: `queued`, `running`, `cancelling` — i.e., job is stopped

### URL source
- **Locked:** Pull `targets[0]` from the prior job object in `reconSessionProgress`
  (`reconState.targets?.[0]`)
- No modal, no user input for URL — URL comes from the prior job record

### Options source
- Reuse `reconState.options` from the prior job (maxAssets, maxDepth, discoveryEngine, etc.)
- Fall back to defaults from `collectCreateSessionPayload` defaults if options absent

### Edge case: session with no tracked job
- Sessions created via Chrome extension (no recon job in DB) do not show the button
- No URL is known for these sessions, so resume is not offered
- This is intentional — do not add a fallback URL-prompt modal

### No-modal flow
- Clicking "Continue Crawl" immediately fires the POST (same pattern as other row buttons)
- No confirmation modal (the action is non-destructive — it only adds new files)
- Show spinner/disabled state on the button while the job starts (same as other action buttons)
- On success: start polling, show recon progress badges, show alert

### Button disabled states
- Disabled while any recon job for this session is active (`queued`/`running`/`cancelling`)
- Disabled while session analysis is running (same `analysisBusy` guard used by Analyze All)

### Claude's Discretion
- Exact button color (suggest `btn-outline-info` or `btn-secondary` to avoid crowding the primary/success colors)
- Icon choice
- Exact ordering relative to other buttons (suggest after "Analyze All" / before "Open Session")

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Frontend source files
- `api/app/static/dashboard.js` — main dashboard JS; session row rendering at line ~2799; `submitCreateSessionFromModal` at ~835; `startReconJobPolling` at ~876; `reconSessionProgress` map; `renderReconProgressBadges` at ~2911
- `api/app/templates/dashboard.html` — session row HTML template

### Backend (read-only reference — no changes needed)
- `api/app/api/routes/recon.py` — `ReconJobStartRequest` (has `resume: bool = False`); `start_recon_job` handler at line ~344
- `api/app/services/recon_job_runner.py` — `ReconRunnerOptions.resume_skip_urls`

</canonical_refs>

<specifics>
## Specific Implementation Notes

- `reconSessionProgress` is a `Map<sessionId, jobSnapshot>` loaded at startup via `refreshActiveReconJobs`
- Each job snapshot has: `status`, `targets` (array of URLs), `options` (crawl settings), `sessionId`, `jobId`, `coverage`, `assets`
- The resume POST payload mirrors `submitCreateSessionFromModal` but sets `resume: true` and skips session creation (session already exists)
- After POST, call `startReconJobPolling(jobId, sessionId)` — same pattern as new crawl
- The session row's button bar renders inside `renderSessionsList` — the new button is added to the same template literal

</specifics>

<deferred>
## Deferred Ideas

- Resume for extension-created sessions (no tracked job / no URL) — deferred, not in scope
- "Resumed" asset count displayed separately in progress badges — nice-to-have, defer unless trivial
- Resume modal with configurable options (max assets, depth etc.) — user wants one-click, not a modal

</deferred>

---

*Phase: 03-resume-crawl-frontend*
*Context gathered: 2026-04-20 via user discussion*
