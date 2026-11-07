# Capture lifecycle — design

Status: agreed 2026-11-07 (map #174, resolution on #178). Journeys signed off by the researcher.
Placeholders only: `acme.io`, `beta.example`.

## Problem

1. **Analyze ends the capture.** `analyze/start` seals the run (`api/capture_router.py` nulls
   `Run.capture_external_id`). A file arriving seconds later opens a new run holding just that file.
   That run sits `queued` forever and becomes the Sessions card's "latest run".
2. **Captures land in the wrong session.** The extension keeps one session id until "New session"
   (`modules/capture-session.js` `restore()`). A capture of a new target appends to a months-old
   session for another target. Its scope never updates (`seed_scope_hosts_if_empty`), so every
   host reads out of scope.
3. **Scope lives in Settings** and outlives the capture it was set for.

## What the researcher gets

| Moment | Behaviour |
|---|---|
| Idle popup | No scope. "Use app.acme.io as scope → acme.io and all its subdomains", or type a domain. Start is disabled until a scope is set. |
| Scope set | "Capture into": **Continue "acme.io"** (pre-selected when a session for that scope exists; shows its capture count, last date, largest capture's files and endpoints) or **New session for acme.io**. Optional engagement. |
| Start capture | A new **capture** (one run) begins. The header shows the target, elapsed time and file count. |
| Analyze now | Analyzes what has arrived; **capture continues**. Files arriving later join the same capture; once uploads have been quiet for 10 s the analysis updates itself ("Updated with 3 new files"). |
| Pause / Resume | Stops or starts listening. The timer stops. Analyze still works, and uploads in flight finish. |
| New site seen | When JS loads from a site outside scope (known analytics/CDN hosts excluded), a badge appears: **Add to scope**, or **End + start new** (pre-fills the new site as the next scope). Capture is never blocked. An unanswered badge stays, and the toolbar icon shows a dot. |
| End capture | No confirmation. Waits for queued uploads, runs the final analysis, turns the run Done (one notification), and clears the scope. The "Capture ended" banner links to the workspace. |
| Browser restart | The capture resumes **paused** with its scope; queued files upload. |
| Forgotten capture | 24 h with no new files, paused or not, ends it automatically, as if End were pressed. |
| Workspace run switcher | Each capture shows its endpoint count, a bar relative to the session's largest capture, and a square copy-run-ID button. An unfinished capture is marked "capturing" and updates live. |

Removed: the popup's "Past captures" list and the "Analyze on upload" toggle. "Capture every tab"
moves to Settings only, behind its existing confirmation.

## Terms (GLOSSARY)

- **Session**: one target and its session scope; can be continued.
- **Capture**: one run of a capture session, from Start to End (or auto-end).
- **Capturing** (run state): a capture between analysis passes. It is non-terminal and not active,
  findings are readable, and more files may arrive.

## Server design (PR 1)

The rejected alternative, reopening a terminal run (DONE→QUEUED), is out because terminal-is-final
is load-bearing:
- the SSE replay and keep-alive (`runs_router.py`);
- `sseClient.ts` and the `runData.tsx` reducer ("terminal is absorbing");
- `runNotify.ts`, which would notify on every pass;
- the Spec and Probe page gates in `App.tsx`;
- readers of the "latest terminal run";
- `ended_at` and completeness;
- REQ-D5 "runs are immutable snapshots".

### State

- `RunState.CAPTURING`, which is in neither `TERMINAL_STATES` nor `ACTIVE_STATES`.
- New transitions:
  - `CORRELATING → CAPTURING`: a pass ends while the capture is open, or while files are still
    unanalyzed.
  - `CAPTURING → DISCOVERING`: the next pass starts.
  - `CAPTURING → DONE | PARTIAL`: End with nothing new to analyze.
  - `CAPTURING → CANCELLED | FAILED`.
- Terminal states keep no exits.
- Migration 0035 extends `ck_run_state` and adds three columns:
  - `run.reanalyze_after timestamptz NULL`, with a partial index where it is not null;
  - `run.last_capture_at timestamptz NULL`;
  - `run.capture_open boolean NOT NULL DEFAULT false`, the explicit capture discriminator once the
    marker is cleared.

### Extension contract (old extensions keep today's behaviour)

| Call | Behaviour |
|---|---|
| `POST /api/sessions/{ext}/capture/start` `{rootDomains, projectId}` | Gets or creates the session. Ends any open capture of that session (the End path). Creates the capture run with `capture_open=true` and returns `{sessionId, runId}`. |
| `POST /api/save-files` with `metadata.runId` | Appends to that run only if it is the session's open capture. If the run is ended: a batch whose files are all already stored (url + sha) returns 200 `stored:false`, so retries are harmless; a batch with new files returns **409 `capture_ended`** (non-retryable). Without `runId` (an old extension), today's accumulator behaviour applies. Each stored batch sets `last_capture_at=now()` and, if the run already has a Job, `reanalyze_after=now()+10s`, in **the same transaction** as the asset rows. |
| `POST /api/sessions/{ext}/analyze/start` `{runId?, observations}` | Locks the run row (`FOR UPDATE`), so one caller wins. With no Job: creates the pipeline Job and **leaves the capture open** (for a `capture_open` run). In `CAPTURING`: starts a pass. While a pass is running: sets `reanalyze_after=now()`. Observations are merged into one stored union every time, and every manifest carries its `requests_ref` forward. A run without `capture_open` (old extension) seals as today. |
| `POST /api/sessions/{ext}/capture/end` `{runId, observations}` | Sets `capture_open=false` and merges the final observations. Then, depending on the run: in CAPTURING with unanalyzed files → final pass; in CAPTURING with none → DONE/PARTIAL; QUEUED with no Job → create the Job; mid-pass → its finalize decides. Idempotent. |
| `POST /api/sessions/{ext}/scope` `{add: "beta.example"}` | Adds a scope entry to the session ("Add to scope"). Normalized with `egress.normalize_scope_entry`. |
| `GET /api/capture/sessions?scope=acme.io` | **Requires a login token** (session external ids are write capabilities). Matches the normalized scope entry exactly against `session.scope_hosts`. Returns `external_id`, `session_id`, `engagement_id`, the capture count, the last capture date, and the largest capture's file and visible-endpoint counts. |
| `GET /api/sessions/{ext}/analyze/progress` | Unchanged shape. A capture in `CAPTURING` is non-terminal, so pending files read `queued`. |

### Finalize (`runs/coordinator.py`)

After CORRELATING:
- files fetched but not analyzed → `CAPTURING` with `reanalyze_after=now()`;
- else `capture_open` → `CAPTURING`;
- else DONE/PARTIAL as today.

The spec is reclassified on every pass. Run notifications and `ended_at` happen only at the
terminal transition.

### Sweeper (`worker/main.py`, beside `reap_orphaned_jobs`)

The worker loop already ticks about every 3 s when idle. Each tick:
- Claims due passes with a guarded update: `SET reanalyze_after=NULL WHERE state='capturing' AND
  reanalyze_after < now()`. Only the row-count winner inserts the DISCOVERING Job, and publishes
  after commit.
- Auto-ends idle captures: `capture_open AND last_capture_at < now() - 24h` → the End path.
- Re-publishes stranded jobs: a run that is QUEUED or CAPTURING with a Job that has no message.

### Read model

- `findings/queries.py` popup summary: read `capturing` runs too.
- `findings/queries.py` run diff: `capturing` counts as incomplete.
- `runs/service.request_cancel`: cancels a CAPTURING run immediately.
- `GET /sessions/{id}/runs`: each run gains `files` and `endpoints` (shown findings, through
  `visible_type_counts`) for the 20 newest runs. Older runs get `null`.
- Progress: a pass reports `total` = the files that pass will analyze, not every file in the run.

### Known limits (DEBT entries)

- An old file analyzed in an earlier pass does not re-resolve against exports from a file that
  arrived later. This is narrow, because static ESM imports load before their importers. The fix is
  a stored cross-module index merged per pass.
- Each pass re-reads every file to build the cross-module index (D28 double recovery), and
  correlate re-scans all request bodies.
- A deploy that bumps `FINDING_HASH_VERSION` during an open capture duplicates findings across that
  boundary. The 24 h auto-end bounds the window.

## Web (PR 2)

- Run switcher (`features/progress/SessionRunsSwitcher.tsx`): endpoints, a bar against the
  session's largest capture, a copy-run-ID square (the ID is not shown; a toast confirms), and a
  "capturing" mark.
- `capturing` state:
  - `runData.tsx` `supersedes()` treats it like `paused`, as the start of a new cycle;
  - the Spec and Probe gates and the "receiving" chip treat `capturing` as readable and receiving;
  - the run header reads "Capturing · listening";
  - `sseClient` and `runNotify` are unchanged (a single terminal event at End).

## Extension (PR 3)

- Popup per the journeys:
  - scope-first idle card;
  - Capture into (Continue or New);
  - live header;
  - Pause / Resume / End capture;
  - new-site badge plus a toolbar dot;
  - End waits for uploads;
  - the scope clears at End;
  - restart resumes paused.
- Dedup per **capture**, not per session. Otherwise a Continue run would miss files captured
  earlier in the session.
- Continue adopts the matched session's `external_id` and its engagement binding, which is
  immutable.
- New-site detection reuses the asset classifier's tracker and CDN lists.
- Removed: Past captures, "Analyze on upload". Moved to Settings: "Capture every tab".
