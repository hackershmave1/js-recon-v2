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
- Migration 0035 extends `ck_run_state` and adds `run.reanalyze_after timestamptz NULL`, with a
  partial index where it is not null.
- The accumulator marker (`capture_external_id`) alone says a capture is open: an extension that
  sends `runId` keeps it set through Analyze, and only End nulls it. The 24 h idle check uses the
  newest `run_asset.created_at`, not a new column.

### Extension contract (old extensions keep today's behaviour)

| Call | Behaviour |
|---|---|
| `POST /api/sessions/{ext}/capture/start` `{rootDomains, projectId}` | Gets or creates the session. Ends any open capture of that session (the End path). Creates the capture run (marker = the extension session id) and returns `{sessionId, runId}`. |
| `POST /api/save-files` with `metadata.runId` | Appends to that run only if it is the session's open capture. If the run is ended: a batch whose files are all already stored (url + sha) returns 200 `stored:false`, so retries are harmless; a batch with new files returns **409 `capture_ended`** (non-retryable). Without `runId` (an old extension), today's accumulator behaviour applies. If the run already has a Job, each stored batch sets `reanalyze_after=now()+10s`, in **the same transaction** as the asset rows. |
| `POST /api/sessions/{ext}/analyze/start` `{runId?, observations}` | Locks the run row (`FOR UPDATE`), so one caller wins. With a `runId`: with no Job, creates the pipeline Job and **leaves the capture open**. In `CAPTURING`: starts a pass. While a pass is running: sets `reanalyze_after=now()`. Observations are merged into one stored union every time, and every manifest carries its `requests_ref` forward. Without a `runId` (an older extension) it seals as today. |
| `POST /api/sessions/{ext}/capture/end` `{runId, observations}` | Nulls the marker and merges the final observations. Ending an empty capture deletes its run. Otherwise, depending on the run: in CAPTURING with unanalyzed files → final pass; in CAPTURING with none → DONE/PARTIAL; QUEUED with no Job → create the Job; mid-pass → its finalize decides. Idempotent. |
| `POST /api/sessions/{ext}/scope` `{add: "beta.example"}` | Adds a scope entry to the session ("Add to scope"). Normalized with `egress.normalize_scope_entry`. |
| `GET /api/capture/sessions?scope=acme.io` | **Requires a login token** (session external ids are write capabilities). Matches the normalized scope entry exactly against `session.scope_hosts`. Returns `external_id`, `session_id`, `engagement_id`, the capture count, the last capture date, and the largest capture's file and visible-endpoint counts. |
| `GET /api/sessions/{ext}/analyze/progress` | Unchanged shape. A capture in `CAPTURING` is non-terminal, so pending files read `queued`. |

### Finalize (`runs/coordinator.py`)

After CORRELATING:
- files fetched but not analyzed → `CAPTURING` with `reanalyze_after=now()`;
- else the capture is still open (marker set) → `CAPTURING`;
- else DONE/PARTIAL as today.

The spec is reclassified on every pass. Run notifications and `ended_at` happen only at the
terminal transition.

### Sweeper (`runs/capture_sweep.py`, called from the worker loop in its own try)

The worker loop already ticks about every 3 s when idle. Each tick:
- Settles resting captures with no live Job that are due (`reanalyze_after` passed) or sealed
  (marker NULL), through a guarded claim; only the row-count winner acts. Settling starts a pass
  if a file is unanalyzed, finishes a sealed capture, and otherwise just clears the schedule. The
  sealed case is the safety net for End racing a finalize. Each run is isolated, so one failing
  run never stalls the tick.
- Auto-ends idle captures: an open capture (QUEUED or CAPTURING) whose newest file is more than
  24 h old → the End path.
- Stranded-job re-publish stays the existing follow-up (`coordinator.publish_stage_job` NOTE).

### Invariants added after code review

- Every terminal transition releases the capture marker and any scheduled pass
  (`runs/service._apply_transition`), however the run ended: a failed pass, a cancel at a
  checkpoint, or the orphan reaper.
- Only one pass is queued or running per run. `capture_pass.can_start_pass` checks for a live Job
  under the run-row lock, because a queued pass leaves the run in CAPTURING.
- A batch with a `runId` re-checks, under a share lock in its own transaction, that its capture is
  still open (`capture_lifecycle.accept_batch`). Otherwise it rolls back and gets `capture_ended`.
- Pause on a CAPTURING run is refused (409). A pause flag carried into CAPTURING is cleared at
  finalize, and one the worker can't honour drops its job instead of leaving the message unacked.
- Start reuses an EMPTY open capture rather than ending (deleting) it.
- A queued pass counts as live for 30 minutes at most, and Resume cancels the job a pause left
  queued. So neither a lost publish nor a pause can hold "one pass at a time" forever.
- End or Analyze during a running pass schedules one more pass (`reanalyze_after`). Finalize then
  rests the run instead of finishing it, so late files and late observations both get analyzed.
- A cancel requested during a pass's last stage cancels at finalize.
- Batches lock the run with `FOR NO KEY UPDATE`, which still waits out End, so two concurrent
  batches can't deadlock.
- "Add to scope" requires sign-in: session scope also bounds a crawl re-run's egress.

### Read model

- `findings/queries.py` popup summary: read `capturing` runs too.
- `findings/queries.py` run diff: `capturing` counts as incomplete.
- `runs/service.request_cancel`: cancels a CAPTURING run immediately and closes it (marker nulled).
- `GET /sessions/{id}/runs`: each run gains `files` and `endpoints` (shown findings, through
  `visible_type_counts`) for the 20 newest runs. Older runs get `null`.
- Progress: a pass reports `total` = the files that pass will analyze, not every file in the run.

### Known limits (DEBT D82–D84)

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
- Sequencing: wait for End's response before the next Start, and never send End for a `runId`
  other than the current capture. Start reuses an EMPTY open capture, so a late End for that same
  run would delete it, and the new capture's batches would get `capture_ended`.
- Treat `409 capture_ended` on a batch as final, not retryable: drop the batch and show that the
  capture has ended.
- Dedup per **capture**, not per session. Otherwise a Continue run would miss files captured
  earlier in the session.
- Continue adopts the matched session's `external_id` and its engagement binding, which is
  immutable.
- New-site detection reuses the asset classifier's tracker and CDN lists.
- Removed: Past captures, "Analyze on upload". Moved to Settings: "Capture every tab".
