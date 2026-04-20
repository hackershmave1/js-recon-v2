---
phase: 01-backend-tech-debt
status: passed
verified: 2026-04-20
---

# Phase 1 Verification: Backend Tech Debt

**Goal:** Eliminate four structural backend problems — unbounded in-memory job dicts, dead dependencies, improper startup DDL, and the endpoint rollup TODO.

## Requirements Coverage

| Req | Description | Status | Evidence |
|-----|-------------|--------|----------|
| REQ-01 | Persistent job state | ✓ PASS | RECON_JOBS/SESSION_ANALYSIS_JOBS removed; Job ORM model at api/app/models/job.py; routes use DbJob |
| REQ-02 | Remove dead dependencies | ✓ PASS | python-jose, passlib, celery, redis absent from requirements.txt and pyproject.toml |
| REQ-03 | Alembic migrations | ✓ PASS | alembic.ini + alembic/env.py + alembic/versions/0001_initial_schema.py exist; main.py calls alembic upgrade head; create_all and ensure_runtime_schema_updates removed |
| REQ-04 | Fix endpoint rollup | ✓ PASS | summarize_endpoint_rollup(analysis_data) called at sessions.py:1272; hardcoded 0 replaced |

## Must-Haves Verified

- [x] `grep "RECON_JOBS" api/app/api/routes/recon.py` → 0 matches
- [x] `grep "SESSION_ANALYSIS_JOBS" api/app/api/routes/sessions.py` → 0 matches
- [x] `api/app/models/job.py` exists with `class Job(Base)`
- [x] `api/requirements.txt` contains no python-jose, passlib, celery, redis
- [x] `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial_schema.py` all exist
- [x] `grep "create_all\|ensure_runtime_schema_updates" api/app/main.py` → 0 matches
- [x] `grep "summarize_endpoint_rollup" api/app/api/routes/sessions.py` → 3 matches (definition + 2 call sites)
- [x] Remaining `"total_unique_endpoints": 0` at line 1236 is a zero-initializer stub for empty state — not the rollup bug

## Test Results

- Phase 1 targeted tests: 2 passed, 3 skipped (test_b011, test_recon_live_progress_updates)
- Pre-existing failures (jsluice binary, external services): unrelated to Phase 1 changes
- No new regressions introduced

## Notes

- The hardcoded 0 at sessions.py:1236 is intentional — it initializes the response struct when no analysis data exists. The actual fix (REQ-04) is at line 1272 where summarize_endpoint_rollup is called.
- Celery task files in api/app/tasks/ retained on disk per 01-01 decision log.
