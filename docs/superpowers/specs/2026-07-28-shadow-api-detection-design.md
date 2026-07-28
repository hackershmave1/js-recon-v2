# Shadow-API detection — base-URL resolution + spec diff (design)

- **Date:** 2026-07-28
- **Status:** approved (brainstorming); **§4 adversarial design gate PASSED 2026-07-28
  = BUILD WITH CHANGES** — six code/doc-verified blockers folded in (see §13).
  Pending the implementation plan; the higher-model code review (§4 gate 2) is owed
  after build.
- **Slice:** a new slice that makes the platform answer *"which endpoints does the
  target's JavaScript call that its published API spec does not document?"* Two phases
  in one slice: **(1)** conservative **base-URL resolution** inside the existing
  extractor (so recovered client paths are *full*, not partial), and **(2)** a **spec
  diff** that ingests a user-supplied OpenAPI/Swagger spec and classifies each
  extracted endpoint as **documented / shadow / unresolved**.
- **Primary REQ:** REQ-C2's **honesty/coverage ethos** is advanced (partial paths are
  never invented as shadow). The literal REQ-C2 SHOULD — a *manual* set-base-URL +
  wrapper-teaching-by-call-shape — is **deferred** (§2.2, §10); this slice adds an
  *automatic subset* REQ-C2 does not itself describe. Touches REQ-D3 (base-URL
  resolution changes the templated path inside `finding_hash` — §3.4), REQ-S1 (RLS on
  the two new tables), REQ-D2 (spec stored as a blob by key), REQ-D5 (classification is
  kept current across rescans — §6.3), REQ-S3 (a `spec.classified` audit event).
  **Shadow detection itself is not one of the 40 REQ-* IDs** — recorded here as an
  explicit, approved **spec extension**.
- **Source concern:** `docs/shadow-api-false-positives.md` (the portable write-up and
  its six preventions). This design implements preventions #1–#4 and leverages the AST
  the extractor already uses (#5); #6 (runtime evidence) stays out of scope (§10).

## 1. Context

The extractor (`src/recon/findings/extract.py`, "Vespasian") is **AST-based** —
tree-sitter (`extract.py:26`) — and already resolves per-call literal URLs for
`fetch`, XHR, `axios.*`, jQuery, and WebSocket. Two gaps make honest shadow detection
impossible today:

1. **No base-URL resolution.** The extractor is a stateless per-call walk with no
   data-flow (`extract.py:14-18`). `axios.create({ baseURL })` and every call on the
   returned instance are **silently dropped** — `_axios_member` returns on any member
   that is not an HTTP verb or `request` (`extract.py:273`). `_dispatch_member`
   (`extract.py:217-226`) recognizes `fetch` on the global objects, `.open(...)` on
   **any** receiver (an existing XHR false-positive surface, acknowledged at
   `extract.py:245-247`), and `axios`/jQuery member calls — but a call on an
   `axios.create()` *instance identifier* matches none of these and is dropped. These
   endpoints are invisible today (a false **negative**); recovered *without* their
   base they become the false **positive** the write-up describes.
2. **No documented side, no diff.** Nothing under `src/` ingests or diffs against an
   OpenAPI/Swagger spec. (OpenAPI *export* — extraction → a spec file — is a separate,
   deferred deliverable and is **not** the input to this diff.)

What already exists and is reused:

- **Canonicalization** (`normalize.py`). `endpoint_operation(method, url)` produces
  `METHOD /templated-path` (`normalize.py:218-221`); `_templatize_path` /
  `template_segment` collapse numeric → `{id}`, UUID → `{uuid}`, hex/high-entropy →
  `{hash}` conservatively (`normalize.py:182-203`); the query reduces to sorted,
  de-duped names; `operation_of_endpoint_value` strips the `?query` suffix
  (`normalize.py:238-243`). `Endpoint.host` is kept **off** the finding hash "so
  REQ-C2 base-URL re-resolution cannot churn identity" (`normalize.py:165-168`) — the
  data model already left a seam for this work.
- **Per-run classification pattern** (`finding_triage`). A user verdict is stored keyed
  `(session_id, finding_hash)` — session-scoped, deliberately not run-scoped, "so it
  survives re-runs" (`models.py:340-370`) — upserted via
  `pg_insert(...).on_conflict_do_update` with a durable `triage.updated` event
  (`probe/triage.py:30-93`), surfaced nested per finding in the findings read
  (`findings_router.py:42-51`). Shadow classification mirrors this exactly.
- **Blob storage** by content-addressed, tenant-scoped key (`storage.py:24-32`); a new
  artifact kind is a one-line `BLOB_KINDS` addition.
- `openapi-spec-validator` is a **planned Vespasian dependency**
  (`Developer Requirements.dc.html:481`) — reused to validate the ingested spec, under
  the hardening of §4.1.

### 1.1 The false-positive mechanism (why this is the whole point)

Shadow = `called − documented`. The diff is only as correct as the *called* set.
Static extraction recovers a **fragment** when it misses a base URL: the client sets
`axios.create({ baseURL: '/location' })` once, then calls `.post('/address/search')`;
a per-call scan sees `/address/search`, the spec documents
`/location/address/search`, and the diff mislabels a fully-documented call as shadow.
A single `create` can taint dozens of endpoints. The fix, in leverage order:
base-URL resolution + a classifier that **never labels a partial path as shadow**.

## 2. Settled decisions

Decided during brainstorming (2026-07-27/28); binding for this slice unless re-opened.

1. **Its own slice**, full superpowers flow.
2. **Base-URL resolution is sequenced first**, and is **auto-conservative**: resolve
   only safe, in-file, literal cases; anything ambiguous stays `unresolved`, never
   guessed. The spec's literal REQ-C2 mechanism (manual set-base-URL + wrapper-teaching)
   is deferred to a fast-follow (§10).
3. **Documented set = a user-supplied OpenAPI/Swagger spec** we ingest — not one we
   generate. **Attach-after** intake: the analyst uploads/pastes a spec via a dedicated
   action after endpoint findings exist; classification runs on demand and re-tags when
   a newer spec is supplied. No spec-by-URL fetch (SSRF surface).
4. **Output = per-run classification.** Each endpoint finding gets a `spec_status`
   (documented / shadow / unresolved) against the supplied spec, **stored session-scoped**
   (keyed `(session_id, finding_hash)`, mirroring `finding_triage`) and **surfaced
   per-run** by filtering a run's findings. Chosen over a separate spec-diff artifact
   and over a first-class "shadow" finding type.
5. **OpenAPI/Swagger export stays out of scope.**
6. **Formats:** OpenAPI 3.0 / 3.1 **and** Swagger 2.0 (validated via
   `openapi-spec-validator`; `servers`/`basePath` normalized, server variables
   resolved — §4).
7. **Verb mismatch** (path documented, method not) → `shadow` with `reason =
   undocumented-method`, distinct from `undocumented-path` — **but only after the
   suffix-verify net (§5.3)**.
8. **Never binary.** Only a *complete, fully-resolved, statically-certain* path may be
   shadow; anything partial/interpolated/base-less, or a suffix of a documented path,
   goes to `unresolved`.

## 3. Phase 1 — base-URL resolution (`extract.py` + `normalize.py`)

### 3.1 A binding pre-pass

Before the sink walk, one pass collects an in-file **base environment** of only
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
  requires `_dispatch_member` (`extract.py:217`) to recognize base-environment instance
  identifiers in addition to `axios`/jQuery/globals. **Note the `.open(...)` interaction:**
  `_dispatch_member` matches `prop == "open"` on *any* receiver first (`extract.py:220`),
  so `NAME.open(...)` still routes to the XHR handler — instance dispatch applies only to
  the HTTP-verb / `request` members axios uses.
- A bare `axios(...)` / `axios.<verb>(...)` with a relative URL, when
  `axios.defaults.baseURL` is known, joins the default base.
- A template URL beginning `` `${NAME}/rest` `` where `NAME` is a const literal folds to
  `join(NAME_value, "/rest")`.
- `join(base, path)`: an absolute `path` (its own scheme/host) wins and `base` is
  ignored; otherwise the base path-prefix is prepended (`"/location"` +
  `"/address/search"` → `"/location/address/search"`, slashes de-duped). An absolute
  `base` (`https://api.host/v3`) contributes host + prefix; `normalize` then splits the
  host onto the occurrence as today.

### 3.3 Conservatism (never guess a base)

- **Literal bases only.** A `baseURL` that is a variable/expression → the instance is
  recognized but its base is **unknown**; its calls are *attributed* (counted, path kept
  relative) and land in `unresolved` — never guessed. This alone fixes the current
  silent-drop false negative.
- **Scope-safe single binding (gate B1).** tree-sitter provides *no* scope resolution
  (`extract.py:79-84` is a flat pre-order walk). Enforce "single binding" against **every
  re-introduction of the identifier text** — nested `const/let/var`, function
  declarations, and **formal / catch / arrow parameters** — not just re-assignment. Any
  collision → treat the identifier as unresolvable (its calls stay relative/unresolved).
  An outer instance name must never bind to an inner shadowed use, e.g.:
  ```js
  const api = axios.create({ baseURL: '/api' });
  fns.forEach((api) => { api.get('/local/thing'); }); // param `api` shadows → do NOT prepend /api
  ```
- **Per-source-unit, not "the bundle" (gate N2).** "In-file" means *within one analyzed
  source unit*. Analyze extracts each recovered source independently (`analyze.py`), so
  **with a source map** a shared `apiClient.js` `create()` and its call sites in other
  modules are different units → unresolved; the map-less single-bundle case is one unit
  and resolves. Cross-unit resolution is the deferred manual/wrapper thread (§10).
- A wrong base is worse than no base — it converts a real shadow into a false
  "documented" (a missed finding). This bias matches `normalize.py`'s existing rule that
  "an over-merge silently loses attack surface, so ambiguous stays literal".

### 3.4 Identity churn (gate N4 — expanded)

Folding a base into the path changes the templated path inside `finding_hash` for
affected endpoints (host stays off the hash; the base **path** is legitimately part of
the operation identity). Consequences, all assessed:

- One-time REQ-D5 diff churn on the first run after this ships (old hash "removed", new
  "added") for rebased endpoints.
- **PARAM findings on rebased endpoints re-key too**, not just endpoints — a param's
  value embeds the operation via `normalize.normalize_param_value(endpoint_operation(...))`
  (`analyze.py:411-413`).
- Session-scoped `finding_triage` verdicts on rebased hashes stop matching (the row
  persists under the old hash). **We do not build a triage-remap migration now** (pre-prod,
  no real triage data, YAGNI) — documented as accepted analyst-state loss; revisit if real
  data exists.
- **The REQ-A3 outbox is unaffected** (gate-confirmed): `store.record_finding` recomputes
  `finding_hash` and dedupes on `(run_id, finding_hash)` (`store.py:86,101`), deterministic
  within a run — no migration needed for finding-row integrity.

## 4. Phase 2a — spec ingest (`recon.spec.ingest`)

- Accept a spec as an uploaded file or pasted text (`POST /runs/{run_id}/spec`, §6.3).
  Detect + validate OpenAPI 3.0/3.1 and Swagger 2.0 via `openapi-spec-validator` (under
  §4.1); an invalid/unparseable spec → `422` with the validator's errors.
- **Documented operation set:** for every `(path, method)`, prepend the base path from
  the spec's servers and reduce to the canonical compare-key of §5.1. Resolve
  OpenAPI 3.x `servers[].url` `{variable}` templates via their `default` (respecting
  `enum`) **before** deriving the base (gate B5), across all `servers` entries (stored in
  `server_bases`, §6.1); Swagger 2.0 uses `basePath`. Path-level / operation-level
  `servers` overrides are **out of MVP scope** and recorded as a known under-resolution
  (§10/§11) — such ops may mis-bucket, documented rather than silently wrong.
- Store the raw spec as a `"spec"` blob (§6.1).

### 4.1 The spec body is untrusted input (gate B4)

An uploaded spec is untrusted on the same footing as target JS. `openapi-spec-validator`
and generic YAML/JSON loaders will, by default, expand YAML aliases/anchors and resolve
`$ref` over the filesystem and network (its `validate_url` / `base_uri` paths fetch
`file://` and HTTP refs) — an SSRF / local-file-read / resource-exhaustion side-window
that would bypass the platform's egress guard (`fetch/egress.py`) and contradict §2.3's
"no spec-by-URL". Mitigations, all required:

- Parse with a **hardened loader**: disable/deny alias & anchor expansion beyond a small
  bound (defeats billion-laughs); enforce a max source size **and** post-parse max
  node-count / nesting depth.
- Confine `$ref` resolution to **in-document JSON-pointers only** (`#/...`): reject any
  `$ref` whose target is `file://`, `http(s)://`, or otherwise non-local — register **no**
  network/file handler. Cyclic in-document `$ref` handled with a visited-set.
- A spec that *requires* external refs → `422` (unsupported), never a fetch.

## 5. Phase 2b — classify (`recon.spec.classify`)

### 5.1 One compare-key, both sides

Reduce every operation — client finding and spec op alike — to `METHOD` + a path in
which **every path-parameter position is wildcarded to `*`**: a spec `{petId}`, a
`normalize` value-template (`{id}`/`{uuid}`/`{hash}`), a bare numeric/uuid segment, and a
single-segment client interpolation (§5.2) all collapse to `*`. Comparison is over
canonical forms, never raw strings (write-up prevention #1). Strip the finding value's
`?query` suffix via `normalize.operation_of_endpoint_value` before comparing (gate N6).
**Only methods in `extract.HTTP_METHODS` are classified** — WebSocket `WS`/`WSS` and any
non-HTTP verb are structurally undocumentable in OpenAPI (gate B3) and route to
`unresolved` (`reason = non-http`), never shadow, and are excluded from the §5.4 audit
denominator.

### 5.2 Statically-certain vs partial

- A path segment that is **exactly** a single interpolation `${...}` in a **non-leading**
  position is a *parameter* position → wildcarded to `*` and remains matchable, so
  `/users/${id}` matches spec `/users/{petId}` → `documented` (gate N1; resolves the prior
  §5.1/§5.2 contradiction).
- A path is **partial** (→ `unresolved`, never shadow) when: a **leading** `${...}`
  (unresolved base) is present; **or** a segment mixes literal + interpolation
  (`v${n}`, `${a}${b}`) so it cannot be cleanly wildcarded; **or** a required base was
  unknown.
- Value-templates (`{id}`/`{uuid}`/`{hash}` from observed literals) are *certain* and
  never make a path partial.

### 5.3 Decision order (first match wins) — suffix-verify precedes all shadow (gate B2)

For each endpoint finding in the run:

1. non-HTTP method → `unresolved` (`reason = non-http`).
2. **partial** (leading/mixed interpolation, or base-unresolved-relative) → `unresolved`
   (`reason = partial | base-unresolved`).
3. compare-key exactly matches a documented op (same method + wildcard path) →
   `documented` (`matched_operation` recorded).
4. finding path is a **proper suffix** of some documented path (strictly shorter, after
   wildcarding), or a documented path is a proper suffix of it → `unresolved`
   (`reason = suffix-verify`, "likely documented, base probably missing"). **This runs
   before any shadow verdict** — the safety net that kills residual base-URL FPs
   (write-up #3).
5. wildcard path matches a documented path under a **different** method → `shadow`
   (`reason = undocumented-method`).
6. complete + statically-certain, no match → `shadow` (`reason = undocumented-path`).
7. otherwise → `unresolved`.

Worked example (gate B2): spec has `POST /search` **and** `GET /location/address/search`;
client `fetch('/search')` (base unresolved). Old order emitted `shadow/undocumented-method`
at step 5; new order hits step 4 (proper suffix of `/location/address/search`) →
`unresolved/suffix-verify`. Correct.

### 5.4 Self-audit metric (honesty, REQ-C2 ethos)

Surfaced over the **run being viewed** (run-scoped, to reconcile with per-run surfacing —
gate N7): the fraction of that run's `shadow` findings whose path is a proper suffix of
some documented path (non-HTTP excluded). A high value means base-URL resolution is
incomplete and the shadow list is suspect — the write-up's 30-second self-audit, baked in.

## 6. Data model + surfacing (mirror `finding_triage`)

### 6.1 `session_spec` (new table, RLS)

One active spec per session: `id, tenant_id (NOT NULL FK), session_id (unique), spec_ref`
(blob key), `spec_format` (`openapi-3 | swagger-2`), `server_bases` (jsonb, resolved),
`operation_count, uploaded_by/actor, created_at, updated_at`. Re-upload upserts. **Blob
lifecycle latent (gate N5):** the spec blob is stored under the *attaching* run's key
(`{tenant}/{run}/spec/{sha}`, `storage.py` convention) while the pointer is
session-scoped; tenant isolation holds (tenant in the key), but re-uploads don't dedupe
and the blob ties to a run — accepted latent, matters only if run-scoped blob GC is added.

### 6.2 `finding_spec_status` (new table, RLS) — gate B6

Keyed `UNIQUE(session_id, finding_hash)` (not a FK to `finding`, exactly like
`finding_triage`): **`tenant_id` (NOT NULL, FK `tenant` ON DELETE CASCADE)**,
`session_id`, `finding_hash`, `status` (`documented | shadow | unresolved`), `reason`,
`matched_operation` (nullable), `spec_ref` (which spec produced this — provenance +
staleness), `created_at`, `updated_at`. Upsert copied from `probe/triage.py:59-74`.
Add `SPEC_TABLES = ("session_spec", "finding_spec_status")` in `models.py`; migration
`0006` enables + FORCEs RLS and creates the `tenant_isolation` policy + GRANTs by looping
`SPEC_TABLES`, mirroring `0004_finding_triage.py` exactly (`0004:34-43`).

### 6.3 Attach + classify service (`recon.spec.service`)

Classification triggers **(a) on attach** — `POST /runs/{run_id}/spec` (run supplies
session scope + event correlation, exactly as triage does): store the spec blob → upsert
`session_spec` → query the session's distinct endpoint findings → run §5 → upsert
`finding_spec_status` → append a durable value-free `spec.classified` `run_event`
(per-bucket counts) — **and (b) automatically at analyze-finalize** for any run whose
session already has a `session_spec`, so REQ-D5 continuous rescans keep new findings
classified without a manual re-POST (gate N3). Both paths call the same **pure** classify
service (DB rows + parsed spec; no katana/engines → host-lane testable). Idempotent:
re-posting a spec re-tags. Returns the run-scoped per-bucket summary + self-audit ratio.

### 6.4 Surfacing

- `findings_router` (`findings_router.py:18`) gains a `spec_status` block per finding
  (mirroring the nested `triage` block) and a top-level `spec` summary (attached format,
  run-scoped per-bucket counts, self-audit ratio; `null` when no spec attached). A finding
  with **no** `finding_spec_status` row is surfaced as **`unclassified`** (distinct from a
  bucket and from `null`, gate N3) — an un-diffed finding is visibly un-diffed.
- Read model `queries.list_findings` left-joins `finding_spec_status` on
  `(session_id, finding_hash)` — the same join shape as triage.
- UI: a spec-upload control, a per-finding status chip (incl. `unclassified`), and a
  `spec_status = shadow` filter. (UI ships behind the same image-rebuild caveat as prior
  slices; components + Vitest coverage land regardless.)

## 7. End-to-end data flow

```
Phase 1 (extraction):
  axios.create/defaults/const base ─▶ extract.py base-URL pre-pass (scope-safe) ─▶ join at sink
     ─▶ normalize.py canonical op ─▶ endpoint finding  (value = "POST /location/address/search")

Phase 2 (attach-after + auto at finalize):
  analyst uploads spec ─▶ recon.spec.ingest (harden, validate, resolve servers, documented op set)
  session endpoint findings + documented ops ─▶ recon.spec.classify (§5)
     ─▶ finding_spec_status (session-scoped)  ─▶ findings read + UI (chips, shadow filter)
```

## 8. Error handling

- Invalid/unparseable/oversized spec, or one needing external `$ref` → `422`; nothing
  persisted (§4.1).
- Spec with no servers/basePath → empty prefix (paths compared as-authored).
- `POST /runs/{id}/spec` for a run/session invisible to the tenant → `404` (RLS),
  distinct from a spec with zero documented ops (`200`, everything → shadow/unresolved).
- Re-classification is a full idempotent re-tag of the session's current findings; a
  `finding_spec_status` row whose `spec_ref` differs from the active `session_spec` is
  treated as **stale** in the read until re-classified; a finding with no row at all is
  **`unclassified`** (§6.4).
- No endpoint findings yet → classification is a no-op (empty summary).

## 9. Testing (all host-lane — no katana/engines; the diff is pure)

- `extract_test`: `axios.create` instance with literal base; `axios.defaults.baseURL`;
  `const` template prefix; **ambiguous → unresolved, never guessed** (variable base,
  reassigned instance); **scope-collision bail** (callback/param shadowing an instance
  name, gate B1); recognized-instance-but-unknown-base attribution; `NAME.open(...)` still
  routes to XHR (gate F1).
- `normalize_test`: base-join path composition; the wildcard compare-key; single-segment
  `${id}` → `*` (gate N1).
- `spec/ingest_test`: OpenAPI 3.0, 3.1, Swagger 2.0; `servers[].url` variable resolution
  (gate B5); `basePath`; invalid → 422; **external `$ref` (`file://`/http) rejected, YAML
  alias-bomb rejected** (gate B4).
- `spec/classify_test`: all seven branches (§5.3), suffix-before-verb order + the B2
  worked example, non-HTTP/WS → unresolved (gate B3), `/users/${id}` → documented (N1),
  the canonical `/location/address/search` case, self-audit ratio.
- `spec/service` + router tests: attach → classify → read; **auto-reclassify at
  analyze-finalize when a spec exists** (gate N3); `unclassified` vs a bucket; RLS 404
  and **tenant isolation on `finding_spec_status`** (gate B6); re-attach re-tags;
  `spec_status` nested in the read; `spec.classified` event recorded.

## 10. Out of scope / deferred (fast-follow)

- **Manual set-base-URL + wrapper-teaching** — the literal REQ-C2 SHOULD.
- **Cross-file / cross-source-unit base resolution** (§3.3, N2).
- **Triage-verdict remap** across the base-URL hash churn (§3.4, N4).
- **Path-level / operation-level `servers` overrides** (§4, B5).
- **Spec-by-URL fetch** (SSRF/egress surface).
- **OpenAPI/Swagger export** (extraction → spec file).
- **Reverse diff** ("documented but never called" — dead spec paths).
- **Runtime-evidence shadow detection** (write-up #6 — spec vs observed traffic; we crawl
  for *assets*, not observed API traffic).

## 11. Open items / risks

- **Canonicalization parity.** The compare-key must reduce spec placeholders, client
  value-templates, and single-segment `${...}` identically; a mismatch silently
  re-introduces FPs. Covered by `classify_test` parity cases; the wildcard-`*` reduction
  is deliberately coarser than `finding_hash` templating and lives only in the classifier
  (never touches identity).
- **Server-variable / servers-override coverage.** Only `servers[].url` `default` values
  are resolved; enums beyond default and path/op-level overrides are deferred (§10) — such
  ops may mis-bucket, surfaced honestly (self-audit ratio will flag suffix-heavy shadows).
- **Swagger 2.0 breadth.** Only path/method + `basePath` feed the diff; other 2.0
  constructs are ignored, not errored.
- **Session vs run scope.** Storage is session-scoped (survives re-runs); the shadow view
  and the summary/ratio are run-scoped (§5.4/§6.3). Different specs per run is a re-open.

## 12. REQ traceability

| REQ | How this slice touches it |
|---|---|
| REQ-C2 | Automatic cross-unit-limited base-URL resolution (subset); honest coverage — partial paths are `unresolved`, never invented as shadow; self-audit metric. Manual set-base-URL + wrapper-teaching remain the deferred SHOULD. |
| REQ-D3 | Base-URL resolution changes the templated path inside `finding_hash` (host excluded); one-time churn + PARAM re-key, no migration (§3.4). |
| REQ-D5 | Classification kept current across continuous rescans via auto-reclassify at finalize (§6.3); `unclassified` state prevents silent under-reporting. |
| REQ-D2 | Spec stored as a content-addressed `"spec"` blob (`storage.py`). |
| REQ-S1 | RLS on `session_spec` and `finding_spec_status` (both carry `tenant_id`; `SPEC_TABLES` FORCE RLS in migration `0006`, §6.2). |
| REQ-S3 | `spec.classified` durable, value-free audit event on each (re)classification. |
| — (extension) | Shadow-endpoint detection itself: an approved capability beyond the 40 REQ-* IDs, built on the OpenAPI substrate; recorded as an explicit spec extension. |

## 13. §4 adversarial design gate (2026-07-28)

Opus adversarial reviewer, proof-bound (each objection cited exact code lines or official
docs). **Verdict: BUILD WITH CHANGES.** All six blockers folded in; the skeleton
(triage-mirrored storage, host-off-hash identity, blob spec, conservative three-bucket
classifier) was validated against the real code, and the reviewer confirmed the REQ-A3
outbox is not broken by the hash change.

| Item | Finding (proof) | Resolution |
|---|---|---|
| **B1** | Pre-pass not scope-aware — `_walk` (`extract.py:79-84`) is flat; a callback param shadowing an instance name → FP **and** FN shadows | §3.3 scope-safe single binding (bail on any identifier re-introduction incl. params) |
| **B2** | Verb-mismatch→shadow fired before suffix-verify; base-less relative literal not "partial" → canonical FP leaked as `undocumented-method` | §5.2 partial refined; §5.3 suffix-verify moved ahead of both shadow branches (worked example) |
| **B3** | `WS`/`WSS` undocumentable in OpenAPI but classified → false shadow | §5.1 only `extract.HTTP_METHODS` classified; non-HTTP → `unresolved/non-http` |
| **B4** | Untrusted spec → `openapi-spec-validator` resolves `file://`/HTTP `$ref` + YAML alias bombs → SSRF/local-read/exhaustion | new §4.1 hardened loader, in-document `$ref` only, bounded parse |
| **B5** | `servers[].url` `{variable}` templates taken literally → documented calls flagged shadow | §4 resolve server variables via `default`/`enum` before deriving base |
| **B6** | `finding_spec_status` lacked `tenant_id` → RLS policy inapplicable → cross-tenant leak | §6.2 add `tenant_id` + `SPEC_TABLES` + migration `0006` mirroring `0004` |

Non-blocking folded: N1 (single-segment `${id}` → param → matchable/documented, §5.2),
N2 (per-source-unit boundary, §3.3), N3 (auto-reclassify at finalize + `unclassified`
state, §6.3/§6.4), N5 (blob-lifecycle latent noted, §6.1), N6 (reuse
`operation_of_endpoint_value`, §5.1), N7 (run-scoped summary/ratio, §5.4). Deferred: N4
triage-verdict remap (§3.4/§10 — pre-prod, YAGNI). Corrected false/stale claims: F1
(`_dispatch_member` also handles `.open` on any receiver — §1/§3.2), F2 (REQ-C2 is
advanced in *ethos*, the literal SHOULD deferred — preamble).
