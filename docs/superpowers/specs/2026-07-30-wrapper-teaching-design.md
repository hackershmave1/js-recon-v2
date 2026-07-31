# Wrapper-teaching — teach the extractor a custom HTTP-client wrapper (design)

- **Date:** 2026-07-30
- **Status:** approved (brainstorming, 2026-07-30). **§4 adversarial design gate PASSED (2026-07-30):
  BUILD WITH CHANGES** — nine proof-bound findings folded below (§12); the core mechanism held under attack.
  Higher-model per-unit review owed during the build.
- **Slice:** the deferred **REQ-C2 first clause** — let an analyst teach the static extractor a custom
  HTTP-client wrapper's call shape so its calls are extracted and attributed as endpoint findings instead of
  being dropped, recovering otherwise-invisible attack surface. The **second clause** (manual set-base-URL +
  re-resolve) shipped earlier; this is the paired fast-follow named in that slice's §9
  (`docs/superpowers/specs/2026-07-29-req-c2-base-url-design.md:296-299`).
- **Primary REQ:** REQ-C2 (first clause). Touches REQ-D3 (finding identity), REQ-A3 (exactly-once outbox),
  REQ-D5 (session-scoped config survives rescans), REQ-S1 (tenant RLS), REQ-A1 (heavy work off the hot path).

## 1. Context

`recon/findings/extract.py` is a tree-sitter AST pass that recognizes a **fixed set** of HTTP-client call
shapes — `fetch`, `XMLHttpRequest.open`, `axios.*`, `axios.create()` instances, jQuery ajax, WebSocket — and
emits one endpoint finding per recognized call (`_handle_call` → `_dispatch_member`, `extract.py:428-465`).
Any other callee falls through and leaves **no trace**: a project's own wrapper —
`const api = makeClient(); api.get('/users')` — produces **zero** findings today (`api` is not `axios`, a
global, `$`, or an `axios.create` var). Those endpoints are missing attack surface.

**Key architectural fact (settled in brainstorming).** Recognizing a new call shape happens at **extraction
time** (the analyze stage), where findings and their stable `finding_hash`es are minted and written through
the transactional outbox. This is a *different phase* from the REQ-C2 base-URL overlay, which is a pure
**read-time** transform over already-extracted findings and never re-extracts. So teaching a wrapper cannot be
a read-time overlay on the `session_base_url` model (as that slice's §9 loosely suggested); it must feed the
**extractor** and produce real findings. This makes wrapper-teaching meaningfully larger than the base-URL
work, and is why it is its own gated slice.

## 2. Settled decisions (user-approved in brainstorming; refined by the §4 gate)

1. **Output fidelity = first-class endpoint findings.** Recovered calls become real endpoint findings —
   identical shape to `axios` calls — with stable hashes, so they flow through the whole pipeline
   (probe/export/classify/triage) with no downstream special-casing.
2. **Mechanism = persisted re-extraction (approach P).** A wrapper-config change triggers an **out-of-band
   re-extract** over the POSTed run's stored source blob(s); recovered endpoints are upserted through the
   existing outbox. Chosen over a read-time overlay (approach R) because first-class findings must be real DB
   rows — R would leave DB-level consumers (classify's finding select, coverage counts) blind and spread merge
   logic across ≥3 read sites.
3. **Matcher MVP = name-based member-call wrappers.** The analyst names a wrapper callee (`api`, `apiClient`);
   the extractor recognizes `<name>.<get|post|put|delete|patch|head|options>(path[, body])`, dispatched
   through the existing axios-member path. `<name>.request({url, method})` is recognized for free (it falls
   out of the axios reuse — §4). Other fuzzy shapes are fast-follows (§10).
4. **Config-removal = documented limitation for MVP.** Removing a rule stops future re-emission but does not
   retract already-persisted wrapper findings (the outbox adds, not removes); the `wrapper` provenance tag
   makes retract-on-removal a clean fast-follow.
5. **Session-scoped config, run-scoped re-extract.** The `session_wrapper` config is session-scoped (mirrors
   `session_spec`/`session_base_url`, so future rescans recognize the wrapper — REQ-D5). But a POST re-extracts
   **only the POSTed run** (§6): re-reading every terminal run's blobs would multiply the O(source) cost. So
   teaching a wrapper recovers surface on **that run + future runs**, not retroactively across all sibling
   runs. (This is the one place it diverges from the base-URL slice, whose `reclassify_run` is session-wide
   because it re-reads no source — only DB findings.)
6. **Coverage is not re-emitted on re-extract (MVP).** Re-extract records endpoint findings only; it does NOT
   emit an `analyze.coverage` event, so the run's coverage counters keep their pre-wrapper values (an honest
   under-report of the newly-recovered endpoints) and multi-asset coverage stays correct (§12 Blocker 1).
   Refreshing coverage to include wrapper endpoints is a fast-follow (§10).
7. **Re-extract runs in the API request (threadpool), endpoints-only.** After the §12 fold it is a pure,
   in-process tree-sitter parse (no Kingfisher subprocess) bounded by analyze's existing input caps, so it can
   run synchronously in the POST like `base_url_service.add_rule` → `reclassify_run` does. Enqueue-to-worker
   is the REQ-A1-strict alternative, deferred (§10) unless re-extract latency proves too high.

## 3. Architecture & components

```
analyst POST /runs/{id}/wrappers {callee:"api"}         (session-scoped config)
        │
        ├──► session_wrapper row (RLS, session-scoped; UNIQUE(session_id, callee))
        │
        └──► wrapper_service.add_rule → re-extract (in-request, threadpool; run-scoped)
                 │  re-read stored source: run.input_ref | run_asset.input_ref  (storage.get_blob)
                 │  _extract_endpoints(source, source_map_ref, wrappers=[...])   ← factored, endpoints-only
                 │      = _analysis_units(source_map_ref, source) → extract(unit, wrappers) → _record_endpoint
                 │        (NO kingfisher.scan, NO analyze.coverage emission)
                 ▼
             new endpoint findings (attributes.wrapper="api", kind="axios", stable finding_hash)
                 │  via store.record_finding — content-addressed, idempotent outbox (REQ-A3)
                 └──► classify · probe · export · triage · base-URL overlay  (unchanged; normal findings)

future runs: analyze stage passes the session's wrapper config to extract() → recognizes the wrapper live
```

New / edited units:

- `src/recon/findings/extract.py` — EDIT: `extract(source, wrappers: Sequence[WrapperRule] = ())`; add an
  optional frozen `wrapper: str | None = None` field to `RawEndpoint` (`extract.py:50-59`); in
  `_dispatch_member` add a **final** branch (after `axios`/jQuery/xhr-`open`/`env.instances`, `extract.py:454-465`)
  — `elif obj in wrapper_callees:` → `_axios_member(..., base="")`, threading the callee name so `_endpoint`
  sets `RawEndpoint.wrapper`. `kind` stays `"axios"` (§7).
- `src/recon/findings/wrappers.py` — NEW (pure, stdlib-only): the `WrapperRule` value + the small predicate
  that decides whether `(callee)` is a taught wrapper. Unit-testable in isolation.
- `src/recon/findings/analyze.py` — EDIT: factor an **endpoints-only** helper `_extract_endpoints(session,
  ..., source, source_map_ref, wrappers)` out of `_analyze_blob` — the `_analysis_units` → `extract(...)` →
  `_record_endpoint` loop only (`analyze.py:264, 272-284`), WITHOUT `kingfisher.scan` (`analyze.py:257`) or the
  `analyze.coverage` `record_event` (`analyze.py:301`). `_analyze_blob` keeps wrapping it with secrets +
  coverage; re-extract calls the helper directly. It must retain `_analysis_units(source_map_ref, source)` so
  re-emitted native endpoints keep their source-map-recovered `path` and thus their `finding_hash` (§12 Imp 4).
- `src/recon/findings/reextract.py` — NEW: the out-of-band re-extract service. `session.get(Run)`→None ⇒ 404;
  resolve `run.input_ref` (single, pass `run.source_map_ref`) or each `run_asset.input_ref` (multi, pass
  `source_map_ref=None`), `storage.get_blob`, call `_extract_endpoints` with the session's wrapper rules, in
  its own `tenant_session`, never transitioning run state (mirrors `spec/service.py::reclassify_run`). A
  missing-blob `ClientError` (`storage.py:70-73`) maps to a clean 404/409, not a raw 500 (§12 Minor 9).
- `src/recon/db/models.py` — EDIT: add `SessionWrapper` model + a new `WRAPPER_TABLES = ("session_wrapper",)`
  tuple (NOT the frozen slice-1 `TENANT_SCOPED_TABLES`, `models.py:482-491`; mirror `BASE_URL_TABLES`
  `models.py:507`).
- `src/recon/migrations/versions/0008_session_wrapper.py` — NEW: create table + RLS, iterating
  `models.WRAPPER_TABLES` exactly as `0007_session_base_url.py:32-41` does (`ENABLE`/`FORCE` RLS +
  `tenant_isolation USING (...) WITH CHECK (...)` on `current_setting('app.current_tenant', true)::text` +
  `GRANT`).
- `src/recon/api/wrappers_router.py` — NEW: `POST/GET/DELETE /runs/{id}/wrappers` (mirrors
  `base_url_router.py`); POST persists the rule and runs re-extract via `run_in_threadpool`; registered in
  `app.py` before `_mount_spa` (SPA catch-all is registered last, `app.py:52,73`).
- `web/src/features/...` — NEW: a small React panel (list / add / delete a wrapper callee) + Vitest; live
  in-container walkthrough deferred (image rebuild), as in prior slices.

## 4. The matcher (MVP)

A `WrapperRule` names a **callee** (a bare identifier, e.g. `api`). A member call `api.<m>(arg0, …)` is treated
as an endpoint iff `<m>` is a recognized HTTP method — dispatched through the existing `_axios_member`
(`extract.py:513-534`), which already derives method from the member name and path from `arg0` via
`_resolve_url`/`_query_params`/`_body_params`, with `base=""` (safe — `_join_base` returns the path unchanged,
`extract.py:352-353`). The recovered endpoint reuses the axios extraction, **plus** a `wrapper` provenance tag
threaded through it (§7); it does not change any axios semantics.

`api.request({url, method})` is recognized in MVP for free — `_axios_member` routes `prop == "request"` to
`_axios_from_config` (`extract.py:515-517`) — so the config shape is in scope even though it wasn't the headline
case (§12 Minor 6). **Deferred to §10:** factory-tracking (bind the callee from `const api = makeClient()` via
the same data-flow `axios.create()` uses, `extract.py:228-256`), callable wrappers (`api('/x', {method})`), and
dotted receivers (`this.http.get(...)`).

**Dispatch ordering is load-bearing (§12 Minor 7):** the wrapper branch MUST be appended **after** the
existing `_dispatch_member` branches, so a callee that collides with a native target (`axios`, `$`, an
`axios.create` instance var, or `.open`) resolves via the native/instance path (strictly better — an instance
keeps its real `base`), and no double-emit occurs (dispatch fires exactly one branch per call).

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
`session_base_url` / `finding_spec_status` do. RLS `FORCE` + `USING`/`WITH CHECK` via the new `WRAPPER_TABLES`
tuple + migration `0008` (§3), per the standing pattern.

## 6. Re-extract mechanics

On `POST /runs/{id}/wrappers` (add or change a rule), the service runs **in the API request via
`run_in_threadpool`, out of band** of the run state machine (the run is typically terminal), mirroring
`base_url_service.add_rule` → `spec/service.py::reclassify_run`. `DELETE` only removes the rule (future runs /
re-extracts stop recognizing it) and does **not** itself re-extract — per §8 it cannot retract
already-persisted findings, so a re-extract on delete would be a pure no-op:

1. Resolve the run's source blob(s): `run.input_ref` (single-asset, with `run.source_map_ref`) or each
   `run_asset.input_ref` (multi-asset, `source_map_ref=None`), `storage.get_blob`
   (`analyze.py:94-114,200`).
2. `_extract_endpoints(source, source_map_ref, wrappers=rules)` per blob (the factored endpoints-only helper,
   §3) → recovered `RawEndpoint`s (plus the natives — extraction is whole-file, so it re-derives the existing
   endpoints too, at their same source-map-recovered paths).
3. `_record_endpoint` → `store.record_finding` for each — the **existing content-addressed, idempotent
   outbox** (REQ-A3, `store.py:86,101,134`): endpoints already present re-emit as no-ops (same `finding_hash`
   + `occurrence_hash`), only genuinely-new wrapper endpoints are inserted.

Because extraction is whole-file and the outbox is idempotent, re-extract is safe to run repeatedly and needs
no diffing. It records **endpoints only** — no `kingfisher.scan` (secrets are unchanged; source is the same)
and no `analyze.coverage` emission (§2.6 / §12 Blocker 1). `finding_hash` is unchanged in definition (type +
host-less value + source path, `normalize.finding_hash`, `normalize.py:302-315`), and the helper preserves the
source-map path resolution (§12 Imp 4), so a wrapper endpoint has the same stable identity it would have had
if the extractor had always recognized it.

## 7. Provenance & identity

`RawEndpoint` gains an optional frozen field `wrapper: str | None = None` (`extract.py:50-59`), set to the
callee when the endpoint came from a taught-wrapper call and threaded through `_axios_member`/`_endpoint`
(`extract.py:310-323,513-534`); `_record_endpoint` surfaces it as `attributes["wrapper"]` (`analyze.py:409`).
`kind` **stays `"axios"`** — NOT `"wrapper"` — because the only downstream `kind` switch is
`reconstruct.py:176` (`content_type="application/json"` iff `kinds <= _JSON_BODY_KINDS = {"fetch","axios"}`,
`reconstruct.py:25`); a `"wrapper"` kind would silently drop the POST-body Content-Type from curl/raw-HTTP/
export (§12 Imp 3). Provenance therefore lives in a **separate attribute**, not in `kind`.

Identity is otherwise unchanged: same `finding_hash` (kind-free — hashes only `{type, value, path}`,
`normalize.py:302-315`; `value` from `normalize_endpoint` depends only on method+url, `normalize.py:224-229`),
so triage (`finding_triage`), shadow classification (`finding_spec_status`), and the REQ-C2 base-URL overlay
(`resolve_operation`, which keys off value/operation/hash never `kind`, `base_url.py:91-113`) all apply
uniformly — a host-less wrapper endpoint is even a *correct* candidate for the base-URL overlay (synergistic).

**Best-effort under collision (§12 Minor 7):** if the same operation is seen both natively and via a wrapper
in one run, the finding dedups on `finding_hash` and `store.record_finding`'s `on_conflict_do_nothing`
(`store.py:101`) keeps the **first** writer's attributes — so the `wrapper` tag is best-effort when a native
call already produced the identical endpoint. Acceptable (the endpoint is captured either way).

## 8. Config-removal semantics (MVP limitation)

The outbox adds findings and cannot cleanly retract them. For MVP, `DELETE /runs/{id}/wrappers/{id}` removes
the rule (so future runs / re-extracts no longer recognize it) but does **not** delete wrapper endpoints
already persisted by a prior re-extract. This is a documented limitation, acceptable because a stale wrapper
finding is over-reporting (safe direction for a recon tool), and the `wrapper` provenance tag makes a
**retract-on-removal** pass (delete provenance-tagged findings + their occurrences for the removed callee) a
clean fast-follow (§10).

## 9. Open items / risks

All §4-gate blockers are resolved by the folds above; residuals, none blocking:

- **Whole-file re-extract cost / tier (§2.7):** in-process tree-sitter parse over each blob on each wrapper
  POST is O(source), bounded by analyze's input caps, run in a threadpool. If latency proves too high on large
  bundles, enqueue-to-worker (§10). The analyst POST is not a latency-sensitive hot path.
- **Coverage under-report (§2.6):** recovered endpoints are not reflected in the run's coverage counters until
  a coverage-refresh fast-follow lands; findings themselves are complete and correct.
- **Run-scoped recovery (§2.5):** other terminal runs in the session are not retroactively re-extracted.

## 10. Out of scope / fast-follows

- **Coverage refresh** so recovered endpoints are counted (needs a multi-asset-safe re-emit or a distinct
  event type `_latest_coverage` won't double-count).
- **Retract-on-removal** of persisted wrapper findings (§8).
- **Session-wide re-extract** (loop every terminal run in the session on a wrapper POST), if retroactive
  recovery across sibling runs is wanted.
- **Fuzzier matcher shapes:** factory-tracking, callable wrappers, dotted receivers (§4).
- **Automatic cross-file base inference by unknown symbol** — the *other* §9 deferral from the base-URL slice;
  unrelated, stays deferred.
- **409 partial-success on `add_rule`** (final-review Minor): the rule is committed in transaction 1, then
  re-extract runs in its own transaction, so a vanished source blob returns 409 (`SourceBlobMissing`) even
  though the rule persisted (it still applies to future runs and appears on GET). Safe-direction and rare;
  a fully-consistent fix shares one transaction between persist + re-extract, or the FE reflects the saved
  rule on a 409. Mirrors `base_url_service`'s two-transaction shape.
- **`reextract_run` per-blob failure containment** (final-review Minor): re-extract catches only
  `ClientError` (→ `SourceBlobMissing`); a non-infra exception mid-loop aborts the whole re-extract (unlike
  `_analyze_assets`' per-asset containment). Acceptable for the synchronous MVP (idempotent outbox makes a
  manual retry safe); the enqueue-to-worker path (§2.7) or per-blob try/except would harden it.
- **Malformed `run_id` path param → 500 not 404** (final-review Minor, repo-wide chore, NOT introduced here):
  `wrapper_service` resolves the run via `session.get(models.Run, run_id)` over an unvalidated path string,
  so a non-UUID id raises a coercion 500 instead of a clean 404 — identical to the shipped
  `base_url_service`/`spec` services. A shared UUID-path-param guard is a cross-cutting fix across all three.

## 11. REQ traceability

| REQ | How this slice touches it |
|---|---|
| REQ-C2 (first clause) | Teach a custom wrapper's call shape so its calls are extracted + attributed — the deferred SHOULD, built here. |
| REQ-D3 | Recovered endpoints get the standard `finding_hash` identity (source-map path preserved); no identity change. |
| REQ-A3 | Re-extract writes through the existing exactly-once transactional outbox; idempotent re-emit. |
| REQ-D5 | Session-scoped `session_wrapper` config → future rescans recognize the wrapper automatically. |
| REQ-S1 | Tenant-scoped read/write via `tenant_session` RLS (`USING` + `WITH CHECK`); unknown/other-tenant run → 404. |
| REQ-A1 | Re-extract is endpoints-only (no subprocess) in a threadpool; heavy/worker path is the deferred alternative. |
| REQ-P1/P2 | No active traffic, no new egress — static re-extraction over already-stored source only. |

## 12. §4 adversarial design gate (2026-07-30)

Proof-bound Opus reviewer, every objection cited to exact `file:line`. **Verdict: BUILD WITH CHANGES.** The
core mechanism held under attack (see "held" below); one spec claim was wrong and is fixed; nine findings
folded.

**The wrong claim (fixed):** "re-extract reuses `_analyze_blob`'s extract→record core" — `_analyze_blob` is
not such a core; it bundles source-map unit resolution + `kingfisher.scan` (`analyze.py:257`) +
`analyze.coverage` emission (`analyze.py:301`) + endpoint recording. Folded into an **endpoints-only factored
helper** (§3, §6).

| # | Sev | Finding (proof) | Fold |
|---|---|---|---|
| 1 | Blocker | Reusing `_analyze_blob` re-emits `analyze.coverage`; multi-asset coverage SUMS every event (`queries.py:268,304-309`), correct only because each asset emits once — a wrapper re-extract would DOUBLE attributed/unattributed/secrets + duplicate per-file rows. | Endpoints-only helper (no coverage emission); don't re-emit coverage for MVP (§2.6, §6, §10). |
| 2 | Blocker/Imp | `_analyze_blob` runs the Kingfisher **subprocess** (`analyze.py:257`→`kingfisher.py:202-208`); reusing it drags a subprocess + binary dependency into the (API) re-extract tier — soft REQ-A1 issue (`app.py:1-5`). | Endpoints-only helper removes the subprocess; run in API threadpool for MVP, worker-enqueue deferred (§2.7, §10). |
| 3 | Imp | `RawEndpoint` has no attributes field (`extract.py:49-59`); `_record_endpoint` hard-codes attributes (`analyze.py:409`); and `kind` MUST stay `"axios"` — `reconstruct.py:176`/`:25` gate POST Content-Type on `kind ∈ {fetch,axios}`, so `kind="wrapper"` would drop it. | New optional `RawEndpoint.wrapper` field threaded through the axios path; `kind="axios"`; provenance a separate attribute (§7). |
| 4 | Imp | `finding_hash` includes source `path` (`normalize.py:302-315`); `_analyze_blob` recovers per-source paths from a source map via `_analysis_units` (`analyze.py:264,272-274`). A helper that skips this churns every native endpoint's hash. | Factored helper retains `_analysis_units(source_map_ref, source)` and threads `run.source_map_ref` (single) / `None` (per asset) exactly as `analyze_run` (§3, §6). |
| 5 | Imp | Config is session-scoped but re-extract can only re-read the POSTed run's blobs — unlike base-URL's session-wide `reclassify_run` (`spec/service.py:183-191`). | Document run-scoped re-extract (§2.5); session-wide loop is a fast-follow (§10). |
| 6 | Minor | `api.request({...})` is recognized for free via `_axios_member`→`_axios_from_config` (`extract.py:515-517`), though listed as deferred. | Note it's in MVP scope (§2.3, §4). |
| 7 | Minor | Collision safety depends on the wrapper branch being appended LAST in `_dispatch_member` (`extract.py:454-465`); and `on_conflict_do_nothing` keeps the first writer's attributes (`store.py:101`) so the tag is best-effort under native/wrapper collision. | Pin ordering + collision test (§4); note best-effort tag (§7). |
| 8 | Minor | `TENANT_SCOPED_TABLES` is the frozen slice-1 set (`models.py:482-491`); later tables use their own tuple (`BASE_URL_TABLES`, `models.py:507`, consumed by `0007:32`). | New `WRAPPER_TABLES` tuple + migration `0008` mirroring 0007 (§3, §5). |
| 9 | Minor | `storage.get_blob` raises `ClientError` on a missing blob (`storage.py:70-73`); a synchronous re-extract has no worker retry lane → raw 500. | Map missing-blob to a clean 404/409 in the service (§3). |

**Load-bearing claims that HELD (code-verified):** out-of-band feasibility (`reclassify_run` precedent —
own `tenant_session`, RLS-invisible→404, reads stored blob, never transitions state; `input_ref`/`RunAsset.
input_ref` persist post-run, `models.py:147,336`); idempotent re-emit (`store.record_finding` content-addressed
+ dual `on_conflict_do_nothing`, `store.py:86,101,134`); matcher reuse via a final `_dispatch_member` branch
→ `_axios_member(base="")`; identity uniformity (`finding_hash`/`operation_of_endpoint_value` are `kind`-free;
classify/reconstruct/base-URL all key off value/operation/hash); model+RLS+route template (`SessionBaseUrl` +
0007 + SPA-last); no active traffic (static only).

## 13. Build sequence (gate-recommended)

1. `extract.py` param + `wrappers.py` predicate + `RawEndpoint.wrapper` provenance threading — pure,
   unit-testable, no DB.
2. Factored endpoints-only `_extract_endpoints` helper (source-map fidelity) + `reextract.py` service.
3. `SessionWrapper` model + `WRAPPER_TABLES` + migration `0008` + `wrapper_service` + `wrappers_router`.
4. React panel + Vitest.

Each step TDD, each through the §4 gate-2 higher-model review before merge.
