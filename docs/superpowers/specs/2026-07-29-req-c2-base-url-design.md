# REQ-C2 manual base-URL resolution — read-time overlay (design)

- **Date:** 2026-07-29
- **Status:** approved (brainstorming). The **§4 adversarial design gate is owed next**, before the
  implementation plan. Higher-model whole-branch review (§4 gate 2) owed after build.
- **Slice:** a small slice that lets an analyst **manually set a base URL** and have it **re-resolve the
  findings that depend on it**, across files — the deferred REQ-C2 SHOULD. One new pure resolver, one new
  session-scoped table, one thin route, and two one-line call-sites that apply the resolver.
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
5. **Prepend-only, segment-boundary, idempotent.** The resolver only **prepends** a base to a path (never
   rewrites/truncates — upholding the classifier's SAFETY INVARIANT, `classify.py:170-185`); prefix
   matching is on **whole path segments**; applying a rule twice is a no-op.
6. **Set-base triggers reclassify.** So persisted `finding_spec_status` verdicts never go stale. Read-time
   stays true for export/probe/threat-model (they recompute every call).
7. **UI this slice = React component + Vitest; live in-container walkthrough deferred** (image rebuild), as
   in UI-0 / X / Y.

## 3. Components (files)

```
src/recon/findings/base_url.py        NEW  pure: resolve_base(path, op_hashes, rules) -> (path', host|None)
src/recon/findings/base_url_test.py   NEW  host-lane, colocated
src/recon/spec/base_url_service.py    NEW  store/list/delete a rule, then trigger reclassify
src/recon/spec/base_url_service_test.py NEW integration, live PG
src/recon/api/base_url_router.py      NEW  POST/GET/DELETE /runs/{run_id}/base-url
src/recon/api/base_url_router_test.py NEW  integration, mirrors spec_router_test.py
src/recon/db/models.py                EDIT add SessionBaseUrl model
alembic/versions/<next>_session_base_url.py NEW guarded table + RLS policy (next sequential revision)
src/recon/probe/reconstruct.py        EDIT apply the resolver inside reconstruct_run
src/recon/spec/service.py             EDIT apply the resolver in _classify_session before classify_operation
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
  tenant_id      text    NOT NULL          -- RLS policy column, as on every session-scoped table
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

- New guarded Alembic revision (the `ADD ... IF NOT EXISTS` pattern the debt doc requires,
  `docs/slice2-deferred-debt.md:119`) + the RLS `USING (tenant_id = current_setting('recon.tenant_id'))`
  policy that every session-scoped table carries.
- The `domain` enum convention (a `recon.domain` StrEnum + a shared `_enum_check`, as
  `docs/slice2-deferred-debt.md:75` recommends for `TriageStatus`) is used for `kind` so the two values live
  in one place, not duplicated across model CHECK + service + API.

## 5. The resolver + mapping semantics (the nuanced part)

`resolve_base(path, op_hashes, rules) -> (path, host | None)` — pure, total, stdlib-only.

**Candidate gate.** Only a **host-less, root-relative** path (begins `/`) is eligible. An op that already
carries a resolved host, or whose path does not begin `/`, is returned unchanged — a base is never applied
to an already-absolute op (no double-join). WS/WSS and other non-HTTP ops are never candidates (they are
excluded from export `paths` and forced to `unresolved` by the classifier already).

**Rule match (at most one rule applies; deterministic precedence).**
1. **`selection`** — applies iff any of the op's `op_hashes` ∈ the rule's `finding_hashes`. Beats every
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
  is preserved intact (upholds the classifier SAFETY INVARIANT — prepend, never rewrite). This drives
  classify path-matching, the reconstruct path, and the export path.
- **scheme+host** (when present) → added to the reconstructed op's `hosts` (and the example scheme), which
  drives export `servers` and fills the probe `{{base_url}}` placeholder. **Classify matches on path only**
  — its operation values are host-less by construction (`normalize`'s `endpoint_operation` builds the value
  with no host, `normalize.py:218-221`), so a host-only base changes export/probe but not the shadow verdict.

**Idempotence.** Segment-boundary matching already prevents a second prepend (after `/address/search` →
`/location/address/search`, the path no longer starts with the `/address` segment prefix); a belt-and-braces
guard also skips when the path already begins (segment-wise) with `base_url`'s path. So reclassify is safely
re-runnable and `resolve_base ∘ resolve_base = resolve_base`.

**Collision-merge in reconstruct.** Applying the overlay can map two operations onto the same
`(method, path)` (e.g. a relative `/address/search` resolving onto an already-absolute `/location/address/
search`). `reconstruct_run` applies the resolver as it builds, and **merges** colliding operations (union
params/body/hosts) rather than dropping either — the same collision-merge the OpenAPI builder already does.

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
the event loop via `run_in_threadpool`, `None` → 404), registered in `app.py` **before** the SPA catch-all:

- `POST /runs/{run_id}/base-url` — body `{kind, path_prefix? | finding_hashes?, base_url}` → validate →
  upsert the rule into the run's **session** → `reclassify_run` → `200 {rule, summary}`.
- `GET /runs/{run_id}/base-url` — the session's current rules.
- `DELETE /runs/{run_id}/base-url/{rule_id}` → delete → `reclassify_run` → `204`.

`reclassify_run` (`service.py:83`) already re-runs `_classify_session` from the session's attached spec; once
`_classify_session` applies the resolver (§3), a set-base/delete re-tags `finding_spec_status` with no other
change. If **no** spec is attached, reclassify is a no-op (`service.py:101-102`) — that is fine: reconstruct
and export still reflect the rules live.

## 7. Error handling

- Unknown / other-tenant run → **404** (RLS: the run lookup returns `None`, as in `attach_and_classify`).
- Unknown `rule_id` on delete → **404**.
- Invalid `base_url` → **422**: not parseable, a scheme other than `http`/`https` when a host is present, a
  path-only value not beginning `/`, or a value carrying a query/fragment.
- Malformed `kind` / the wrong match field for the kind / empty `finding_hashes` → **422**.
- The resolver is pure and total (it cannot raise on well-formed rows), so there is no builder-style
  broad-except needed on the read path; validation happens at the write boundary (the route).

## 8. Testing (host-lane pure + integration + FE)

- **`findings/base_url_test.py` (host-lane, pure):** prefix join and selection join; a base with scheme+host
  vs path-only; precedence (selection beats a longer prefix; longest prefix wins); segment-boundary match
  (`/address` does **not** match `/address-svc`); no double-join on an already-absolute op; idempotence
  (`resolve_base` twice == once); a leading-`${...}` op resolved via a **selection** rule; a non-candidate
  op returned unchanged.
- **`spec/base_url_service_test.py` + `api/base_url_router_test.py` (integration, live PG):** POST a rule →
  `reconstruct_run` reflects it and the export `{{base_url}}` is filled; the canonical
  `unresolved → documented` flip in `finding_spec_status` and the `base_url_incompleteness_ratio` moving;
  GET lists; DELETE re-resolves back; unknown run / other-tenant run → 404 (RLS); invalid base / kind → 422;
  a no-spec session POST succeeds and is a reclassify no-op.
- **FE:** a base-URL panel (list rules, add prefix/selection, delete, show the re-resolved path) + Vitest.
  Live in-container walkthrough deferred (image rebuild) — the same deferral recorded for UI-0 / X / Y.

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
  an over-broad prefix could base ops the analyst did not intend. Mitigated by segment-boundary matching,
  the candidate gate (relative-only), idempotence, and `selection` as the precise escape hatch. The analyst
  owns precision — the rule is explicit intent, surfaced back in `GET`.
- **Base carrying a host vs the existing single-file resolution.** When extraction already resolved a host
  single-file, a later host-bearing base rule is redundant; the candidate gate (already-absolute → skip)
  keeps it from conflicting.
- **Selection rules reference `finding_hash`es.** If a rescan changes which findings exist, a stale
  selection hash simply matches nothing (harmless); `GET` shows the rule so the analyst can prune it.
- **Reclassify latency.** `_classify_session` is a bounded, pure in-process pass over the session's DISTINCT
  endpoints (`service.py:182-190`); running it synchronously on set-base matches how `attach_and_classify`
  already behaves. No async path added.

## 11. REQ traceability

| REQ | How this slice touches it |
|---|---|
| REQ-C2 (second clause) | Manual set-base-URL that re-resolves dependents across files — the deferred SHOULD, built here. Wrapper-teaching (first clause) is the fast-follow (§9). |
| REQ-S1 | Tenant-scoped read/write via `tenant_session` RLS; unknown/other-tenant run → 404. |
| REQ-P1 / REQ-P2 | Respected — pure static resolution of analyst-supplied bases; no active traffic, no new egress. |
| REQ-D5 | Session-scoped rules + reclassify keep verdicts correct across continuous rescans. |
| — (consumers) | Sharpens the shadow classifier (an approved extension) and fills the OpenAPI export base; feeds the Slice 4 threat model via `reconstruct_run`. |

## 12. §4 adversarial design gate (2026-07-29)

Owed next, before the implementation plan. Attack surface to hand the adversarial reviewer explicitly:
- The **prepend-only + SAFETY-INVARIANT** claim (`classify.py:170-185`): prove the overlay cannot create a
  false `documented` (base prepended so a client path spuriously equals a spec path) or a false `shadow`.
- The **two-application-point** claim: prove `reconstruct_run` and `_classify_session` are the only client-op
  sources, and that applying the resolver in both (plus reclassify-on-set) leaves no stale verdict.
- **Identity non-churn:** prove the overlay never touches `finding_hash` (path/value stay as stored).
- **Idempotence + collision-merge** under repeated reclassify and under a relative op resolving onto an
  existing absolute op.
- **RLS/404 seam**, **SPA route order**, and every `file:line` citation above.
