# REQ-C2 manual base-URL resolution — read-time overlay (design)

- **Date:** 2026-07-29
- **Status:** approved (brainstorming); **§4 adversarial design gate PASSED (2026-07-29, Opus) — BUILD WITH
  CHANGES**, three blockers folded into §4/§5 before this record (gate record + resolutions in §12).
  Higher-model whole-branch review (§4 gate 2) owed after build.
- **Slice:** a small slice that lets an analyst **manually set a base URL** and have it **re-resolve the
  findings that depend on it**, across files — the deferred REQ-C2 SHOULD. One new pure resolver, one new
  session-scoped table, one thin route, two call-sites (reconstruct + classify) that apply the resolver, and
  an order-independent request-merge.
- **Primary REQ:** REQ-C2 — *"custom wrappers are taught by mapping a call shape; cross-file base URLs are
  resolved via a manual set-base-URL that re-resolves dependents"*
  (`Javascript recon app redesign/Developer Requirements.dc.html:358`). This slice builds the **second
  clause** (manual set-base-URL + re-resolve dependents). The first clause (**wrapper-teaching**) is a
  deliberate fast-follow (§9) that writes into the same model.
- **Consumers (already built):** the **shadow classifier** (accuracy — moves partial cross-file paths out
  of `unresolved` into `documented`/`shadow`), the **OpenAPI export** (fills the `{{base_url}}` gap), and
  transitively the **Slice 4 threat model** (consumes `reconstruct_run`).

## 1. Context

What already ships (verified against `HEAD` `b66cfba`; corrects the stale memory that said `axios.create`
is dropped):

- The extractor resolves a **conservative literal subset of bases single-file**: `axios.create({ baseURL })`
  (`extract.py:244-245,463-465`, test `extract_test.py:245-247`), `axios.defaults.baseURL`
  (`extract.py:250-253`), and a leading `${CONST}` prefix (`extract.py:247-249,360-406`). The join is
  prepend-only via `_join_base`/`_resolve_url` (`extract.py:351-357,409-423`).
- **`host` is kept off the finding hash on purpose** so a base re-resolution cannot churn finding identity
  (`normalize.py:167-168` docstring; `finding_hash` hashes `{type, value, path}` only, `:302-315`; host is
  split out per-occurrence at `:224-229`). **But the path/prefix IS part of `value`/`path`, so it is
  hashed** — this is why re-resolution must be a read-time overlay, never a mutation (§3, §5).
- The **3-bucket shadow classifier** already exists and is honest: `classify_operation`
  (`classify.py:186-225`) routes a base-unresolved path (leading `${...}`, `is_partial`,
  `classify.py:96-112`) to `unresolved`, and a bare-tail match against a *different* documented op to
  `unresolved`/`suffix-verify` (step 4, `:206-210`) **before** any `shadow` verdict. A run-level
  `base_url_incompleteness_ratio` self-audit signal already flags likely-missing base resolution
  (`SpecSummary`, `classify.py:240`; `summarize`, `:243-289`).

**The two client-operation sources this slice must reach** (verified — they do **not** share one choke
point):

1. `reconstruct_run(tenant_id, run_id)` (`reconstruct.py:130-136`) → `build_requests` (`:53`) → consumed by
   the OpenAPI export (`export_router.py:36`), the curl/HTTP serializers (`probe_router.py:33`), and the
   Slice 4 threat model (in-process). Recomputed **every call** (no persistence).
2. The shadow classifier's write path `_classify_session` (`service.py:156-218`) reads
   `models.Finding.value` **directly from the DB** (`:182-194`) and **upserts persisted verdicts** into
   `finding_spec_status`. Triggered by `attach_and_classify` (`service.py:45`) and `reclassify_run`
   (`:83`, the analyze-finalize hook).

**The gap:** there is no persisted base data model and no way to set a base after extraction — a base that
lives in another file (or is a non-literal expression) leaves its dependent calls **relative**, which is
correct-but-incomplete: they land in `unresolved` and render `{{base_url}}` in probe output
(`serialize.py:22,48`).

**Binding platform constraint:** unchanged. This slice adds **no active traffic and no new egress**
(REQ-P1/P2) — it is pure static resolution of analyst-supplied bases over already-stored findings.

## 2. Settled decisions

Binding for this slice unless re-opened. (Each was chosen in brainstorming; the alternatives and why they
lost are in §10.)

1. **Manual set-base-URL only.** Wrapper-teaching and automatic cross-file base inference are **out of
   scope** (§9). The analyst supplies the base; the tool re-resolves dependents.
2. **Read-time overlay, never a mutation.** A base rule is applied by a **pure resolver at read time**; the
   stored `Finding` rows and their hashes are **never rewritten** (path is inside `finding_hash`, so a
   rewrite would churn identity and break occurrence dedup + verdict keys). This is the whole reason `host`
   was kept off the hash.
3. **Targeting = analyst selection or a path-prefix rule.** Not per-run/per-source (too blunt for a bundle
   that calls several services) and not auto-by-symbol (needs extractor changes — deferred, §9).
4. **Session-scoped persistence**, mirroring `session_spec` / `finding_spec_status` — a base learned about
   an app is stable across continuous rescans (REQ-D5), like the attached spec and its verdicts.
5. **Prepend-only, segment-boundary, idempotent, relative-only.** The resolver only **prepends** a base to a
   path (never rewrites/truncates — upholding the classifier's SAFETY INVARIANT, `classify.py:170-185`);
   prefix matching is on **whole path segments**; applying a rule twice is a no-op; and it applies **only to
   host-less/relative ops** (§5, gate Blocker B1).
6. **Set-base triggers reclassify.** So persisted `finding_spec_status` verdicts never go stale. Read-time
   stays true for export/probe/threat-model (they recompute every call).
7. **UI this slice = React component + Vitest; live in-container walkthrough deferred** (image rebuild), as
   in UI-0 / X / Y.

## 3. Components (files)

```
src/recon/findings/base_url.py        NEW  pure: resolve_base(request|value, rules) — match + prepend-only
src/recon/findings/base_url_test.py   NEW  host-lane, colocated
src/recon/spec/base_url_service.py    NEW  store/list/delete a rule, then trigger reclassify
src/recon/spec/base_url_service_test.py NEW integration, live PG
src/recon/api/base_url_router.py      NEW  POST/GET/DELETE /runs/{run_id}/base-url
src/recon/api/base_url_router_test.py NEW  integration, mirrors spec_router_test.py
src/recon/db/models.py                EDIT add SessionBaseUrl model + register in the RLS table tuple (:438-461)
src/recon/migrations/versions/<next>_session_base_url.py NEW create_all + RLS policy (mirror 0006_spec_diff)
src/recon/probe/reconstruct.py        EDIT apply resolver POST param-join (assembled request) + order-independent merge
src/recon/spec/service.py             EDIT _classify_session: select per-hash occurrence-host presence, apply resolver before classify_operation
src/recon/api/app.py                  EDIT include_router(base_url_router) before the SPA catch-all
web/src/...                           NEW  base-URL panel component + Vitest
```

The pure resolver lives in `findings/base_url.py` — a **shared** home, because both consumers already
depend on `findings` (`spec.classify` imports `findings.extract`/`findings.normalize`; `probe.reconstruct`
imports `findings.queries`). Neither feature reaches into the other's internals.

`base_url.py` does **no DB work** and **no finding logic** — it shapes a path against a set of rules. The
store/trigger split keeps `base_url_service.py` single-purpose (persist + call the existing
`spec.service.reclassify_run`) and under the size guideline.

## 4. Data model — `session_base_url`

Session-scoped, tenant-RLS, mirroring `session_spec` / `finding_spec_status` (same tenant policy, same
`on_conflict` upsert convention as `probe/triage.py`).

```
session_base_url
  id             uuid    primary key
  tenant_id      uuid    NOT NULL          -- RLS policy column, as on every session-scoped table
  session_id     uuid    NOT NULL          -- fk -> session
  kind           text    NOT NULL  CHECK (kind IN ('prefix','selection'))
  path_prefix    text    NULL              -- set iff kind='prefix'   (leading '/', segment sequence)
  finding_hashes text[]  NULL              -- set iff kind='selection'
  base_url       text    NOT NULL          -- 'https://api.example.com/v3'  OR  '/location'
  actor          text    NULL              -- best-effort label, like triage/spec
  created_at     timestamptz NOT NULL default now()
  updated_at     timestamptz NOT NULL default now()
  CHECK ( (kind='prefix'    AND path_prefix   IS NOT NULL AND finding_hashes IS NULL)
       OR (kind='selection' AND finding_hashes IS NOT NULL AND path_prefix    IS NULL) )
  UNIQUE (session_id, path_prefix)          -- prefix rules upsert; selection rules insert + delete-by-id
```

- **Migration (gate Blocker B3 — corrected).** A new Alembic revision in `src/recon/migrations/versions/`
  (the real, and only, alembic-scanned location — **not** a top-level `alembic/`). A brand-new table follows
  the `0006_spec_diff` precedent — `Base.metadata.create_all(bind)` + RLS layering
  (`migrations/versions/0006_spec_diff.py:10-12,30-43`) — **not** the `0003` `ADD COLUMN IF NOT EXISTS`
  pattern (that is for adding columns to existing tables). The table is registered in `models.py`'s RLS
  table tuple (mirroring the spec tables, `models.py:438-461`).
- **RLS policy (gate Blocker B3 — corrected).** The table carries the **exact** policy every session-scoped
  table uses (`0006_spec_diff.py:40-41`, `0004_finding_triage.py:40-41`) — copied verbatim, plus the same
  `GRANT` block:
  ```
  USING      (tenant_id::text = current_setting('app.current_tenant', true))
  WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true))
  ```
  The real GUC is `app.current_tenant` (set via `set_config('app.current_tenant', :tid, true)`,
  `db/base.py:53`) — **not** `recon.tenant_id`; the `::text` cast is required (the column is `uuid`); the
  `, true` missing-ok flag stops `current_setting` raising on an unset GUC; and the `WITH CHECK` is
  load-bearing because this table is **written** by POST/DELETE (REQ-S1 write-side tenant enforcement under
  `FORCE ROW LEVEL SECURITY`).
- **`kind` enum.** Uses the `recon.domain` StrEnum + shared `_enum_check` convention (`models.py:41-43`;
  `src/recon/domain.py`), so the two values live in one place, not duplicated across model CHECK + service +
  API (the same DRY the debt doc recommends for `TriageStatus`, `docs/slice2-deferred-debt.md:75`).

## 5. The resolver + mapping semantics (the nuanced part)

`resolve_base` is pure, total, stdlib-only. It matches a rule and **prepends** — it never rewrites/truncates
(upholding the classifier SAFETY INVARIANT, `classify.py:170-185`).

**Application point (gate Blocker B2 — never before the endpoint↔param join).** `build_requests` joins
endpoint findings to their query/body params by **matching operation-key strings** (endpoints keyed at
`reconstruct.py:64`, params **independently** at `:67`, joined at `:71,94`). If the resolver rewrote the
endpoint key *before* that join, the param group key (still `GET /address/search`) would no longer match the
re-based endpoint key (`GET /location/address/search`) and **every query/body param would be silently
dropped** — a REQ-C2 honesty regression. So:
- In `reconstruct_run`, the resolver is applied **after** the join, on the assembled `ReconstructedRequest`
  (path + hosts, params already attached); param findings ride along with their endpoint and need no hash
  match. (This also settles that a `selection` rule lists **endpoint** hashes, `reconstruct.py:45,124`, and
  could never match a param finding's own distinct hash — post-join, it doesn't need to.)
- In `_classify_session`, the resolver is applied per-finding to the `value` string just before
  `classify_operation`.

**Candidate gate — evaluated with occurrence-host presence on BOTH application points (gate Blocker B1).**
A base is applied only to an op with **no resolved host** and a root-relative path (begins `/`). An
already-absolute op is returned unchanged — no double-join, and, critically, **no false `shadow`** from
re-basing an op that was already complete. The subtlety: a stored `Finding.value` is **always host-less**
(`normalize.py:220-221,228-229` — the host is split onto the occurrence, off the value/hash), so
host-presence is **not** visible from the value alone. Therefore:
- In `reconstruct_run`, candidacy uses `ReconstructedRequest.hosts` (`reconstruct.py:39,74-79`) — skip if
  non-empty.
- In `_classify_session`, the select MUST additionally carry a per-hash *"any occurrence has a host"* flag
  (a correlated `EXISTS`/join onto `finding_occurrence.host`, `models.py:287`), so classify applies the
  **identical** relative-only gate and cannot flip an originally-absolute op to a false `shadow`.

Without this, the two points disagree (export skips the op, classify re-bases it). WS/WSS and other non-HTTP
ops are never candidates (already excluded from export `paths` and forced to `unresolved` by the classifier).

**Rule match (at most one rule applies; deterministic precedence).**
1. **`selection`** — applies iff any of the op's endpoint hashes ∈ the rule's `finding_hashes`. Beats every
   prefix rule (explicit intent wins over a heuristic).
2. **`prefix`** — applies iff the op's path starts with `path_prefix` **on segment boundaries** (so
   `/address` matches `/address/search` but not `/address-svc/...`). Among matching prefix rules, the
   **longest** `path_prefix` (most segments) wins. `path_prefix` only **selects** which ops the rule
   applies to — it is **never stripped** from the path; `base_url` is prepended to the whole path. (So
   `path_prefix='/address'`, `base_url='/location'` turns `/address/search` into `/location/address/search`,
   not `/location/search`.)
3. **Tie-break** — most-recent `updated_at`. Cannot occur for prefix rules (unique per session); defined for
   overlapping `selection` rules.

**Applying the matched rule.** Split `base_url` into an optional `scheme://host[:port]` and a `path-prefix`:
- **path-prefix** → **prepended** to the whole op path (`base_path + path`), so the op's own literal suffix
  is preserved intact. This drives classify path-matching, the reconstruct path, and the export path.
- **scheme+host** (when present) → added to the reconstructed op's `hosts`, and — because reconstructed
  hosts are bare hostnames that both emitters otherwise default to `https://` (`openapi.py:257`,
  `serialize.py:48`) — the resolver must also synthesize an `example_url` carrying the base's scheme+port so
  `_servers` honors it (`openapi.py:248-255`), reusing the openapi slice's IPv6-bracketing and
  userinfo-stripping (`openapi.py:250-251`, commit `1e24e66`) (gate note N2). Fills export `servers` + the
  probe `{{base_url}}` placeholder. **Classify matches on path only** — its operation values are host-less
  by construction (`normalize.py:218-221`), so a host-bearing base changes export/probe but not the shadow
  verdict.

**Idempotence.** Segment-boundary matching already prevents a second prepend (after `/address/search` →
`/location/address/search`, the path no longer starts with the `/address` segment prefix); a belt-and-braces
guard also skips when the path already begins (segment-wise) with `base_url`'s path. So reclassify is safely
re-runnable and `resolve_base ∘ resolve_base = resolve_base`.

**Collision-merge in reconstruct (gate note N3).** Applying the overlay can map two operations onto the same
`(method, path)` (a relative `/address/search` resolving onto an already-absolute `/location/address/
search`). `reconstruct_run` merges colliding `ReconstructedRequest`s — union hosts/query/body/
`endpoint_hashes`, with a deterministic `example_url` pick — rather than dropping either. This is a **new**
order-independent merge over `ReconstructedRequest`; the OpenAPI builder's merge (`openapi.py:174-186`)
operates on OpenAPI dicts and is **not** reusable here. Because `build_requests` input order determines
grouping and its determinism is currently untested (`docs/slice2-deferred-debt.md:77`), this slice adds a
`build_requests` input-permutation test alongside the merge. (So this is more than a one-line call-site.)

Worked example — the canonical false positive (`docs/shadow-api-false-positives.md:59-69`):

```
extracted finding value:   GET /address/search            (axios.create baseURL lives in another file)
spec documents:            GET /location/address/search
analyst sets:              kind=prefix  path_prefix='/address'  base_url='/location'
                           (or: kind=selection  finding_hashes=[<hash>]  base_url='/location')

resolve_base            →  GET /location/address/search
classify:  unresolved/suffix-verify  →  documented    (finding_spec_status re-tagged by reclassify)
export:    {{base_url}}  →  /location/address/search
```

If `base_url` were `https://api.example.com/location`, export additionally emits
`servers: [https://api.example.com]` and the curl/HTTP serializers drop the `{{base_url}}` placeholder — all
through the single `reconstruct_run` path.

## 6. Route & reclassify

`base_url_router`, mirroring `spec_router` (`Depends(get_tenant_id)`, run→session resolution, DB work off
the event loop via `run_in_threadpool`, `None` → 404), registered in `app.py` **before** the SPA catch-all
(`app.py:36-41`, before `_mount_spa` at `:50`):

- `POST /runs/{run_id}/base-url` — body `{kind, path_prefix? | finding_hashes?, base_url}` → validate →
  upsert the rule into the run's **session** → `reclassify_run` → `200 {rule, summary}`.
- `GET /runs/{run_id}/base-url` — the session's current rules.
- `DELETE /runs/{run_id}/base-url/{rule_id}` → delete → `reclassify_run` → `204`.

`reclassify_run` (`service.py:83`) already re-runs `_classify_session` from the session's attached spec; once
`_classify_session` applies the resolver (§5), a set-base/delete re-tags `finding_spec_status` with no other
change. If **no** spec is attached, reclassify is a no-op (`service.py:101-102`) — that is fine: reconstruct
and export still reflect the rules live.

**Two-transaction note (gate note N5).** `base_url_service` persists the rule in one `tenant_session`, then
`reclassify_run` opens its own (`service.py:92`) — unlike `attach_and_classify`'s single session
(`service.py:60-80`). A crash between them leaves the rule stored but verdicts un-retagged; this is harmless
(reconstruct/export reflect rules live, and reclassify is idempotent and re-runnable) and is accepted for
this slice rather than refactored into a shared session.

## 7. Error handling

- Unknown / other-tenant run → **404** (RLS: the run lookup returns `None`, as in `attach_and_classify`).
- Unknown `rule_id` on delete → **404**.
- Invalid `base_url` → **422**: not parseable, a scheme other than `http`/`https` when a host is present, a
  path-only value not beginning `/`, or a value carrying a query/fragment. (Validation reuses the openapi
  slice's userinfo/IPv6 handling, `openapi.py:250-251`, so a host-bearing base round-trips.)
- Malformed `kind` / the wrong match field for the kind / empty `finding_hashes` → **422**.
- The resolver is pure and total (it cannot raise on well-formed rows), so there is no builder-style
  broad-except needed on the read path; validation happens at the write boundary (the route).

## 8. Testing (host-lane pure + integration + FE)

- **`findings/base_url_test.py` (host-lane, pure):** prefix join and selection join; a base with scheme+host
  vs path-only; precedence (selection beats a longer prefix; longest prefix wins); segment-boundary match
  (`/address` does **not** match `/address-svc`); **an originally-absolute op (occurrence has a host) is NOT
  re-based by a prefix rule** (B1); a **re-based op keeps its query/body params** via the post-join
  application (B2); a host-bearing base populates `example_url` with scheme+port and survives IPv6/userinfo
  (N2); idempotence (`resolve_base` twice == once); a leading-`${...}` op resolved via a **selection** rule;
  a non-candidate op returned unchanged; **`build_requests` input-permutation determinism + order-independent
  collision-merge** (N3).
- **`spec/base_url_service_test.py` + `api/base_url_router_test.py` (integration, live PG):** POST a rule →
  `reconstruct_run` reflects it and the export `{{base_url}}` is filled; the canonical
  `unresolved → documented` flip in `finding_spec_status` and the `base_url_incompleteness_ratio` moving;
  an originally-absolute documented op stays `documented` after a broad prefix rule (B1 end-to-end); GET
  lists; DELETE re-resolves back; unknown run / other-tenant run → 404 (RLS); invalid base / kind → 422; a
  no-spec session POST succeeds and is a reclassify no-op.
- **FE:** a base-URL panel (list rules, add prefix/selection, delete). Because identity is non-churn, the
  panel/findings view must show `matched_operation` (the resolved documented op) alongside the unchanged raw
  `value` so the analyst isn't confused by `/address/search` sitting next to a `documented` verdict (gate
  note N4). Vitest coverage; live in-container walkthrough deferred (image rebuild), as in UI-0 / X / Y.

## 9. Out of scope / deferred (fast-follow)

- **Wrapper-teaching (REQ-C2 first clause).** Teach a custom `fetch`/`axios` wrapper by mapping a call shape
  so its calls are attributed to a path + base. The higher-recon-value follow-on (it recovers endpoints that
  are dropped/unattributed today — missing attack surface), deferred because it needs a fuzzier matcher and
  more config surface; it writes into this same `session_base_url` model.
- **Automatic cross-file base inference (by unresolved base symbol).** Have the extractor surface each
  unknown base token (`baseURL: CONFIG.API`) and its dependent endpoints, so the analyst only fills a value.
  Best UX and closest to "re-resolves dependents", but requires extractor + persistence changes — a bigger,
  riskier build than a first slice warrants.
- **Static request-header/auth extraction (the paired C2 thread, `docs/slice2-deferred-debt.md:69`).** Still
  the manual tester's to add; unchanged by this slice.
- **Per-run / per-source coarse base.** Rejected as too blunt for multi-service bundles.

## 10. Open items / risks

- **Prefix foot-gun.** A prefix rule prepends to **every** relative op that starts with its segment prefix;
  an over-broad prefix could base ops the analyst did not intend. Mitigated by segment-boundary matching, the
  relative-only candidate gate on **both** application points (B1), idempotence, and `selection` as the
  precise escape hatch. The analyst owns precision — the rule is explicit intent, surfaced back in `GET`.
- **Base carrying a host vs the existing single-file resolution.** When extraction already resolved a host
  single-file, the op carries a host → the candidate gate skips it, so a later host-bearing rule cannot
  conflict (B1).
- **Selection rules reference `finding_hash`es.** If a rescan changes which findings exist, a stale selection
  hash simply matches nothing (harmless); `GET` shows the rule so the analyst can prune it.
- **Reclassify latency + two-transaction (N5).** `_classify_session` is a bounded, pure in-process pass over
  the session's DISTINCT endpoints (`service.py:182-190`); running it synchronously on set-base matches how
  `attach_and_classify` already behaves. The rule-persist and reclassify run in separate sessions (§6, N5) —
  accepted as harmless/idempotent.

## 11. REQ traceability

| REQ | How this slice touches it |
|---|---|
| REQ-C2 (second clause) | Manual set-base-URL that re-resolves dependents across files — the deferred SHOULD, built here. Wrapper-teaching (first clause) is the fast-follow (§9). |
| REQ-S1 | Tenant-scoped read/write via `tenant_session` RLS (`USING` + `WITH CHECK`, §4); unknown/other-tenant run → 404. |
| REQ-P1 / REQ-P2 | Respected — pure static resolution of analyst-supplied bases; no active traffic, no new egress. |
| REQ-D5 | Session-scoped rules + reclassify keep verdicts correct across continuous rescans. |
| — (consumers) | Sharpens the shadow classifier (an approved extension) and fills the OpenAPI export base; feeds the Slice 4 threat model via `reconstruct_run`. |

## 12. §4 adversarial design gate (2026-07-29)

Opus adversarial reviewer, proof-bound (every objection cited exact `file:line` from this repo).
**Verdict: BUILD WITH CHANGES.** Confirmed sound under attack (each held): identity non-churn (`finding_hash`
hashes `{type,value,path}` only, `normalize.py:302-315`; overlay never writes back; `finding_spec_status`
keyed on `finding_hash`, `models.py:417`; `matched_operation` is the *documented* op, `classify.py:130-142`);
selection feasibility (endpoint hashes exposed at both points, `reconstruct.py:45,124` / `service.py:193`);
classify step order untouched (`classify.py:202-219`); SPA route order (`app.py:36-41,50,71-75`);
`UNIQUE(session_id, path_prefix)` with `NULLS DISTINCT` for selection rows; the `recon.domain` enum
convention (`models.py:41-43`); REQ-P1/P2. Three blockers folded before this record:

| # | Finding (proof) | Resolution (folded) |
|---|---|---|
| **B1** | The "skip already-absolute op" candidate gate cannot be evaluated in `_classify_session`: a stored `value` is always host-less (`normalize.py:220-221,228-229`) and the classify select carries only value (`service.py:182-183`), so a prefix rule would prepend to an originally-absolute op → **false `shadow`** (breaks the §12 no-false-shadow claim). `reconstruct` *can* gate (it has `ReconstructedRequest.hosts`, `reconstruct.py:39`) → the two points disagree. | §5 candidate gate rewritten: thread a per-hash "any occurrence has a host" flag into `_classify_session` (join `finding_occurrence.host`, `models.py:287`) so both points apply the identical relative-only gate. |
| **B2** | `build_requests` joins endpoint↔param findings by matching operation-key strings (`reconstruct.py:64,67,71,94`). Resolving the endpoint key *before* the join makes the param key no longer match → **all query/body params silently dropped**; selection rules (endpoint hashes only) structurally can't re-key params. | §5 pins the application point **after** the join, on the assembled `ReconstructedRequest` (params already attached); resolver operates on the request, not the pre-group key. |
| **B3** | The stated RLS SQL/GUC/migration were wrong: GUC is `app.current_tenant` not `recon.tenant_id` (`db/base.py:53`); missing `::text` cast, `, true` flag, and `WITH CHECK`; and a new table uses the `0006_spec_diff` create_all+RLS precedent, not `0003`'s `IF NOT EXISTS`; migration path is `src/recon/migrations/versions/` not `alembic/versions/`. | §3/§4 corrected: exact `0006`/`0004` policy + GRANT copied verbatim, table registered in the `models.py:438-461` tuple, correct migration path. |

Non-blocking, folded: host-bearing base must synthesize `example_url` + reuse IPv6/userinfo handling (N2,
§5/§7); a new order-independent `ReconstructedRequest` merge + `build_requests` permutation test — not the
openapi dict merge (N3, §5/§8); FE surfaces `matched_operation` beside the unchanged value (N4, §8);
two-transaction set-base accepted as idempotent (N5, §6). Migration-path fix also covers gate note N1.
