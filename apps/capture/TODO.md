# JavaScript Security Extractor - Multi-Agent Project Board

For session-start guidance, read `AGENTS.md` first.

This file is the source of truth for active and upcoming engineering work only.
Any agent must read and update this file before starting or finishing a task that changes the active work queue.

## Collaboration Protocol (Required)

### 1) Task Claim / Lock
1. Pick one task with `Status: OPEN`.
2. Update it immediately to:
   - `Status: CLAIMED`
   - `Owner: AGENT_A` or `AGENT_B`
   - `Started: <UTC timestamp>`
3. No other agent may work that task while it is `CLAIMED`, `IN_PROGRESS`, or `IN_REVIEW`.

### 2) Status Lifecycle
- `OPEN` -> `CLAIMED` -> `IN_PROGRESS` -> `IN_REVIEW` -> `DONE`
- If blocked by dependency/handoff: `BLOCKED_HUMAN`

### 3) Dependency and Human Gate Rule
- Default rule: tasks must be independently executable.
- If a task requires prior work, it must explicitly list `Depends On`.
- Any task with non-empty `Depends On` must have `Human Gate: REQUIRED`.
- Agent must not start dependent task until a human sets:
  - `Human Approval: APPROVED by <name/date>`

### 4) Collision Prevention
- One owner per task ID.
- If two edits claim the same task, earliest `Started` timestamp wins.
- Losing claim must revert to `OPEN` or choose another task.

### 5) Handoff Notes (Required)
On task completion, add:
- `PR/Commit:`
- `Validation:`
- `Risks/Follow-ups:`
- `User Experience Change:` (what the user will now experience differently)
- `Manual Validation Steps:` (clear, reproducible steps a human can run)

### 6) Design-First Requirement (Mandatory)
- Before any implementation begins, agent must create/update a matching entry in `IMPLEMENTATION_DETAILS.md`.
- The implementation entry must include:
  - task ID, owner, scope, planned file changes, API/schema changes, risks, and test plan.
- No code changes are allowed until the implementation entry status is `PLANNED`.
- Required linkage:
  - `TODO.md` task ID <-> `IMPLEMENTATION_DETAILS.md` entry ID.

### 7) Testing Requirement (Mandatory)
- Every task must include automated tests or explicit justification why tests cannot be added.
- Minimum required before `DONE`:
  - run task-specific tests written/updated by the implementing agent.
  - run at least one other agent's relevant test suite to validate compatibility.
- Task validation notes must include exact commands and pass/fail outcome.
- Move-on gate: an agent must not claim/start the next task until the current task has a recorded, passing cross-agent test run.
- If cross-agent tests fail, task status must not move to `DONE`; set `IN_REVIEW` or `BLOCKED_HUMAN` with failure details.
- Mandatory live testing domain policy (effective immediately):
  - Allowed domain for manual/integration/live capture tests: `wishandwash.co.il`
  - Prohibited in new tests/task notes: `example.com` and legacy HoneyBook targets
  - If task scope touches sourcemap detection/ingestion/analysis-on-upload, validation notes must include the exact JS URL and MAP URL used from `wishandwash.co.il`.
  - Historical entries may reference old domains; they are superseded by this rule for all future work.

### 8) Application Overview Update (Mandatory)
- Every completed task must update `APPLICATION_OVERVIEW.md` when architecture, data model, API behavior, or workflow changes.
- If no overview update is needed, agent must explicitly state: `Overview Impact: NONE` in task handoff notes.

### 9) Completion Gate: UX + Manual Validation (Mandatory)
- A task is not `100% complete` and must not be marked `DONE` unless both fields are present in the task handoff:
  - `User Experience Change:`
  - `Manual Validation Steps:`
- These two fields must be concrete and user-facing (not internal-only implementation notes).

### 10) Test Domain Policy (Mandatory, All Agents)
- Effective immediately, all new live/manual/integration testing must use `wishandwash.co.il` (or its subdomains).
- `example.com` is prohibited for new testing payloads, smoke checks, task notes, and validation steps.
- Legacy entries using older domains remain as historical logs only and must not be copied into new tasks.

### 11) Archive Policy (Mandatory, All Agents)
- `TODO.md` is for active/planned work only (`OPEN`, `CLAIMED`, `IN_PROGRESS`, `IN_REVIEW`, `BLOCKED_HUMAN`).
- When a task reaches `DONE` or `DROPPED`, move its full record to `COMPLETED_TASKS.md` and remove it from `TODO.md` in the same change.

### 12) Cross-Agent Verification Sign-Off (Mandatory)
- Every completed task handoff must include a dedicated field:
  - `Claude Verification Request:` concise summary of what changed + exact checks Claude must run independently.
- The task cannot move beyond `IN_REVIEW` until the second agent adds:
  - `Independent Verification:` `PASS` or `FAIL`, command/evidence, timestamp.
- Move-on gate:
  - No agent may claim/start the next task until the current task has an explicit `Independent Verification: PASS` from the other agent (or a human override).

## Priority Legend
- `CRITICAL`: must land first for core product flow
- `HIGH`: needed for production usability
- `MEDIUM`: quality/integration improvements
- `LOW`: resilience/polish

---

## Active Work Queue (Atomic Tasks)

Protocol note: any historical task entries below that mention legacy domains are archival records only. For all new execution and validation, use `wishandwash.co.il` per Section 10.

### **Suggested Sprint Split (Updated 2026-06-16)**

#### **Sprint Focus: Documentation Quality & High-Impact Features**

**AGENT_A (Backend Reliability & Quality)**:
1. **B-022** - Fetch Hardening for URL/SourceMap Retrieval (HIGH, no dependencies)
2. **B-024** - SourceMap Header Hint Support (MEDIUM, minimal dependencies)  
3. **B-014** - TruffleHog Container Integration (MEDIUM, backend focus)
4. **B-030** - Katana Library Recon Integration (HIGH, backend focus)

**AGENT_B (Analysis & UI Experience)**:  
1. **B-027** - Unified Asset Graph for Discovery Provenance (HIGH, enables mapper-style navigation)
2. **B-008** - Sensitive File Reference Detection (MEDIUM, no dependencies)
3. **B-010** - Finding Provenance Rollup (LOW, foundational for workspace)
4. **B-025** - Secret Rollup by Type+Value (MEDIUM, complements rollup work)

#### **Priority Justification:**
- **Focus on HIGH priority items** first (B-022, B-027, B-030)
- **Minimize dependency blockers** - selected items with minimal/no dependencies  
- **Balance backend reliability** (A) with **user-facing improvements** (B)
- **Defer complex items** like B-015 (Workspace UI) until foundational rollup work is done


### Required human checkpoints
- Before starting any task with `Human Gate: REQUIRED`, human must mark approval line.
- If a dependency task changes contract/schema, dependent task returns to `BLOCKED_HUMAN` until re-approved.

---

## Backlog (after active queue)


### B-001 - Session-level sourcemap analytics
- Priority: LOW
- Status: OPEN
- Owner: UNASSIGNED
- Depends On: T-004, T-009
- Human Gate: REQUIRED
- Human Approval: PENDING


### B-002 - Enhanced sourcemap visualization (tree + linkage)
- Priority: LOW
- Status: OPEN
- Owner: UNASSIGNED
- Depends On: T-010
- Human Gate: REQUIRED
- Human Approval: PENDING


### B-005 - Collaborative annotations/sharing
- Priority: WISHLIST
- Status: OPEN
- Owner: UNASSIGNED
- Depends On: none
- Human Gate: NO


### B-006 - High-Signal Endpoint Filtering Mode
- Priority: HIGH
- Status: OPEN
- Owner: UNASSIGNED
- Depends On: T-033
- Human Gate: REQUIRED
- Human Approval: PENDING
- Scope: Add an optional strict endpoint mode that suppresses build/module/static-path noise and prioritizes auth/admin/internal/API/well-known routes.
- Done When: Analysts can toggle `High-Signal` mode and see materially fewer low-value endpoints without losing critical auth/API paths.
- Benefit: Reduces false positives and review fatigue during large-session triage, improving analyst speed and trust in findings.




### B-010 - Finding Provenance Rollup (Cross-File Dedupe View)
- Priority: LOW
- Status: OPEN
- Owner: UNASSIGNED
- Depends On: T-035
- Human Gate: NO
- Scope: Add a rollup that dedupes identical findings globally and shows occurrence counts plus source-file list.
- Done When: Analysts can pivot between per-file findings and deduped global findings with provenance.
- Benefit: Removes repeated noise from chunked builds and helps prioritize unique issues first.


### Capture Reliability Execution Order (Approved)
1. `B-011` Automated Headless JS/Map Recon Runner
2. `B-017` Extension Auth Context Capture and Replay
3. `B-026` Capture Coverage KPIs and Miss-Reason Taxonomy
4. `B-012` SourceMap Validation Matrix and Coverage Metrics
5. `B-016` Lazy-Chunk Route Exploration Strategy
6. `B-022` Fetch Hardening for URL/SourceMap Retrieval
7. `B-028` SourceMap Discovery Precedence and Audit Trail
8. `B-029` WishAndWash Capture E2E Regression Harness
9. `B-015` Mapper-Style Analyst Workspace UI
10. `B-014` TruffleHog Container Integration

### B-013 - Per-Target Auth Profile Support for Recon Jobs  
- Priority: MEDIUM
- Status: OPEN
- Owner: UNASSIGNED
- Depends On: B-011, B-017
- Human Gate: NO
- Scope: Add simple target profiles for backend/headless scan jobs (domain-scoped cookies/custom headers) stored in local config files. This is operator-managed profile storage and reuse, not extension capture transport.
- Done When:
  - Authenticated targets can be scanned reproducibly without manual browser setup.
  - Profile CRUD supports validation and scoped use per target domain.
  - Profiles stored as plain text JSON config files (local device only).
- Benefit: Avoids 4xx-driven blind spots and enables deeper JS/map retrieval in authenticated or protected app surfaces.


### B-014 - TruffleHog Container Integration
- Priority: MEDIUM
- Status: OPEN
- Owner: UNASSIGNED
- Depends On: B-011, B-012
- Human Gate: NO
- Scope: Add first-class TruffleHog scanning support using container execution against stored session artifacts (`docker run --rm -v ... trufflesecurity/trufflehog:latest filesystem ...`) and map findings back to `fileId`/`sessionId`.
- Scope Notes:
  - `httpx` integration is intentionally excluded by product direction.
- Done When: User can launch TruffleHog scan for a session from API/UI and view normalized secret findings alongside existing analysis results.
- Benefit: Adds a complementary secret detector with low integration risk (containerized), improving coverage without replacing existing jsluice/REP extractors.


### B-015 - Mapper-Style Analyst Workspace UI
- Priority: MEDIUM
- Status: OPEN
- Owner: UNASSIGNED
- Depends On: T-035, B-011, B-012, B-016, B-018
- Human Gate: REQUIRED
- Human Approval: PENDING
- Scope: Add a dedicated workspace view with file tree/search, source viewer, and segmented finding tabs (`API Endpoints`, `GraphQL`, `Secrets`, `URLs`) plus quick actions (`beautify`, `copy`, `download`).
- Done When: Analysts can triage large sessions in a single high-density screen without jumping between tabs/pages.
- Benefit: Improves analyst speed and clarity for large scans by bringing file navigation and findings context into one interface.


### B-016 - Lazy-Chunk Route Exploration Strategy
- Priority: MEDIUM
- Status: OPEN
- Owner: UNASSIGNED
- Depends On: B-011
- Human Gate: NO
- Scope: Add configurable interaction strategy for headless scans (route warm-up, click/script triggers, timed waits) to force lazy chunk loading before capture finalization.
- Done When: Scan jobs consistently collect late-loaded JS/chunk assets on SPA targets.
- Benefit: Expands capture depth beyond initial page load and reduces missed sourcemaps/endpoints in modern frontends.

### B-028 - SourceMap Discovery Precedence and Audit Trail
- Priority: HIGH
- Status: OPEN
- Owner: UNASSIGNED
- Depends On: B-012, B-024
- Human Gate: NO
- Scope: Implement deterministic source map candidate precedence (`uploaded_content > SourceMap header > sourceMappingURL comment > .js.map fallback`) and persist audit records for selected/rejected candidates with reasons.
- Done When:
  - Each file has one canonical selected map candidate (or explicit no-map verdict) with explainable provenance.
  - Operators can see candidate evaluation history in API/UI without manual reproduction.
- Benefit: Eliminates ambiguity around conflicting map hints and reduces false expectations in analysis flow.


### B-029 - WishAndWash Capture E2E Regression Harness
- Priority: HIGH
- Status: OPEN
- Owner: UNASSIGNED
- Depends On: B-011, B-017, B-026
- Human Gate: NO
- Scope: Add repeatable E2E harness for `wishandwash.co.il` that validates capture depth and sourcemap pipeline outcomes against baseline thresholds.
- Done When:
  - CI/manual harness reports pass/fail on coverage gates (for example minimum discovered/ingested JS count and minimum successful sourcemap processing ratio).
  - Failures include top miss reasons and representative asset samples.
- Benefit: Prevents regressions in the exact real-world flow you care about and gives a stable quality bar for future changes.


### B-030 - Katana Library Recon Integration (URL -> JS/Map -> Analysis)
- Priority: HIGH
- Status: OPEN
- Owner: UNASSIGNED
- Depends On: B-011, B-013, B-017
- Human Gate: REQUIRED
- Human Approval: PENDING
- Scope: Add first-class Katana-backed recon jobs where user submits target URL(s), crawler discovers JS assets (standard/hybrid), backend ingests/discovers `.map` files, and existing analysis pipeline runs automatically.
- Done When:
  - User can launch Katana recon job from API/UI with unauthenticated mode and authenticated mode (via stored profile and/or captured auth context).
  - Job output stores deterministic crawl provenance (`source=katana`, depth, discovered_via, response metadata, timestamp) and coverage counters.
  - Discovered JS assets and sourcemaps flow through existing `/api/save-files` + analysis without duplicate records.
- Benefit: Improves JS/map coverage beyond passive browsing and supports authenticated crawling for protected app surfaces.


### B-032 - Sessions Tab Scalability and Clarity Overhaul
- Priority: HIGH
- Status: OPEN
- Owner: UNASSIGNED
- Depends On: T-023, B-026, B-031
- Human Gate: NO
- Scope: Redesign Sessions tab for high-volume usage with virtualization/pagination, grouped status sections, sticky filter/action bar, progressive row hydration, and reduced polling churn to avoid UI glitches under active analysis.
- Done When:
  - Sessions view remains responsive with large datasets (target: 500+ rows) and active polling.
  - Critical actions (Open Session, Analyze All, Stop, Delete) stay visible and stable during updates.
  - User can quickly isolate problem sessions (failed, no analysis, low coverage) via built-in facets.
- Benefit: Fixes perceived UI instability when many sessions/files are active and makes triage faster.

---

## Definition of Done (Global)
- Code merged and runnable in Docker.
- Existing core workflows still pass smoke checks.
- Pre-implementation design entry exists in `IMPLEMENTATION_DETAILS.md`.
- Task-specific tests are added/updated and executed.
- At least one relevant test set from another agent is executed and recorded before claiming the next task.
- API/UI/architecture changes documented in `README.md` and `APPLICATION_OVERVIEW.md`.
- Task entry updated with final status and handoff notes.
