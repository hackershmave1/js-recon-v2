# Roadmap — M1: Technical Quality

_Security concerns excluded from this milestone. See .planning/CONCERNS.md for security findings._

---

## Phase 1: Backend Tech Debt

**Goal:** Eliminate the four structural backend problems that cause runtime instability (unbounded memory, lost job state on restart, dead dependencies, improper migrations) and fix the endpoint rollup bug.

**Requirements:** REQ-01, REQ-02, REQ-03, REQ-04

**Depends on:** —

### Scope

- Replace in-memory `RECON_JOBS` / `SESSION_ANALYSIS_JOBS` dicts with DB-backed job records using the existing SQLAlchemy session
- Remove `python-jose`, `passlib` from requirements (never imported); decide on Celery (remove or wire up)
- Convert startup DDL to Alembic migrations; ensure `alembic upgrade head` runs at startup
- Fix the hardcoded `0` at `sessions.py:1214` to return the actual endpoint count from DB

---

## Phase 2: UI Polish

**Goal:** Bring the dashboard and Chrome extension to a consistent, professional visual standard by fixing the three high-priority and eleven minor UI issues identified in the audit.

**Requirements:** REQ-05, REQ-06, REQ-07, REQ-08

**Depends on:** —

### Scope

- Create shared color token (`--accent`, `--accent-hover`) used by both dashboard CSS and extension CSS/JS; align to dashboard hue family
- Collapse 14-stop font-size scale to 5-stop CSS custom properties in `dashboard.css`; update all callsites
- Rewrite `getEmptyState(title, body, icon)` in `dashboard.js` to emit `.empty-state-title` / `.empty-state-body` markup with per-context body copy
- Address the 11 minor UI findings from `.planning/UI-REVIEW.md` (hardcoded prefill, emoji buttons, aria-labels, confirm() replacements, z-index fix, status bar, padding, heading semantics, extension button colors, polling indicators, at-rest empty state message)
