---
phase: 01-backend-tech-debt
plan: 01
subsystem: infra
tags: [python, requirements, dependencies, celery, redis, python-jose, passlib, cve]

# Dependency graph
requires: []
provides:
  - Clean requirements.txt and pyproject.toml without python-jose CVE surface
  - Documented removal rationale in requirements.txt comment block
  - Celery task files retained on disk for optional Beat worker deployment
affects: [02-ui-polish]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Dead dependency removal documented with inline comment block explaining rationale and CVE refs"

key-files:
  created: []
  modified:
    - api/requirements.txt
    - api/pyproject.toml

key-decisions:
  - "Celery task files (api/app/tasks/) retained on disk — removed only from default pip install; Beat worker installs celery separately"
  - "Comment block added to requirements.txt to document removal rationale and CVE-2024-33664 reference"
  - "pyproject.toml updated in lockstep with requirements.txt to keep both dependency declarations in sync"

patterns-established:
  - "Dependency removals: document inline with date, rationale, and CVE refs at top of requirements.txt"

requirements-completed: [REQ-02]

# Metrics
duration: 5min
completed: 2026-04-19
---

# Phase 1 Plan 01: Remove Dead Dependencies Summary

**Eliminated python-jose CVE-2024-33664 attack surface by removing 4 dead packages (python-jose, passlib, celery, redis) from both requirements.txt and pyproject.toml while retaining Celery task files for optional Beat worker deployment.**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-04-19T19:33:56Z
- **Completed:** 2026-04-19T19:38:00Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Confirmed zero imports of jose/passlib/celery/redis in `api/app/api/` routes before removal
- Removed `python-jose[cryptography]==3.3.0`, `passlib[bcrypt]==1.7.4`, `celery==5.3.4`, `redis==5.0.1` from both requirements files
- Added comment block to `api/requirements.txt` documenting removal date, rationale, and CVE reference
- All 11 retained packages remain with original version pins unchanged
- `api/app/tasks/` directory and all 4 Celery task files intact on disk

## Task Commits

Each task was committed atomically:

1. **Task 1: Verify no runtime imports of dead packages** - (read-only scan, no commit — confirmation gate)
2. **Task 2: Remove dead packages from requirements.txt and pyproject.toml** - `6dbfd46` (chore)

**Plan metadata:** (docs commit — see below)

## Files Created/Modified

- `api/requirements.txt` — Removed 4 dead packages; added comment block explaining removals with CVE ref
- `api/pyproject.toml` — Removed same 4 dead packages from `dependencies` array

## Decisions Made

- Celery task files are NOT deleted from disk — they implement the real `retention_cleanup` Beat schedule and will be used when the Beat worker is deployed with `celery` installed separately.
- Comment block placed above the first package line in `requirements.txt` (not at EOF) for visibility.
- pyproject.toml updated in lockstep so both files declare identical dependency sets.

## Deviations from Plan

None - plan executed exactly as written.

Note: The plan's inline Python verification script produced false positives (matched comment lines), but the acceptance criteria greps and manual file inspection confirmed correct removal. This is not a deviation — the script limitation was identified and the correct verification outcome confirmed.

## Issues Encountered

The plan's Python verification script (`python3 -c "..."`) scans full file text including comment lines, so it reported false positives for `python-jose`, `passlib`, `celery`, and `redis` present in the comment block. The actual acceptance criteria greps (with `^` anchors for standalone lines) confirmed zero live package lines remain. This is a known limitation of simple substring matching against commented files — not a real issue.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- requirements.txt and pyproject.toml are clean; `pip install -r api/requirements.txt` will not pull python-jose or passlib
- CVE-2024-33664 attack surface eliminated
- Ready for Phase 1 Plan 02 (next backend tech debt task)

---
*Phase: 01-backend-tech-debt*
*Completed: 2026-04-19*
