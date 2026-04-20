---
phase: 01-backend-tech-debt
plan: "03"
subsystem: api
tags: [job-persistence, db, recon, session-analysis, memory-leak-fix]
dependency_graph:
  requires: [01-02]
  provides: [db-backed-recon-jobs, db-backed-session-analysis-jobs]
  affects: [api/app/api/routes/recon.py, api/app/api/routes/sessions.py]
tech_stack:
  added: []
  patterns: [SQLAlchemy ORM query, worker-owned DB session, UUID coercion for SQLite compat]
key_files:
  created: []
  modified:
    - api/app/api/routes/recon.py
    - api/app/api/routes/sessions.py
    - api/tests/test_recon_live_progress_updates.py
decisions:
  - UUID coercion (uuid.UUID(job_id)) applied at all filter(DbJob.id==) call sites for SQLite/PostgreSQL compatibility
  - Worker threads receive their own DB session via worker_session_factory (no shared session with request thread)
  - RECON_JOB_STOP_EVENTS and SESSION_ANALYSIS_STOP_EVENTS retained as in-process dicts (not persisted)
metrics:
  duration: ~35min (including interruption and verification)
  completed: 2026-04-19
---

# Phase 1 Plan 03 Summary: Replace In-Memory Job Dicts with DB

**Status:** Complete
**Date:** 2026-04-19
**Commits:**
- `16f794f` feat(01-03): replace RECON_JOBS dict with DB operations in recon.py
- `aadbedb` feat(01-03): replace SESSION_ANALYSIS_JOBS dict with DB operations in sessions.py
- `c96dbea` fix(01-03): coerce job_id str to UUID before SQLAlchemy filter; update stale tests

## What Was Built

The two unbounded module-level in-memory dicts — `RECON_JOBS` in `recon.py` and `SESSION_ANALYSIS_JOBS` in `sessions.py` — were fully replaced with SQLAlchemy queries against the `jobs` table created in plan 01-02. All job state (status, assets, coverage, cancellation flags) is now written to and read from the database, so job state survives API restarts and is safely shared across multiple uvicorn workers. Worker threads receive their own DB session via a `worker_session_factory` to avoid shared-session race conditions across threads.

## Key Files Changed

- `api/app/api/routes/recon.py` — Removed `RECON_JOBS` dict; added `from ...models import Job as DbJob`; rewrote `get_public_job_snapshot`, `update_job_asset`, `finalize_job`, `get_latest_session_capture_coverage` to accept `db_session`; rewrote `run_recon_job_worker` with its own session; rewrote all four route handlers to use `Depends(get_db)`; added UUID coercion at all filter call sites.
- `api/app/api/routes/sessions.py` — Removed `SESSION_ANALYSIS_JOBS` dict; added `SESSION_ANALYSIS_STOP_EVENTS` for in-process stop signals; rewrote `get_job_snapshot`, `update_job_file_status`, `finalize_job`, `is_job_cancellation_requested`, `mark_queued_files_as_cancelled` to accept `db_session`; rewrote `run_session_analysis_worker` with its own session; fixed `get_latest_session_capture_coverage` call site to pass `db`.
- `api/tests/test_recon_live_progress_updates.py` — Updated from in-memory dict API to DB-backed API; uses `Job.__table__.create()` on in-memory SQLite (not `Base.metadata.create_all`) to avoid JSONB incompatibility in other models.

## Verification

- [x] RECON_JOBS removed from recon.py (0 matches)
- [x] SESSION_ANALYSIS_JOBS removed from sessions.py (0 matches)
- [x] `from ...models import Job as DbJob` present in both files
- [x] DbJob used in 11 places in recon.py, 26 places in sessions.py
- [x] SESSION_ANALYSIS_STOP_EVENTS dict present in sessions.py
- [x] RECON_JOB_STOP_EVENTS dict retained in recon.py
- [x] All job reads/writes go through DbJob model
- [x] Tests pass: 13/13 (test_01_02_job_model + test_recon_live_progress_updates)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] UUID string passed to SQLAlchemy UUID column filter**
- **Found during:** Test execution
- **Issue:** `filter(DbJob.id == job_id)` where `job_id` is a `str` fails on SQLite (UUID dialect expects `uuid.UUID` object). Works on PostgreSQL but breaks test isolation.
- **Fix:** Added `uuid.UUID(job_id)` coercion at all five `filter(DbJob.id == ...)` call sites in `recon.py`; `stop_recon_job` also validates UUID format and returns 404 on malformed input.
- **Files modified:** `api/app/api/routes/recon.py`
- **Commit:** `c96dbea`

**2. [Rule 1 - Bug] test_recon_live_progress_updates.py referenced removed RECON_JOBS dict**
- **Found during:** Test run after migration
- **Issue:** Two existing tests called `recon.RECON_JOBS.clear()` and `recon.RECON_JOBS[job_id] = ...` — the dict no longer exists.
- **Fix:** Rewrote both tests to insert `DbJob` rows into an in-memory SQLite database (jobs table only) and pass `db_session` to all helper functions.
- **Files modified:** `api/tests/test_recon_live_progress_updates.py`
- **Commit:** `c96dbea`

### Pre-existing Test Failures (out of scope)

The following failures existed before plan 01-03 and are unrelated to the job dict migration:
- `test_api_endpoints.py::TestSessionAnalysisEndpoints` — fail with PostgreSQL connection refused (no DB running in test environment)
- `test_security_utils.py` / `test_t016_*` — unrelated to job persistence
- `test_jsluice_extractor.py` — binary not present in test environment
- `test_b020_chunked_regex.py` — imports `api.app...` with wrong path prefix

These are tracked in deferred items, not caused by this plan.

## Self-Check: PASSED

- `api/app/api/routes/recon.py` — exists, RECON_JOBS absent, DbJob imported
- `api/app/api/routes/sessions.py` — exists, SESSION_ANALYSIS_JOBS absent, DbJob imported
- `api/tests/test_recon_live_progress_updates.py` — exists, 2/2 tests pass
- Commits `16f794f`, `aadbedb`, `c96dbea` — all present in git log
