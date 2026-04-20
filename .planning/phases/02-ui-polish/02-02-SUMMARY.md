---
phase: 02-ui-polish
plan: 02
subsystem: ui
tags: [css, dashboard, typography, design-system]

# Dependency graph
requires: []
provides:
  - Five-stop dashboard typography scale via CSS custom properties
  - Replacement of one-off font-size declarations with named tokens
affects: [api/app/static/dashboard.css]

# Tech tracking
tech-stack:
  added: []
  patterns: [tokenized typography scale instead of one-off font sizes]

key-files:
  created: []
  modified:
    - api/app/static/dashboard.css

key-decisions:
  - "Kept body 15px, stat-number display sizes, and icon sizing outside the typography token scale so the new system only normalizes UI text"

patterns-established:
  - "Dashboard typography should use --text-xs through --text-lg rather than raw rem values in selectors"

requirements-completed: [REQ-06]

# Metrics
duration: 15min
completed: 2026-04-20
---

# Phase 2 Plan 02: Collapse Dashboard Typography to a 5-Stop Scale

**dashboard.css now uses five named text-size tokens instead of a spread of near-duplicate rem values**

## Performance

- **Duration:** 15 min
- **Completed:** 2026-04-20T08:39:39Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments
- Added `--text-xs`, `--text-sm`, `--text-base`, `--text-md`, and `--text-lg` to the dashboard `:root` block
- Replaced raw font-size declarations across buttons, tables, result cards, modals, badges, empty states, and status surfaces with token references
- Removed the audit's one-off offenders (`0.775rem`, `0.82rem`, `0.85rem`, `0.9rem`, `1.05rem`) from selector rules
- Preserved intentional non-scale exceptions for body size, stat display numbers, icons, and inline `code`

## Task Commits

1. **Task 1-2: Tokenize dashboard typography scale** - `52715ed` (feat)

## Files Created/Modified
- `api/app/static/dashboard.css` - added the scale tokens and replaced raw selector font sizes with `var(--text-*)`

## Decisions Made
- Rounded a handful of visually near-identical sizes to the nearest scale stop instead of preserving micro-differences that caused the original audit finding

## Deviations from Plan

None.

## Issues Encountered

None.

## User Setup Required

None.

## Next Phase Readiness

- Empty-state and minor-polish plans can now rely on stable dashboard type tokens rather than raw size literals.

---
*Phase: 02-ui-polish*
*Completed: 2026-04-20*
