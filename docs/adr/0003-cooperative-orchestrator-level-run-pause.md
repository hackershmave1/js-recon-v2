---
status: accepted
date: 2026-08-08
---

# 3. Cooperative, orchestrator-level run pause (not OS signal-stop)

## Context and Problem Statement

A run is a long, multi-stage state machine
(`queued→discovering→fetching→ingesting→analyzing→correlating→done`). An operator needs
to pause and later resume a run without losing work or corrupting state, and a paused run
must survive a worker restart/redeploy. How should "pause" be implemented (REQ-A4)?

## Considered Options

* **Cooperative pause at the orchestrator** — a persisted `pause_requested` flag the
  worker polls at safe checkpoints, then exits the stage cleanly and records where to
  resume.
* **OS signal-stop the worker** (`SIGSTOP`/`SIGTSTP` the process/container).
* **No pause** — support only cancel.

## Decision Outcome

Chosen option: **cooperative, orchestrator-level pause.** `request_pause` sets a persisted
flag; the worker checks control flags at safe checkpoints (before a stage, between
per-asset steps) and raises a non-failure `ControlInterrupt` that transitions the run to
`paused`; `resume` re-enqueues from `resumed_from_stage`. Only crawl stages (katana)
genuinely checkpoint mid-stage, so a fast single-file run may reach a terminal state
before the pause is observed — pause is best-effort by design, not a hard preempt.

### Consequences

* Good — a paused run's state is fully persisted (DB + content-addressed blobs), so it
  survives a worker restart and resumes deterministically from a stage boundary.
* Good — a `ControlInterrupt` is explicitly *not* a failure: it is never retried or
  dead-lettered (`queue/retry.py:30-39`), so pausing does not burn the retry budget.
* Bad — pause is not instantaneous; a short run can finish before it pauses (documented in
  `apps/platform/docs/superpowers/specs/2026-07-30-ui-catch-up-design.md:144-145`).

### Confirmation

Checkpoints: `worker/main.py:101-135,151-160` (docstring L1-8: "observes cancel/pause
flags at safe checkpoints"). Flag + resume: `runs/service.py:184-210,242-264`. Flag read
+ interrupt: `runs/queries.py:31-52`; `ControlInterrupt` `queue/retry.py:30-39`. Resume
re-enqueue: `runs/coordinator.py:237-241`. "Only crawls truly checkpoint":
`.../specs/2026-07-30-ui-catch-up-design.md:144-145`.

## More Information

Recorded retroactively 2026-08-08 (DEBT D10). **Honesty note:** the string `SIGSTOP`
appears nowhere in the code or specs — it is the D10 backlog's shorthand for "we pause
cooperatively, not by signal-stopping a process." The rejection rationale is design-time
judgment (off-repo memory `run-pause-model`): a `SIGSTOP`-ed worker still holds its Redis
Streams pending-list claim (blocking `XAUTOCLAIM` reclaim) and any open DB
transaction/locks, cannot persist resumable state, and does not survive a container
restart — so resume would not be durable, defeating the requirement. Katana itself is
stopped by process **kill** (`killpg`), not signal-pause (`discover/harness.py:1-11,94-100`). See ADR-0001
(the pending-list claim) and `docs/ARCHITECTURE.md` ("Async spine").
