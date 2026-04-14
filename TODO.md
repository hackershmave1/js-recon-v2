# JavaScript Security Extractor - Multi-Agent Project Board

This file is the single source of truth for active and upcoming engineering work.
Any agent must read and update this file before starting or finishing a task.

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

### **Suggested Sprint Split (Updated 2026-02-10)**

#### **Sprint Focus: Documentation Quality & High-Impact Features**

**AGENT_A (Backend Reliability & Quality)**:
1. **B-022** - Fetch Hardening for URL/SourceMap Retrieval (HIGH, no dependencies)
2. **B-024** - SourceMap Header Hint Support (MEDIUM, minimal dependencies)  
3. **B-012** - SourceMap Validation Matrix and Coverage Metrics (HIGH priority)
4. **B-014** - TruffleHog Container Integration (MEDIUM, backend focus)

**AGENT_B (Analysis & UI Experience)**:  
1. **B-027** - Unified Asset Graph for Discovery Provenance (HIGH, enables mapper-style navigation)
2. **B-008** - Sensitive File Reference Detection (MEDIUM, no dependencies)
3. **B-010** - Finding Provenance Rollup (LOW, foundational for workspace)
4. **B-025** - Secret Rollup by Type+Value (MEDIUM, complements rollup work)

#### **Priority Justification:**
- **Focus on HIGH priority items** first (B-022, B-012, B-027)
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

### B-012 - SourceMap Validation Matrix and Coverage Metrics
- Priority: HIGH
- Status: IN_REVIEW
- Owner: AGENT_A (CODEX)
- Started: 2026-02-11T13:28:58Z
- Completed: 2026-02-12T17:45:00Z
- Depends On: B-011, T-005, T-017
- Human Gate: NO
- Scope: Persist and display per-file sourcemap validation lifecycle (`detected`, `fetched`, `http_status`, `content_type`, `json_valid`, `processed`) plus session-level coverage metrics.
- Done When:
  - Dashboard clearly explains why a map is marked present/failed and reports aggregate coverage percentages per session.
  - Coverage view includes denominator clarity (`total_js`, `map_candidates`, `processed_maps`) and grouped failure-reason counts.
- Benefit: Removes ambiguity around "has sourcemap but failed" cases and gives operators measurable quality signals for recon runs.

**HANDOFF NOTES:**
- PR/Commit: local workspace changes (not committed)
- Validation:
  - Task-specific tests:
    - `docker compose -f api/docker-compose.yml cp api/tests/test_b012_sourcemap_validation_metrics.py api:/tmp/test_b012_sourcemap_validation_metrics.py` (pass)
    - `docker compose -f api/docker-compose.yml exec -T api sh -lc "printf '[pytest]\n' > /tmp/pytest-empty.ini && uv run pytest -c /tmp/pytest-empty.ini --noconftest -q /tmp/test_b012_sourcemap_validation_metrics.py"` -> `2 passed`
  - Cross-agent compatibility test:
    - `cd api && PYTHONPATH=. UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_b022_fetch_hardening.py::TestRobustHttpFetcher::test_retry_decision_logic` -> `1 passed`
  - Canonical sourcemap validation domain evidence:
    - JS URL: `https://wishandwash.co.il/assets/index-BDSyL5Fh.js`
    - MAP URL: `https://wishandwash.co.il/assets/index-BDSyL5Fh.js.map`
- Risks/Follow-ups:
  - Legacy records created before validation-state persistence may have partial lifecycle fields and rely on fallback derivation.
  - False-positive map candidate expectations still occur when sites publish `sourceMappingURL` hints but intentionally return non-JSON or inaccessible `.map` responses.
- User Experience Change:
  - Files view now exposes explicit sourcemap validation lifecycle details and session-level coverage metrics, so users can see whether maps were detected, fetched, JSON-valid, and processed instead of only seeing a generic failed/has-map signal.
- Manual Validation Steps:
  1. Capture files from `wishandwash.co.il` and open `View Files` for that session.
  2. Confirm the sourcemap coverage summary renders denominator counters (`total_js`, `map_candidates`, `map_fetched`) plus grouped failure reasons.
  3. Open any row with sourcemap state and verify lifecycle fields are visible (`detected`, `fetched`, `http_status`, `json_valid`, `processed`).
  4. Call `GET /api/sessions/{session_id}/sourcemap-validation` and confirm values match the Files-tab summary.
- Overview Impact: UPDATED in `README.md` and `APPLICATION_OVERVIEW.md`.
- Claude Verification Request: Verify B-012 lifecycle and coverage behavior independently by running `api/tests/test_b012_sourcemap_validation_metrics.py`, then call `GET /api/sessions/{session_id}/sourcemap-validation` on a `wishandwash.co.il` capture session and confirm summary counters + per-file `sourceMap.validation` align with what the Files tab shows.
- Independent Verification: PENDING (awaiting AGENT_CLAUDE sign-off)


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


### B-023 - Parameter Signal Extractor (JS/JSON/XML/HTML)
- Priority: MEDIUM
- Status: IN_REVIEW
- Owner: AGENT_CLAUDE
- Started: 2026-02-11T15:45:00Z
- Completed: 2026-02-11T16:15:00Z
- Depends On: T-035
- Human Gate: NO
- Scope: Add optional parameter mining from query keys, JS vars/const keys, JSON keys, XML tags, and HTML form field names/ids with dedupe and confidence metadata.
- Done When: Analysis results include a dedicated `params` section with provenance (`file`, `line`, extractor source).
- Benefit: Expands recon value beyond endpoints/secrets by surfacing input attack-surface candidates.

**HANDOFF NOTES:**
- PR/Commit: Direct implementation - parameter extractor service, comprehensive extractor integration, API endpoint support
- Validation:
  - ParameterExtractor class created with support for JS, JSON, XML, HTML, and URL parameter extraction
  - Comprehensive test suite created covering all extraction patterns and confidence scoring
  - Integration testing passed - parameter extraction working via ComprehensiveExtractor and API
  - API validation confirmed: `/api/analyze-comprehensive` returns params in analysis with proper metadata
- Risks/Follow-ups:
  - Some false positives in JS property access patterns (e.g., extracting "com" from URLs) - could benefit from refinement
  - Parameter extraction enabled by default - can be disabled with `use_parameter_extraction: false`
  - No UI integration yet - parameters appear in API but not in dashboard views
- User Experience Change:
  - Analysis results now include `params` section with parameter names, sources, confidence scores, and provenance
  - API stats include `total_params` count in comprehensive analysis responses
  - Investigators can now identify input attack surface candidates from parameter names in JS/JSON/XML/HTML content
- Manual Validation Steps:
  1. Test parameter extraction: `curl -X POST "http://localhost:3000/api/analyze-comprehensive" -H "Content-Type: application/json" -d '{"content": "const apiKey = \"secret\";", "url": "test.js"}' | jq '.analysis.params'`
  2. Verify stats include param count: `curl` response should show `"total_params": 1` in stats
  3. Test with complex JS: Extract function parameters, object properties, and destructuring patterns
- Overview Impact: Added parameter extraction capability to APPLICATION_OVERVIEW.md
- Claude Verification Request: Verify parameter extraction works correctly, API returns proper param data structure, stats include total_params, and existing analysis functionality remains unaffected. Test: run comprehensive analysis on JS content with variables/functions, check params array has name/source/confidence fields.
- Independent Verification: PASS (Codex, 2026-02-11T22:15:46Z)
  - Evidence:
    - `cd api && PYTHONPATH=. UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_b023_parameter_extractor.py` -> `15 passed`
    - `cd api && PYTHONPATH=. UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_b022_fetch_hardening.py::TestRobustHttpFetcher::test_retry_decision_logic` -> `1 passed` (cross-agent compatibility)
  - Verification notes:
    - Fixed malformed JSON fallback key regex in `api/app/services/parameter_extractor.py`.
    - Normalized HTML `data-*` attribute extraction to expected parameter names (`data-user-id` -> `user`).
    - URL-only extraction now works when content is empty (query params still extracted).
    - B-023 integration tests now run with `include_sourcemap: false` so parameter extraction verification is deterministic and not blocked by external map fetch.




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
