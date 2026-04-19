---
phase: 01-backend-tech-debt
plan: 02
subsystem: database
tags: [sqlalchemy, orm, postgresql, sqlite, job-model, migration-prep]

# Dependency graph
requires: []
provides:
  - "Job SQLAlchemy ORM model in api/app/models/job.py (__tablename__='jobs')"
  - "Job exported from api/app/models.__all__"
  - "11-column schema covering both RECON_JOBS and SESSION_ANALYSIS_JOBS dict shapes"
affects: [01-03-replace-recon-jobs-dict, 01-04-replace-session-analysis-jobs-dict, alembic-migrations]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Job model uses Boolean for cancel_requested (consistent with file.py/source_map.py pattern)"
    - "Large mutable state stored as JSON column (state_json), mirroring FileAnalysis.analysis pattern"
    - "session_id stored as String(36), not FK, to avoid cascade complexity with recon jobs"
    - "TDD test creates only the jobs table (Job.__table__.create) to avoid JSONB incompatibility in other models under SQLite"

key-files:
  created:
    - api/app/models/job.py
    - api/tests/test_01_02_job_model.py
  modified:
    - api/app/models/__init__.py

key-decisions:
  - "[01-02] Used Boolean for cancel_requested instead of String('0'/'1') — existing models (file.py, source_map.py) all use Boolean; plan note about String portability was incorrect"
  - "[01-02] state_json uses sqlalchemy.types.JSON (cross-dialect) not postgresql.JSONB — JSONB would block SQLite testing"
  - "[01-02] No FK from Job.session_id to Session.id — avoids cascade complexity; recon jobs may reference sessions not yet committed"

patterns-established:
  - "Job model is the single persisted type for both recon and session analysis jobs"
  - "TDD fixture creates one table at a time (Model.__table__.create) when other models have PostgreSQL-only types"

requirements-completed: [REQ-01]

# Metrics
duration: 3min
completed: 2026-04-19
---

# Phase 1 Plan 02: Create Job ORM Model Summary

**SQLAlchemy Job model with 11 columns covering both RECON_JOBS and SESSION_ANALYSIS_JOBS dict shapes, exported from app.models, backed by TDD test suite (11/11 passing)**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-04-19T19:37:11Z
- **Completed:** 2026-04-19T19:39:40Z
- **Tasks:** 2
- **Files modified:** 3 (1 created + 1 modified + 1 test created)

## Accomplishments

- Created `api/app/models/job.py` with `class Job(Base)` — `__tablename__ = "jobs"`, all 11 required columns
- Exported `Job` from `api/app/models.__init__` with `__all__` updated; all prior exports preserved
- Full TDD cycle: RED commit (11 failing tests) → GREEN commit (all pass); fixture fixed to avoid JSONB/SQLite incompatibility

## Task Commits

Each task was committed atomically:

1. **RED: Failing tests for Job model** - `4527eda` (test)
2. **Task 1: Create api/app/models/job.py** - `83b0a06` (feat)
3. **Task 2: Export Job from __init__ + fixture fix** - `c2100eb` (feat)

_Note: TDD task split into test commit (RED) then implementation commit (GREEN)_

## Files Created/Modified

- `api/app/models/job.py` — SQLAlchemy Job ORM model; 11 columns covering recon and session-analysis job state
- `api/app/models/__init__.py` — Added `from .job import Job` and `"Job"` to `__all__`
- `api/tests/test_01_02_job_model.py` — 11 TDD tests: importability, column presence, type checks, construction defaults

## Decisions Made

- **Boolean for cancel_requested** — Plan specified `String("0"/"1")` for portability, but existing models (`file.py`, `source_map.py`) already use `Boolean`. Consistency takes precedence; corrected via Rule 1 auto-fix.
- **sqlalchemy.types.JSON (not JSONB)** — Using the cross-dialect `JSON` type ensures the model works in both PostgreSQL (production) and SQLite (tests). JSONB is PostgreSQL-only and would break test fixture setup.
- **No FK from session_id to Session** — Recon jobs reference sessions that may not exist yet when a job starts; keeping session_id as a plain String avoids cascade complexity.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Used Boolean instead of String("0"/"1") for cancel_requested**
- **Found during:** Task 1 (creating job.py)
- **Issue:** Plan specified `cancel_requested = Column(String(1), ...)` with "0"/"1" values for "portability", but `api/app/models/file.py` and `api/app/models/source_map.py` both use `Column(Boolean, ...)`. Using a different pattern would create inconsistency and surprise future developers.
- **Fix:** Changed `cancel_requested` to `Column(Boolean, nullable=False, default=False, server_default="false")` matching existing models.
- **Files modified:** `api/app/models/job.py`
- **Verification:** All 11 tests pass; column inspects correctly.
- **Committed in:** `83b0a06` (Task 1 commit)

**2. [Rule 3 - Blocking] Fixed sqlite_session fixture JSONB incompatibility**
- **Found during:** Task 2 GREEN phase (running TDD construction tests)
- **Issue:** Fixture called `Base.metadata.create_all(bind=engine)` which tried to create all tables including `files` (uses `JSONB`) — SQLite can't render `JSONB`, raising `UnsupportedCompilationError`.
- **Fix:** Changed fixture to `Job.__table__.create(bind=engine)` — creates only the jobs table, sidestepping all JSONB columns in other models.
- **Files modified:** `api/tests/test_01_02_job_model.py`
- **Verification:** All 4 construction tests now pass; 11/11 total.
- **Committed in:** `c2100eb` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (1 Rule 1 bug, 1 Rule 3 blocking)
**Impact on plan:** Both fixes essential for correctness and test runability. No scope creep.

## Issues Encountered

- SQLite/JSONB incompatibility in test suite fixture — resolved by creating only the jobs table in the SQLite fixture rather than all tables. The core production model is unaffected.

## User Setup Required

None — no external service configuration required. Alembic migration (adding the `jobs` table to the database) is handled in plan 01-03 or a dedicated migrations plan.

## Next Phase Readiness

- `Job` model is importable and tested; plan 01-03 (replace RECON_JOBS dict) can immediately import and use `Job`
- No blockers; alembic migration will be needed before the table can be used in production
- `state_json` JSON column is cross-dialect; works in both PostgreSQL (prod) and SQLite (tests)

## Self-Check: PASSED

All files confirmed present:
- `api/app/models/job.py` — FOUND
- `api/app/models/__init__.py` — FOUND
- `api/tests/test_01_02_job_model.py` — FOUND
- `.planning/phases/01-backend-tech-debt/01-02-SUMMARY.md` — FOUND

All commits confirmed:
- `4527eda` test(01-02) RED — FOUND
- `83b0a06` feat(01-02) Task 1 — FOUND
- `c2100eb` feat(01-02) Task 2 — FOUND

---
*Phase: 01-backend-tech-debt*
*Completed: 2026-04-19*
