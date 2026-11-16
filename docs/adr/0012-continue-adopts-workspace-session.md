---
status: "accepted"
date: 2026-11-16
---

# 0012. Continue may adopt a workspace session by giving it an extension id (server-minted, write-once)

## Context and Problem Statement

A crawl started in the workspace and a capture of the same site by the extension end up as **two
sessions** (issue #158). A workspace session has `session.external_id = NULL`; the extension only
ever continues sessions that already carry an extension id
(`sessions_for_scope` filters `external_id IS NOT NULL`,
`apps/platform/src/recon/sessions/capture_scope.py`), because `external_id` is the key every
ingest route uses to find "its" session (`capture_router._find_session_by_external_id`,
`UNIQUE (tenant_id, external_id)`, `db/models.py` `uq_session_tenant_external_id`).

Letting Continue adopt a workspace session means **giving an existing session an extension id**. That
is a new write path into a session that was not created by the capture path, and the extension id is
the write capability for that session's captures (`capture_lifecycle_router.capture_sessions_for_scope`
docstring). So the design has to settle who may do it, how the extension finds the session, the exact
write, the engagement binding, replay, and what the id's uniqueness becomes.

## Decision Drivers

* The engagement binding is immutable for the life of the session (`GLOSSARY.md`; Continue already
  sends the session's own `engagement_id`, `capture-lifecycle.js` `start`).
* Tenant isolation is Postgres RLS (ADR-0002); an anonymous caller lands in the shared capture tenant
  and must never reach a signed-in tenant's sessions.
* Two extensions may act at once; exactly one may win (ADR-0001's at-least-once world).
* Adopt never overwrites an existing extension id. (Reading an id is already open to any signed-in same-tenant caller: `sessions_router.py` `_summary_dict`, `capture_scope.py` `ScopeMatch`; that boundary is unchanged.)
* Smallest change: reuse the existing Continue flow, ingest routes and unique key unchanged.

## Considered Options

| | Option | Pros | Cons |
|---|---|---|---|
| A | **Server mints the id; one atomic adopt call** (chosen) | The client never chooses a key value; one UPDATE decides the winner; the extension's existing `adopt(externalId)` path is reused | A lost response strands the minted id (self-heals, see failure modes) |
| B | Extension sends its own session UUID; server stores it | Retry-idempotent (same id again = 200) | Client picks the key; must be format-checked; two extensions with the same UUID race into one session; larger change in the extension |
| C | A new `workspace_session_id` column the ingest routes also accept | `external_id` semantics untouched | Every ingest route (`_find_session_by_external_id` and 11 callers) learns a second key; migration; two ways to address a session |
| D | Keep two sessions, join at read time (merge view, #158 option B) | No new write path | Not what the user decided; findings/spec/threat model stay per session |

## Decision Outcome

Chosen option: **A**.

**Who.** Signed-in callers only (`_resolve_ingest_tenant(...)` returning `signed_in=True`, a
server-signed `typ=auth` token), else 401. The tenant comes from the token, never the request, and
every query runs under `tenant_session(tenant_id)` (RLS), so a session of another tenant is
indistinguishable from a missing one (404). The shared anonymous capture tenant is never offered an
adopt. Any signed-in role may adopt, as with "Add to scope" (`add_to_scope`); only `admin` exists today.
The origin lock applies, but it lets a `null` Origin through (`capture_router.py` `_enforce_origin_lock`), so the real protection is the Bearer token. The actor is read from the token with `Depends(get_actor)` (`api/deps.py`), and the route **fails closed (401) when the actor is `None`**, because `_resolve_ingest_tenant` discards the user id.

**How the extension learns what is adoptable.** `GET /api/capture/sessions?scope=` keeps its current
`sessions` list and gains a second list, `workspace`: sessions of this tenant with
`external_id IS NULL`, `archived_at IS NULL` and the entry in `scope_hosts` (same exact-match rule as
today), newest first, capped at `MAX_MATCHES`. Entries carry `session_id`, `engagement_id`, `name`,
run count and last-run time, and **no external id (there is none)**. An older extension ignores the new key.

**The write (one statement).**

```sql
UPDATE session SET external_id = :minted          -- uuid4, minted by the server
 WHERE id = :session_id AND external_id IS NULL AND archived_at IS NULL
RETURNING id, engagement_id;
```

One row → adopted; the response is `{sessionId, externalId, engagementId}` and nothing else changes.
Zero rows → one follow-up read to classify: gone/foreign → 404; archived → 409 `archived`; already has an
id → 409 `already_adopted`, **without** echoing that id. If the read matches none of these (a concurrent un-archive between the two statements), the UPDATE is retried once, then 409 `conflict`. Two concurrent adopts: Postgres row-locks
the row, the second UPDATE re-evaluates `external_id IS NULL` on the committed row and matches
nothing, so exactly one wins (READ COMMITTED re-check). `engagement_id`, `scope_hosts`, `name` and
`created_by` are never in the SET list, and the request body carries no field that reaches the row.

**Then** the extension calls the existing `captureSession.adopt(externalId, {binding: {projectId:
engagementId}})` and `capture/start`; from here the session is an ordinary capture session. No
ingest route changes.

**Uniqueness.** `UNIQUE (tenant_id, external_id)` is unchanged and still the backstop. The id is a
fresh uuid4; an `IntegrityError` (practically impossible) retries once with a new id **in a new transaction** (a failed statement aborts the current one, `db/base.py`), then fails.
`external_id` is **write-once, enforced in the database**: a `BEFORE UPDATE` trigger raises when `OLD.external_id IS NOT NULL AND NEW.external_id IS DISTINCT FROM OLD.external_id` (only `create_session` writes the column today). There is no un-adopt.

**Audit.** A structured log `session.adopted` with `actor` = the token's real `user_id`, tenant,
session id and the engagement id (the pattern for `session.delete_requested`, D48; no `session` event
table exists). The minted id is not logged.

**Replay.** The call is not idempotent by design: a second adopt of the same session is the 409 above.
If the response is lost, the session now has an id nobody remembers; it appears in the *normal*
Continue list (it now matches `external_id IS NOT NULL`) and is continued as any capture session.

**Extension ordering.** The adopt call runs inside `CaptureLifecycle.start`'s `serialize` block, after the
`capture_in_progress` check (`capture-lifecycle.js`), so a refused Start never burns an adopt. On
`already_adopted` / `conflict` the popup re-runs the lookup and offers the session from `sessions`
(it now has an id). The existing note that the session switch is not rolled back now also covers a
permanent server write; a test covers "adopt 200, then `capture/start` fails".

**Everything that changes for an adopted session** (found by the review; each is fixed in C2 or accepted):

| Place | Change | Disposition |
|---|---|---|
| `findings/queries._cross_run_sightings` | the crawl run flips `platform`→`capture` | **fix**: add `Run.target IS NULL`; test |
| `runs/coordinator.rerun` | picks the latest run of any kind; a capture run has no target/input → `NoRunToRerun` (400) | **fix**: pick the latest *reproducible* run (target or `input_ref`); test |
| `sessions/capture_scope.sessions_for_scope` | `captures` and `largest` count the crawl run; the popup's "capture N" uses it | **fix**: count capture runs only (same predicate as `_capture_run`); test |
| `sessions/service._summary/_card_label` | the card shows the latest run's numbers and a derived label | accepted: the card reports the latest run, as for any session with several runs |
| web `BulkDeleteConfirm.tsx` | warns "an extension capture may reappear" for any session with an id | accepted: true while an extension holds the id |
| popup "Add to scope" | now reachable for a session with a crawl target; scope also bounds crawl re-runs (`coordinator.edit_and_rerun` re-attests authorization on widening) | accepted: same signed-in tenant boundary; stated here so it is not a surprise |

### Consequences

* Good: one target is one session with its crawl run and capture run; no new key, route or column.
* Good: the winner of a race is decided by one row-locked UPDATE; the loser changes nothing.
* Bad: a signed-in same-tenant user can adopt any workspace session in their tenant. That is the same
  trust boundary as listing and continuing a capture session today.
* Bad: adoption is permanent. A session adopted by mistake stays an extension session.
* Bad: the response of a successful adopt is the only place the new id is returned once; lost-response
  recovery is via the normal Continue list, not a retry of adopt.
* Bad: a migration (the write-once trigger) is added.

### Confirmation

Tests (C2, #223), each a failing test first: signed-out and anonymous → 401; a session of another
tenant → 404 and unchanged; a session with an id → 409, id not in the body, row unchanged; archived →
409; two concurrent adopts → exactly one 200 and one 409, one id stored; `engagement_id` and
`scope_hosts` unchanged after adopt; the `session.adopted` log carries the token's user id; the
sightings origin of the pre-adopt crawl run stays `platform`; the listing never contains a session that
has an id. Also: the trigger refuses to change a set id; re-run after adopt re-crawls the crawl run; the Continue list counts capture runs only; the route is 401 without an actor; adopt 200 then a failing `capture/start` leaves a continuable session. The write lives in one function in `recon/sessions/capture_scope.py`.

## More Information

Issues #213 (this design), #223 (build), #158 (the idea), #179. Spec:
`apps/platform/docs/superpowers/specs/2026-11-15-research-workflow-scope-and-popup-design.md`
(slices C1, C2). ADR-0010 "More Information". Related: ADR-0002 (RLS), ADR-0009 (runtime capture).

**Review outcome (2026-11-16): accepted with conditions.** An adversarial subagent (Fable) tried to
disprove the design with code lines. It **could not break** tenant isolation (RLS ENABLE+FORCE with
USING and WITH CHECK, `0001_initial.py`; non-superuser engine, `db/base.py`; the anonymous tenant is
never `signed_in`, `capture_router.py`), the race (a READ COMMITTED UPDATE re-checks its WHERE after
waiting on the row lock, PostgreSQL docs 13.2.1; archive, delete and scope edits take the same row
lock; the repo already uses this idiom at `capture_router.py`), or the engagement binding
(`_get_or_create_session` returns the existing row before `projectId` is read;
`seed_scope_hosts_if_empty` never overwrites a scope). It **did find**, now folded in above: the
"single side effect" claim was wrong (re-run, Continue counts, card, bulk-delete, Add to scope); the actor
was not available on the route as first designed; the zero-row classification had an unhandled case;
the extension ordering was unspecified; "409 without the id" is not a control by itself (ids are
already listed to same-tenant callers), so the driver was reworded; the origin lock does not stop a
`null` Origin; the retry needs its own transaction; write-once should be a trigger. Not proven from
code: that the deployed `database_url` is the `recon_app` role. **C2 must satisfy every condition above.
Status stays `proposed` until the user approves this design.**
