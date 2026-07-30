# Wrapper-teaching — teach the extractor a custom HTTP-client wrapper (design)

- **Date:** 2026-07-30
- **Status:** approved (brainstorming, 2026-07-30). **§4 adversarial design gate: PENDING** (runs next,
  before the implementation plan). Higher-model per-unit review owed during the build.
- **Slice:** the deferred **REQ-C2 first clause** — let an analyst teach the static extractor a custom
  HTTP-client wrapper's call shape so its calls are extracted and attributed as endpoint findings instead of
  being dropped, recovering otherwise-invisible attack surface. The **second clause** (manual set-base-URL +
  re-resolve) shipped earlier; this is the paired fast-follow named in that slice's §9
  (`docs/superpowers/specs/2026-07-29-req-c2-base-url-design.md:296-299`).
- **Primary REQ:** REQ-C2 (first clause). Touches REQ-D3 (finding identity), REQ-A3 (exactly-once outbox),
  REQ-D5 (session-scoped config survives rescans), REQ-S1 (tenant RLS).

## 1. Context

`recon/findings/extract.py` is a tree-sitter AST pass that recognizes a **fixed set** of HTTP-client call
shapes — `fetch`, `XMLHttpRequest.open`, `axios.*`, `axios.create()` instances, jQuery ajax, WebSocket — and
emits one endpoint finding per recognized call (`_handle_call` → `_dispatch_member`, `extract.py:428-465`).
Any other callee falls through and leaves **no trace**: a project's own wrapper —
`const api = makeClient(); api.get('/users')` — produces **zero** findings today (`api` is not `axios`, a
global, `$`, or an `axios.create` var, so no `_dispatch_member` branch fires). Those endpoints are missing
attack surface.

**Key architectural fact (settled in brainstorming).** Recognizing a new call shape happens at **extraction
time** (the analyze stage), where findings and their stable `finding_hash`es are minted and written through
the transactional outbox. This is a *different phase* from the REQ-C2 base-URL overlay, which is a pure
**read-time** transform over already-extracted findings and never re-extracts. So teaching a wrapper cannot be
a read-time overlay on the `session_base_url` model (as that slice's §9 loosely suggested); it must feed the
**extractor** and produce real findings. This makes wrapper-teaching meaningfully larger than the base-URL
work, and is why it is its own gated slice.

## 2. Settled decisions (user-approved in brainstorming)

1. **Output fidelity = first-class endpoint findings.** Recovered calls become real endpoint findings —
   identical shape to `fetch`/`axios` — with stable hashes, so they flow through the whole pipeline
   (probe/export/classify/triage/coverage) with no downstream special-casing.
2. **Mechanism = persisted re-extraction (approach P).** A wrapper-config change triggers an **out-of-band
   re-extract** over the run's stored source blob(s); recovered endpoints are upserted through the existing
   outbox. Chosen over a read-time overlay (approach R) because first-class findings must be real DB rows —
   R would leave DB-level consumers (classify's finding select, coverage counts) blind and spread merge logic
   across ≥3 read sites.
3. **Matcher MVP = name-based member-call wrappers.** The analyst names a wrapper callee (`api`, `apiClient`);
   the extractor recognizes `<name>.<get|post|put|delete|patch|…>(path[, body])`, mirroring the axios-instance
   recognizer. Fuzzier shapes are fast-follows (§10).
4. **Config-removal = documented limitation for MVP.** Removing a rule stops future re-emission but does not
   retract already-persisted wrapper findings (the outbox adds, not removes); the provenance tag makes
   retract-on-removal a clean fast-follow.
5. **Session-scoped persistence**, mirroring `session_spec` / `session_base_url` — a wrapper learned about an
   app is stable across continuous rescans (REQ-D5).

## 3. Architecture & components

```
analyst POST /runs/{id}/wrappers {name:"api"}          (session-scoped config)
        │
        ├──► session_wrapper row (RLS, session-scoped)
        │
        └──► re-extract service (out-of-band, mirrors reclassify_run)
                 │  re-read stored source: run.input_ref | run_asset.input_ref
                 │  extract(source, wrappers=[...])   ← NEW extractor param
                 │  _record_endpoint(...) → store.record_finding (existing outbox, idempotent)
                 ▼
             new endpoint findings (kind attribute wrapper="api", stable finding_hash)
                 │
                 └──► classify · probe · export · triage · coverage  (unchanged; they see normal findings)

future runs: analyze stage reads the session's wrapper config → recognizes the wrapper live (REQ-D5)
```

New / edited units:

- `src/recon/findings/extract.py` — EDIT: `extract(source, wrappers: Sequence[WrapperRule] = ())`; in
  `_dispatch_member`, a configured wrapper callee's HTTP-method member call is dispatched through the existing
  `_axios_member` path (method from the member name, path from arg0, query/body via the existing helpers),
  tagging provenance on the resulting `RawEndpoint`.
- `src/recon/findings/wrappers.py` — NEW (pure): the `WrapperRule` value + the small predicate that decides
  whether a given `(callee, method)` is a taught wrapper call. Stdlib-only, unit-testable in isolation.
- `src/recon/db/models.py` — EDIT: add `SessionWrapper` model; register in the RLS `TENANT_SCOPED_TABLES`
  tuple.
- `src/recon/migrations/versions/<next>_session_wrapper.py` — NEW: create table + RLS policy, mirroring the
  `session_base_url` migration verbatim (`USING` + `WITH CHECK` on `current_setting('app.current_tenant',
  true)::text`, GRANT).
- `src/recon/findings/reextract.py` (or a function in the analyze module) — NEW: the out-of-band re-extract
  service — reuses `_analyze_blob`'s extract→record core over the run's stored blob(s) with the config.
- `src/recon/spec`-style service + `src/recon/api/wrappers_router.py` — NEW: `POST/GET/DELETE
  /runs/{id}/wrappers`; POST persists the rule and triggers the re-extract; router registered before the SPA
  catch-all.
- `web/src/features/...` — NEW: a small React panel (list / add / delete a wrapper name) + Vitest; live
  in-container walkthrough deferred (image rebuild), as in prior slices.

## 4. The matcher (MVP)

A `WrapperRule` names a **callee** (an identifier, e.g. `api`). A member call `api.<m>(arg0, …)` is treated as
an endpoint iff `<m>` is a recognized HTTP method (`get|post|put|delete|patch|head|options`, the set
`_axios_member` already handles). The recovered endpoint reuses the axios extraction exactly:

- method = the member name `<m>`;
- path = `arg0` resolved as a URL string (the existing `_resolve_url` / string-literal handling; a dynamic
  arg0 is left as the same non-resolvable trace axios calls already produce — honesty, REQ-C2);
- query/body params via the existing `_query_params` / `_body_params` / `_config_query_params` helpers.

MVP recognizes a **bare-identifier** callee (`api.get(...)`). **Deferred to §10:** factory-tracking (bind the
callee from `const api = makeClient()` via the same data-flow `axios.create()` uses, `extract.py:228-256`),
callable wrappers (`api('/x', {method})`), request-config shape (`api.request({url, method})`), and dotted
receivers (`this.http.get(...)`).

## 5. Data model

`session_wrapper` (mirrors `session_base_url`, `models.py:446-478`):

```
id             uuid    PK
tenant_id      uuid    NOT NULL   -- fk -> tenant (RLS scoped)
session_id     uuid    NOT NULL   -- fk -> session
callee         text    NOT NULL   -- the wrapper identifier, e.g. 'api'
actor          text    NULL       -- who taught it (audit; best-effort, like base-URL rules)
created_at / updated_at
UNIQUE (session_id, callee)        -- one rule per callee; POST upserts on it
```

Session-scoped (not per-run) so the wrapper survives continuous rescans, exactly as `session_spec` /
`session_base_url` / `finding_spec_status` do. RLS `FORCE` + `USING`/`WITH CHECK` per the standing pattern.

## 6. Re-extract mechanics

On `POST /runs/{id}/wrappers` (add or change a rule), the service runs **out of band** (not through the run
state machine — the run is typically terminal), mirroring `spec/service.py::reclassify_run`. `DELETE` only
removes the rule (future runs / re-extracts stop recognizing it) and does **not** itself re-extract — per §8
it cannot retract already-persisted findings, so a re-extract on delete would be a pure no-op:

1. Resolve the run's source blob(s): `run.input_ref` (single-asset) or each `run_asset.input_ref`
   (multi-asset), `storage.get_blob`.
2. `extract(source, wrappers=rules)` per blob → recovered `RawEndpoint`s (plus the ones already found; the
   extraction is whole-file, so it re-derives the existing endpoints too).
3. `_record_endpoint` → `store.record_finding` for each — the **existing content-addressed, idempotent
   outbox** (REQ-A3): endpoints already present re-emit as no-ops (same `finding_hash`), only genuinely-new
   wrapper endpoints are inserted.

Because extraction is whole-file and the outbox is idempotent, re-extract is safe to run repeatedly and needs
no diffing. `finding_hash` is unchanged in definition (type + host-less value + source path,
`normalize.finding_hash`), so a wrapper endpoint has the same stable identity it would have had if the
extractor had always recognized it.

**Coverage (open item, see §9).** `_analyze_blob` also emits an `analyze.coverage` event; re-extract must
update coverage so recovered endpoints are counted. Single-asset coverage is "latest-id wins"
(`queries._latest_coverage`), so a re-emitted event is correct; multi-asset coverage is a **sum** across
per-asset events, where a naive re-emit would double-count. Resolving this cleanly (re-emit vs. replace) is
flagged for the §4 gate / plan.

## 7. Provenance & identity

Each recovered endpoint carries a provenance attribute `wrapper: "<callee>"` on the finding (via
`_record_endpoint`'s `attributes`). This makes them auditable, distinguishable in the findings UI, and
prunable by a future retract pass. Identity is otherwise unchanged: same `finding_hash`, so triage
(`finding_triage`), shadow classification (`finding_spec_status`), and the REQ-C2 base-URL overlay all apply
uniformly with zero special-casing — a wrapper endpoint is just an endpoint.

## 8. Config-removal semantics (MVP limitation)

The outbox adds findings and cannot cleanly retract them. For MVP, `DELETE /runs/{id}/wrappers/{id}` removes
the rule (so future runs / re-extracts no longer recognize it) but does **not** delete wrapper endpoints
already persisted by a prior re-extract. This is a documented limitation, acceptable because a stale
wrapper finding is over-reporting (safe direction for a recon tool), and the `wrapper` provenance tag makes a
**retract-on-removal** pass (delete provenance-tagged findings + their occurrences for the removed callee) a
clean fast-follow (§10).

## 9. Open items / risks (for the §4 gate)

- **Coverage on re-extract** (§6): single-asset is fine (latest-wins); multi-asset sum needs a
  non-double-counting re-emit/replace decision.
- **Whole-file re-extract cost:** re-running extraction over every blob on each wrapper POST is O(source);
  bounded by the same input caps analyze already enforces. Acceptable; noted.
- **Provenance on a call also matched natively:** if a taught callee collides with a real recognizer (e.g.
  the analyst names `axios`), the native path wins and the rule is a no-op; the matcher must not double-emit.
- **Dynamic path arg:** `api.get(someVar)` yields the same non-resolvable trace as a dynamic `axios.get` —
  honesty preserved, no invented path.
- **Out-of-band write on a terminal run:** re-extract writes findings outside the state machine, exactly as
  `reclassify_run` writes verdicts out-of-band; must run in its own `tenant_session` and not transition state.

## 10. Out of scope / fast-follows

- **Retract-on-removal** of persisted wrapper findings (§8).
- **Fuzzier matcher shapes** (§4): factory-tracking, callable wrappers, request-config shape, dotted receivers.
- **Automatic cross-file base inference by unknown symbol** — the *other* §9 deferral from the base-URL slice;
  unrelated to this one, stays deferred.
- **Wrapper-specific header/auth extraction** — the paired static-header thread; unchanged here.

## 11. REQ traceability

| REQ | How this slice touches it |
|---|---|
| REQ-C2 (first clause) | Teach a custom wrapper's call shape so its calls are extracted + attributed — the deferred SHOULD, built here. |
| REQ-D3 | Recovered endpoints get the standard `finding_hash` identity; no identity change. |
| REQ-A3 | Re-extract writes through the existing exactly-once transactional outbox; idempotent re-emit. |
| REQ-D5 | Session-scoped `session_wrapper` config → future rescans recognize the wrapper automatically. |
| REQ-S1 | Tenant-scoped read/write via `tenant_session` RLS (`USING` + `WITH CHECK`); unknown/other-tenant run → 404. |
| REQ-P1/P2 | No active traffic, no new egress — static re-extraction over already-stored source only. |

## 12. §4 adversarial design gate

PENDING — to be run next (proof-bound, exact `file:line`), before the implementation plan. Findings will be
folded here.
