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

---

## Phase 3: Resume Crawl Frontend

**Goal:** Wire the backend resume feature into the dashboard UI so users can resume interrupted crawl sessions with one click.

**Requirements:** TBD

**Depends on:** Phase 1, Phase 2

### Scope

- Add a "Continue Crawl" action inline in each eligible session row's action bar (visible only when session has files AND a prior tracked recon job exists AND that job is in a terminal state)
- Use `reconSessionProgress.targets[0]` + saved options for the resumed job payload (no user input required for URL)
- POST `{sessionId, url, resume: true, ...options}` to `/api/recon/jobs/start`
- Keep the new button's state (disabled/hidden) in sync with polling via `patchSessionReconProgressRow`
- Out of scope (deferred per 03-CONTEXT.md): extension-created session resume with URL prompt, "resumed" asset count in badges, resume modal

**Plans:** 1 plan

Plans:
- [x] 03-01-PLAN.md — Wire Continue Crawl button, continueCrawl method, and polling sync into dashboard.js
