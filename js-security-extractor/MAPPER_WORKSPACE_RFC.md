# Mapper-Style Workspace RFC

## Purpose
Define a future implementation plan for a high-density analyst workspace inspired by MapperPlus, adapted to this project's existing API, session model, and extraction pipeline.

## Goals
- Provide a single-screen workflow for large-session triage.
- Keep context visible: file tree, source code, and findings at the same time.
- Preserve traceability from finding -> file -> line/column.
- Avoid re-running analysis for simple navigation actions.

## Non-Goals
- Replacing existing New Analysis flow in phase 1.
- Introducing external SaaS dependencies.
- Building real-time multi-user collaboration.

## User Problems to Solve
- Too much tab switching between `View Files`, `New Analysis`, and results.
- Difficult to understand "what these results belong to".
- Hard to prioritize findings when duplicates dominate.

## Proposed UX

### Layout
- Left pane: session-scoped file tree with search + filters (`all`, `analyzed`, `failed`, `has map`).
- Center pane: source viewer with tabs (`Source`, `Map`, `Reconstructed Sources`).
- Right pane: findings panel with tabs (`API Endpoints`, `GraphQL`, `Secrets`, `URLs`, `Dependencies`).
- Top bar: active session selector, refresh, run/stop analysis, status chips.

### Primary Flows
1. Select session -> file list loads.
2. Click file -> source loads, findings panel binds to selected file.
3. Click finding -> editor jumps to line/column and highlights occurrence.
4. Toggle "dedupe rollup" -> findings collapse into unique entries with occurrence counts and provenance list.

### Required Interactions
- File search (substring + extension filters).
- In-file search.
- Copy/download source.
- Beautify toggle.
- "Open in Analysis" deep-link for compatibility with current page.

## Data/API Requirements

### New/Extended Endpoints
- `GET /api/sessions/{session_id}/workspace-tree`
  - Returns grouped file nodes + minimal metadata for tree rendering.
- `GET /api/files/{file_id}/workspace-context`
  - Returns file metadata, analysis summary, sourcemap state, and quick counts.
- `GET /api/files/{file_id}/findings?dedupe=true|false`
  - Returns normalized findings with provenance.
- `GET /api/sessions/{session_id}/findings-rollup`
  - Returns deduped global findings list for session-level prioritization.

### DTO Decisions
- Every finding must include:
  - `type`, `value/url`, `fileId`, `sourceFile`, `line`, `column`, `confidence`, `extractor`.
- Deduped finding includes:
  - `dedupeKey`, `occurrences`, `filesAffected`, `firstSeen`, `sources[]`.

## Frontend Architecture
- New route: `/workspace` (supports query params `session_id`, `file_id`, `view`).
- Route-driven state:
  - `session_id` controls left pane scope.
  - `file_id` controls active editor/finding context.
  - `view` controls right-pane tab.
- Preserve existing pages; workspace is additive.

## Performance Constraints
- Must handle 2,000 files/session without UI freeze.
- File tree should virtualize rows after first 200 visible nodes.
- Use memoized client-side selectors for filtering/sorting.
- Polling only for active session analysis jobs; avoid full-page rerender on every tick.

## Security/Privacy Constraints
- Keep secrets visible as requested; no default masking.
- Do not expose raw auth headers/cookies in UI.
- Continue CORS restrictions to localhost dashboard + extension origin.

## Rollout Plan

### Phase 1: Foundation
- Add route shell, pane layout, and workspace-tree API.
- Support file selection + source display.

### Phase 2: Findings Integration
- Add file findings API and right-pane tabs.
- Add click-to-line/column navigation.

### Phase 3: Dedupe Rollup
- Add session and file-level dedupe endpoints.
- Add rollup toggle and provenance inspector.

### Phase 4: Power Actions
- Add beautify/copy/download/search UX polish.
- Add compatibility deep-links from existing `View Files` buttons.

## Testing Strategy
- Backend tests:
  - DTO contract tests for new workspace endpoints.
  - Dedupe algorithm correctness tests (stable keys, counts, provenance).
- Frontend tests:
  - Route state hydration from URL query params.
  - File selection, finding jump, and tab state persistence.
- Manual smoke:
  1. Open workspace with `session_id`.
  2. Select a file and confirm source + findings bind correctly.
  3. Enable dedupe and verify counts/provenance mapping.
  4. Navigate directly via URL and confirm state restores.

## Dependencies and Task Mapping
- `B-018` is completed by this RFC and serves as approval gate for implementation.
- `B-015` implements the workspace using this document.
- `B-010` provides dedupe rollup logic used by workspace.
- `B-012` provides sourcemap status clarity shown in workspace context.

## Open Decisions for Human Approval
- Whether workspace should be default landing page or optional mode.
- Maximum file content preview size before lazy loading.
- Whether reconstructed source files are preloaded or on-demand.
