# Agent Session Guide

This is the first file agents should read at the start of every session. It defines where project truth lives and prevents duplicate docs from competing with each other.

## Session Start Checklist

1. Read this file.
2. Check `git status --short` before editing; do not revert unrelated user changes.
3. Read `TODO.md` for active/planned work only.
4. Read `.planning/STATE.md` for the current project state and recent decisions.
5. For architecture context, read `APPLICATION_OVERVIEW.md` first, then `.planning/codebase/*` only as needed.
6. For implementation work, update `IMPLEMENTATION_DETAILS.md` before code changes when the task requires design-first planning.
7. Archive completed work in `COMPLETED_TASKS.md` and remove it from `TODO.md` in the same change.

## Documentation Authority Map

| Need | Authoritative file | Notes |
|------|--------------------|-------|
| Start a session | `AGENTS.md` | This file. Keep it short and current. |
| Active work queue | `TODO.md` | Only `OPEN`, `CLAIMED`, `IN_PROGRESS`, `IN_REVIEW`, and blocked active work belongs here. |
| Completed work history | `COMPLETED_TASKS.md` | Archive only. Do not use as current setup guidance. |
| Current project state | `.planning/STATE.md` | Snapshot of current status, decisions, and recent cleanup outcomes. |
| User/product quickstart | `README.md` | Human-facing setup and workflow. |
| Backend quickstart | `api/README.md` | API-specific setup/config. |
| Architecture overview | `APPLICATION_OVERVIEW.md` | High-level system behavior and API/data-flow overview. |
| Maintainer codebase map | `.planning/codebase/*` | Detailed maps for structure, stack, integrations, testing, conventions, and concerns. |
| Implementation design log | `IMPLEMENTATION_DETAILS.md` | Planning and test records for implementation tasks. |
| Future feature RFCs/specs | `MAPPER_WORKSPACE_RFC.md`, `docs/superpowers/specs/*` | Design references, not session-start guidance. |

## Current Runtime Truth

- Supported Compose services: `postgres` and `api`.
- Celery/Redis worker services are not part of the active runtime.
- API startup runs Alembic from the repository root with `python -m alembic upgrade head`.
- The secure jsluice wrapper is canonical.
- `native_sourcemap_processor.py` is canonical; `sourcemap_processor.py` is a compatibility alias.
- Startup recovers orphaned `queued`, `running`, and `cancelling` job rows into terminal states.

## Work Rules

- Match existing style; avoid opportunistic rewrites.
- Do not guess ambiguous behavior. Ask for clarification or document the uncertainty.
- Explain risky refactors before doing them.
- Prefer small, verifiable changes over broad rewrites.
- Use `wishandwash.co.il` for new live/manual/integration validation involving capture, ingestion, sourcemaps, or analysis-on-upload.
- Do not use `example.com` or legacy HoneyBook targets for new validation notes; historical archive entries may still contain them.

## Cleanup Policy

- Keep docs if they are authoritative, actively maintained, or useful design references.
- Delete generated reports, one-off phase notes, and stale debug scripts after their useful content is incorporated into authoritative docs.
- If a document is retained as archive/history, label it clearly and avoid treating it as current setup or runtime guidance.
