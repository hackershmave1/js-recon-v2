# Requirements — M1: Technical Quality

_Scope: non-security technical debt and UI polish. Security concerns are explicitly out of scope._

## REQ-01 — Persistent Job State
In-memory `RECON_JOBS` and `SESSION_ANALYSIS_JOBS` dicts in the API must be replaced with database-backed job records. Jobs must survive API restarts. Entries must not grow unbounded.

## REQ-02 — Remove Dead Dependencies
`python-jose` and `passlib` are listed in `requirements.txt` but are never imported in any application code. They must be removed. `celery` and `redis` are declared but the Celery task infrastructure is never dispatched from anywhere — either wire up Celery or remove it.

## REQ-03 — Alembic Migrations
The API currently applies schema changes via ad-hoc DDL executed at startup. Alembic is already listed in requirements. All schema changes must be managed through Alembic migration files. Startup should call `alembic upgrade head`, not raw DDL.

## REQ-04 — Fix Endpoint Rollup TODO
`api/app/api/routes/sessions.py:1214` contains a hardcoded `0` return for the endpoint rollup count. This must be computed from actual data.

## REQ-05 — Unified Accent Color Token
The dashboard uses `#356AE6` as its primary accent color; the Chrome extension uses `#0a84ff`. These must be unified to a single design token so both surfaces share the same brand color family.

## REQ-06 — Typography Scale
`dashboard.css` contains at least 14 distinct font-size values for "small UI text" (0.8rem, 0.8125rem, 0.82rem, 0.85rem, etc.) with no perceptible visual difference. These must be collapsed to a 5-stop CSS custom property scale (`--text-xs` through `--text-lg`) and applied consistently.

## REQ-07 — Empty State Component
`getEmptyState()` in `dashboard.js` emits raw icon + paragraph markup instead of using the `.empty-state-title` / `.empty-state-body` CSS classes defined in `dashboard.css:538–539`. All six empty result panels must use the proper markup and include actionable body copy.

## REQ-08 — Minor UI Findings
The 11 additional minor findings documented in `.planning/UI-REVIEW.md` must be addressed, including: hardcoded test domain prefill, emoji buttons, missing aria-labels on settings icon, `confirm()` for destructive actions, `showAlert` z-index, status bar duplication, asymmetric tile padding, heading semantic override, extension button semantic colors, polling lag indicators, and "No analysis context selected" shown at rest.
