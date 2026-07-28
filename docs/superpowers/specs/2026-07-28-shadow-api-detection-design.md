# Shadow-API detection — base-URL resolution + spec diff (design)

- **Date:** 2026-07-28
- **Status:** approved (brainstorming); pending written-spec review, then the §4
  adversarial design gate, then the implementation plan.
- **Slice:** a new slice that makes the platform answer *"which endpoints does the
  target's JavaScript call that its published API spec does not document?"* It has
  two phases in one slice: **(1)** conservative **base-URL resolution** inside the
  existing extractor (so recovered client paths are *full*, not partial), and **(2)**
  a **spec diff** that ingests a user-supplied OpenAPI/Swagger spec and classifies
  each extracted endpoint as **documented / shadow / unresolved**.
- **Primary REQ:** REQ-C2 (cross-file base-URL resolution + the honesty/coverage
  ethos) is advanced directly. Touches REQ-D3 (base-URL resolution changes the
  templated path inside `finding_hash` — see §3.4), REQ-S1 (RLS on the two new
  tables), REQ-D2 (spec stored as a blob by key), REQ-S3 (a `spec.classified` audit
  event). **Shadow detection itself is not one of the 40 REQ-* IDs** — it is recorded
  here as an explicit, approved **spec extension** built on the OpenAPI substrate.
- **Source concern:** `docs/shadow-api-false-positives.md` (the portable write-up of
  the false-positive problem and its six preventions). This design implements
  preventions #1–#4 and leverages the AST the extractor already uses (#5); #6
  (runtime evidence) stays out of scope (§10).

## 1. Context

The extractor (`src/recon/findings/extract.py`, "Vespasian") is **AST-based** —
tree-sitter (`extract.py:26`) — and already resolves per-call literal URLs for
`fetch`, XHR, `axios.*`, jQuery, and WebSocket. Two gaps make shadow detection
impossible-to-do-honestly today:

1. **No base-URL resolution.** The extractor is a stateless per-call walk with no
   data-flow (`extract.py:14-18`). `axios.create({ baseURL })` and every call on the
   returned instance are **silently dropped** — `_axios_member` returns on any member
   that is not an HTTP verb or `request` (`extract.py:273`), and `_dispatch_member`
   only recognizes `axios`, jQuery, and the global objects (`extract.py:217-226`), so
   a call on an `axios.create()` instance identifier matches nothing. These endpoints
   are invisible today (a false **negative**); once recovered *without* their base,
   they become the false **positive** the write-up describes.
2. **No documented side, no diff.** Nothing under `src/` ingests or diffs against an
   OpenAPI/Swagger spec. (OpenAPI *export* — extraction → a spec file — is a separate,
   deferred deliverable and is **not** the input to this diff.)

What already exists and is reused:

- **Canonicalization** (`normalize.py`). `endpoint_operation(method, url)` produces
  `METHOD /templated-path` (`normalize.py:218-221`); `_templatize_path` /
  `template_segment` collapse numeric → `{id}`, UUID → `{uuid}`, hex/high-entropy →
  `{hash}` conservatively (`normalize.py:182-203`); the query is reduced to sorted,
  de-duped names. Crucially, `Endpoint.host` is kept **off** the finding hash "so
  REQ-C2 base-URL re-resolution cannot churn identity" (`normalize.py:165-168`) — the
  data model already left a seam for this work.
- **Per-run classification pattern** (`finding_triage`). A user verdict is stored keyed
  `(session_id, finding_hash)` — session-scoped, deliberately not run-scoped, "so it
  survives re-runs" (`models.py:340-370`) — upserted via
  `pg_insert(...).on_conflict_do_update` with a durable `triage.updated` event
  (`probe/triage.py:30-93`), surfaced nested per finding in the findings read
  (`findings_router.py:42-51`). Shadow classification mirrors this exactly.
- **Blob storage** by content-addressed, tenant-scoped key (`storage.py:24-32`); a new
  artifact kind is a one-line addition to `BLOB_KINDS`.
- `openapi-spec-validator` is already a **planned Vespasian dependency**
  (`Developer Requirements.dc.html:481`) — reused to validate the ingested spec.

### 1.1 The false-positive mechanism (why this is the whole point)

Shadow = `called − documented`. The diff is only as correct as the *called* set.
Static extraction recovers a **fragment** when it misses a base URL: the client
config sets `axios.create({ baseURL: '/location' })` once, then calls
`.post('/address/search')`; a per-call scan sees `/address/search`, the spec
documents `/location/address/search`, and the diff mislabels a fully-documented call
as shadow. A single `create` can taint dozens of endpoints. The fix, in leverage
order, is base-URL resolution + a three-bucket classifier that **never labels a
partial path as shadow**.

## 2. Settled decisions

Decided during brainstorming (2026-07-27/28); binding for this slice unless re-opened.

1. **Its own slice**, full superpowers flow (brainstorm → spec → §4 gates → plan →
   build).
2. **Base-URL resolution is sequenced first**, and is **auto-conservative**: resolve
   only the safe, in-file, literal cases automatically; anything ambiguous stays
   `unresolved` and is never guessed. The spec's literal REQ-C2 mechanism (a *manual*
   set-base-URL + wrapper-teaching by call-shape) is deferred to a fast-follow (§10).
3. **The documented set is a user-supplied OpenAPI/Swagger spec** we ingest — not a
   spec we generate. **Attach-after** intake: the analyst uploads/pastes a spec via a
   dedicated action after endpoint findings exist; classification runs on demand and
   re-tags when a newer spec is supplied. No spec-by-URL fetch (SSRF surface).
4. **Output = per-run classification.** Each endpoint finding gets a `spec_status`
   (documented / shadow / unresolved) computed against the supplied spec, **stored
   session-scoped** (keyed `(session_id, finding_hash)`, mirroring `finding_triage`,
   so it survives re-runs) and **surfaced per-run** by filtering a run's findings.
   Chosen over a separate spec-diff artifact (rebuilds machinery) and a first-class
   "shadow" finding type (a shadow is a relationship, not a fact about the JS;
   documented/unresolved are not findings).
5. **OpenAPI/Swagger export stays out of scope** — a separate deliverable.
6. **Formats:** OpenAPI 3.0 / 3.1 **and** Swagger 2.0 accepted (validated via
   `openapi-spec-validator`; `servers`/`basePath` normalized).
7. **Verb mismatch** (path documented, method not) is classified **shadow** with a
   `reason` of `undocumented-method`, distinct from `undocumented-path`.
8. **The three buckets are never binary.** Only a *complete, fully-resolved,
   statically-certain* path may be shadow; anything partial/interpolated/base-less, or
   a suffix of a documented path, goes to `unresolved`.

## 3. Phase 1 — base-URL resolution (`extract.py` + `normalize.py`)

### 3.1 A binding pre-pass

Before the sink walk, a single pass collects an in-file **base environment** of only
*statically-certain string-literal* bases:

- `const|let|var NAME = axios.create({ baseURL: "<literal>" })` → `NAME` is an axios
  instance with that base.
- `axios.defaults.baseURL = "<literal>"` → a default base for bare `axios(...)` /
  `axios.<verb>(...)` calls.
- `const NAME = "<literal>"` **used** as the leading `${NAME}` of a template-literal
  URL → a constant-folded path prefix.

### 3.2 Join at the sink

- A member call on a known instance identifier — `NAME.get/post/put/...(url, ...)` —
  is dispatched through the existing axios handling with `url = join(base, url)`. This
  requires `_dispatch_member` (`extract.py:217`) to recognize instance identifiers in
  the base environment, in addition to `axios`/jQuery/globals.
- A bare `axios(...)` / `axios.<verb>(...)` with a relative URL, when
  `axios.defaults.baseURL` is known, joins the default base.
- A template URL beginning `` `${NAME}/rest` `` where `NAME` is a const literal folds
  to `join(NAME_value, "/rest")`.
- `join(base, path)`: an absolute `path` (has its own scheme/host) wins and `base` is
  ignored; otherwise the base's path-prefix is prepended (`"/location"` + 
  `"/address/search"` → `"/location/address/search"`, slashes de-duped). An absolute
  `base` (`https://api.host/v3`) contributes host + prefix; `normalize` then splits the
  host onto the occurrence as it does today.

### 3.3 Conservatism (never guess a base)

- **Literal bases only.** A `baseURL` that is a variable/expression → the instance is
  recognized but its base is **unknown**; its calls are *attributed* (counted, path
  kept relative) and land in `unresolved` — never guessed. This alone fixes the
  current silent-drop false negative.
- **Single binding only.** If `NAME` is reassigned (more than one declarator/
  assignment), it is ambiguous → not resolved.
- **In-file only.** No cross-file/import following (that is the deferred manual/
  wrapper-teaching thread).
- A wrong base is worse than no base — it converts a real shadow into a false
  "documented" (a missed finding). This bias matches `normalize.py`'s existing rule
  that "an over-merge silently loses attack surface, so ambiguous stays literal".

### 3.4 Identity churn (call it out)

Folding a base into the path changes the templated path inside `finding_hash` for
affected endpoints (host stays off the hash; the base **path** is legitimately part of
the operation identity). Consequences, all accepted:

- The first run after this ships shows a one-time REQ-D5 diff churn (old hash
  "removed", new hash "added") for rebased endpoints.
- Session-scoped `finding_triage` / `finding_spec_status` keyed on the old hash no
  longer match the new hash for those endpoints (they re-key). This is inherent to
  making identity *more correct* and needs no migration.

## 4. Phase 2a — spec ingest (`recon.spec.ingest`)

- Accept a spec as an uploaded file or pasted text (`POST /runs/{run_id}/spec`, §6.3).
  Detect + validate OpenAPI 3.0/3.1 and Swagger 2.0 via `openapi-spec-validator`;
  an invalid/unparseable spec → `422` with the validator's errors.
- Compute the **documented operation set**: for every `(path, method)` in the spec,
  prepend the base path from `servers[0]` (3.x) / `basePath` (2.0) and reduce to the
  same canonical compare-key used on the called side (§5.1). Store the raw spec as a
  `"spec"` blob and a `session_spec` row (§6.1) with the format and normalized server
  base(s).

## 5. Phase 2b — classify (`recon.spec.classify`)

### 5.1 One compare-key, both sides

Reduce every operation — client finding and spec op alike — to
`METHOD` + a path in which **every path-parameter position is wildcarded to `*`**:
a spec `{petId}`, a `normalize` value-template (`{id}`/`{uuid}`/`{hash}`), and a bare
numeric/uuid segment all collapse to `*`. Comparison is over canonical forms, never
raw strings (write-up prevention #1).

### 5.2 Statically-certain vs partial

A finding path is **partial** if any segment still contains an interpolation hole
(`${...}` survived from a template literal) or a required base was unknown. A
value-template segment (`{id}` from an observed `123`) is *certain* and does **not**
make the path partial. Only a non-partial path is eligible to be shadow.

### 5.3 Decision order (first match wins)

For each endpoint finding in the session:

1. **partial** (interpolation hole, or base-unresolved-relative) → `unresolved`
   (`reason = partial | base-unresolved`).
2. compare-key matches a documented op (same method + wildcard path) → `documented`
   (`matched_operation` recorded).
3. wildcard path matches a documented path under a **different** method → `shadow`
   (`reason = undocumented-method`).
4. finding path is a **suffix** of some documented path (or vice-versa) after
   wildcarding → `unresolved` (`reason = suffix-verify`; "likely documented, base
   probably missing" — the safety net that kills residual base-URL FPs, write-up #3).
5. path is complete + statically-certain with no match → `shadow`
   (`reason = undocumented-path`).
6. otherwise → `unresolved`.

### 5.4 Self-audit metric (honesty, REQ-C2 ethos)

The spec summary surfaces **the fraction of `shadow` findings whose path is a suffix
of some documented path**. A high value means base-URL resolution is incomplete and
the shadow list is suspect — the write-up's 30-second self-audit, baked into output
so the data makes the point rather than static prose.

## 6. Data model + surfacing (mirror `finding_triage`)

### 6.1 `session_spec` (new table, RLS)

One active spec per session: `id, tenant_id, session_id (unique), spec_ref` (blob
key), `spec_format` (`openapi-3 | swagger-2`), `server_bases` (jsonb), `operation_count`,
`uploaded_by`/`actor`, `created_at`, `updated_at`. Re-upload upserts.

### 6.2 `finding_spec_status` (new table, RLS)

Keyed `UNIQUE(session_id, finding_hash)` (not a FK to `finding`, exactly like
`finding_triage`): `status` (`documented | shadow | unresolved`), `reason`,
`matched_operation` (nullable), `spec_ref` (which spec produced this classification —
provenance + staleness), timestamps. Upsert copied from `probe/triage.py:59-74`.

### 6.3 Attach + classify service (`recon.spec.service`)

`POST /runs/{run_id}/spec` (run supplies session scope + event correlation, exactly
as triage does): store the spec blob → upsert `session_spec` → query the session's
distinct endpoint findings → run §5 → upsert `finding_spec_status` rows → append a
durable `spec.classified` `run_event` (value-free: counts per bucket). Idempotent:
re-posting a spec re-tags. Returns the per-bucket summary + the self-audit ratio.

### 6.4 Surfacing

- `findings_router` (`findings_router.py:18`) gains a `spec_status` block per finding
  (mirroring the existing nested `triage` block) and a top-level `spec` summary
  (attached format, per-bucket counts, self-audit ratio, `null` when no spec attached).
- Read model `queries.list_findings` left-joins `finding_spec_status` on
  `(session_id, finding_hash)` — the same join shape as triage.
- UI: a spec-upload control on the run/findings view, a per-finding status chip, and a
  `spec_status = shadow` filter. (UI ships behind the same image-rebuild caveat as the
  prior slices; components + Vitest coverage land regardless.)

## 7. End-to-end data flow

```
Phase 1 (extraction):
  axios.create/defaults/const base ─▶ extract.py base-URL pre-pass ─▶ join at sink
     ─▶ normalize.py canonical op ─▶ endpoint finding  (value = "POST /location/address/search")

Phase 2 (attach-after):
  analyst uploads spec ─▶ recon.spec.ingest (validate, documented op set)
  session endpoint findings + documented ops ─▶ recon.spec.classify (§5)
     ─▶ finding_spec_status (session-scoped)  ─▶ findings read + UI (chips, shadow filter)
```

## 8. Error handling

- Invalid/unparseable/oversized spec → `422` (validator errors); nothing persisted.
- Spec with no `servers`/`basePath` → empty prefix (paths compared as-authored).
- `POST /runs/{id}/spec` for a run/session invisible to the tenant → `404` (RLS),
  distinct from a spec with zero documented ops (`200`, everything → shadow/unresolved).
- Re-classification is a full idempotent re-tag of the session's current findings; a
  `finding_spec_status` row whose `spec_ref` differs from the active `session_spec` is
  treated as **stale** in the read until re-classified.
- No endpoint findings yet → classification is a no-op (empty summary).

## 9. Testing (all host-lane — no katana/engines; the diff is decoupled)

- `extract_test`: `axios.create` instance with literal base; `axios.defaults.baseURL`;
  `const` template prefix; **ambiguous → unresolved, never guessed** (variable base,
  reassigned instance); the recognized-instance-but-unknown-base attribution case.
- `normalize_test`: base-join path composition; the wildcard compare-key.
- `spec/ingest_test`: OpenAPI 3.0, 3.1, Swagger 2.0, `servers`/`basePath`
  normalization, invalid → 422.
- `spec/classify_test`: all six decision branches (§5.3), suffix-match, verb mismatch,
  the canonical `/location/address/search` case (documented once the base resolves;
  a base-less `/address/search` → `unresolved/suffix-verify`, **never** shadow), and
  the self-audit ratio.
- `spec/service` + router tests: attach → classify → read; RLS 404; re-attach re-tags;
  `spec_status` nested in the findings read; `spec.classified` event recorded.

## 10. Out of scope / deferred (fast-follow)

- **Manual set-base-URL + wrapper-teaching** — the literal REQ-C2 SHOULD mechanism
  (operator declares a base / maps a call shape). This slice does the automatic subset;
  the manual override is the natural next increment (it needs its own data model + API
  + UI).
- **Cross-file / imported base resolution** beyond a single file.
- **Spec-by-URL fetch** (would reopen the SSRF/egress surface kept app-guarded).
- **OpenAPI/Swagger export** (extraction → spec file) — separate deliverable.
- **Reverse diff** ("documented but never called" — dead/unused spec paths).
- **Runtime-evidence shadow detection** (write-up #6 — diff a spec against observed
  traffic). We crawl for *assets*, not observed API traffic; this stays future work.

## 11. Open items / risks

- **Canonicalization parity.** The compare-key must reduce spec placeholders and client
  value-templates identically; a mismatch here silently re-introduces FPs. Covered by
  `classify_test` parity cases; the wildcard-`*` reduction is deliberately coarser than
  `finding_hash` templating and lives only in the classifier (it must not touch
  identity).
- **Swagger 2.0 breadth.** Only path/method + `basePath` are needed for the diff;
  2.0-specific constructs beyond that are ignored, not errored.
- **Session vs run scope.** Storage is session-scoped (survives re-runs); the shadow
  *view* is per-run. If an engagement legitimately needs different specs per run, that
  is a re-open (not anticipated).

## 12. REQ traceability

| REQ | How this slice touches it |
|---|---|
| REQ-C2 | Automatic cross-file-ish base-URL resolution (subset); honest coverage — partial paths are `unresolved`, never invented as shadow; self-audit metric. Manual set-base-URL + wrapper-teaching remain the deferred SHOULD. |
| REQ-D3 | Base-URL resolution changes the templated path inside `finding_hash` (host still excluded); one-time churn, no migration (§3.4). |
| REQ-D2 | Spec stored as a content-addressed `"spec"` blob (`storage.py`). |
| REQ-S1 | RLS on `session_spec` and `finding_spec_status` (tenant-scoped, FORCE RLS in the migration). |
| REQ-S3 | `spec.classified` durable, value-free audit event on each (re)classification. |
| — (extension) | Shadow-endpoint detection itself: an approved capability beyond the 40 REQ-* IDs, built on the OpenAPI substrate; recorded here as an explicit spec extension. |
