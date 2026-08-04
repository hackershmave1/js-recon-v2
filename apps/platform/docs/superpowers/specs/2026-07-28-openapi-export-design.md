# OpenAPI export — thin serializer over reconstructed findings (design)

- **Date:** 2026-07-28
- **Status:** approved (brainstorming); the **§4 adversarial design gate is owed next**, before the
  implementation plan. Higher-model code review (§4 gate 2) owed after build.
- **Slice:** a small slice that makes the platform answer *"emit the API we statically reconstructed
  from the target's JavaScript as an OpenAPI document."* It is the **inverse of the shipped spec-ingest**
  slice: ingest reads a spec the analyst supplies (to find shadow endpoints); export **writes out** the
  spec we reconstructed. One new pure serializer + one thin route.
- **Primary REQ:** fulfils Vespasian's charter role *"Endpoints · params · OpenAPI spec"*
  (`Javascript recon app redesign/Developer Requirements.dc.html:478`). Shadow/export are **not** among
  the 40 numbered REQ-* IDs — recorded here as an approved **spec extension**, exactly as shadow
  detection was. Touches REQ-S1 (tenant-scoped read), REQ-P1/P2 (respected — see §2), and REQ-D2
  lightly (an artifact, produced on-demand rather than stored).
- **Primary consumer:** the **Slice 4 grounded threat model** — the export is the LLM-digestible
  grounded-evidence *bridge* into it (the same pure function feeds the threat model in-process).
  **Secondary consumer:** **Burp** import (analyst downloads the file).

## 1. Context

`probe/reconstruct.py` already aggregates the run's ENDPOINT + PARAM findings into per-operation
request objects — this is the inverse-shaped data the exporter needs, already built:

- `ReconstructedRequest(operation, method, path, hosts, query_params, body_params, content_type,
  example_url, probeable, endpoint_hashes)` (`reconstruct.py:34-45`); `QueryParam(name, example)`
  (`:28-31`).
- `build_requests(findings)` groups by canonical operation, unions params, collects hosts, sets
  `content_type="application/json"` only when body params exist and all kinds ∈ `{fetch, axios}`, and
  marks `probeable=False` for WS/WSS (`reconstruct.py:53-127`).
- `reconstruct_run(tenant_id, run_id)` is **pure** over `queries.list_findings` and returns `None` on an
  unknown/invisible run (`reconstruct.py:130-136`) — this is the RLS-safe 404 seam.

`probe/serialize.py` is the existing precedent for "serialize a reconstructed request to an output
format": `to_curl` (`:59-90`), `to_http` (`:93-107`). A whole-run OpenAPI builder is the same family.

**Canonicalization already done** (`normalize.py`): an operation value is `METHOD /templated-path`
(`endpoint_operation`, `:218-221`); path params are templated to `{id}`/`{uuid}`/`{hash}`. Endpoint
`host` is carried per-occurrence, off the finding hash.

**What is missing:** nothing under `src/` emits a spec. Every `openapi`/`swagger` reference in
`src/recon` is the ingest/validate path or the shadow-slice DB column. This slice adds the emitter.

**Binding platform constraint:** the platform sends **no automated active traffic** (REQ-P1 — manual
probe handoff) and its egress is static asset fetch/crawl only (REQ-P2). The export is therefore a
**pure static serialization of already-stored findings** — it introduces no new egress and no probing.

## 2. Prior art — why we build this, not adopt an off-the-shelf tool

Researched 2026-07-28 (web). Two real tools emit OpenAPI; neither fits this platform's constraints:

| | `praetorian-inc/vespasian` | `shriyanss/js-recon` | This serializer |
|---|---|---|---|
| Emits OpenAPI | yes (3.0) | yes | yes (3.0.3) |
| Input | **live/captured traffic** (crawl, Burp/HAR/mitmproxy) | static JS | **our stored findings** |
| Sends active traffic | yes (OPTIONS, introspection, schema-from-responses) | no | no |
| Fits REQ-P1/P2 (no active traffic) | **no** | yes | yes |
| Response schemas | yes (from real responses) | undocumented | no (honestly "not observed") |
| Integrated w/ runs, tenancy, threat model | no | no (external file) | **yes, in-process** |
| New engine to run | Go + Chrome | Node CLI | none — findings already extracted |

- This repo's "Vespasian" only **borrowed the name** from Praetorian's tool; ours is a static
  tree-sitter reimplementation. Praetorian Vespasian builds its (richer) spec from **live traffic +
  active probing** — exactly what REQ-P1/P2 forbid here; its response-schema richness comes from data
  we deliberately do not collect.
- `js-recon` is a static Node CLI that also emits OpenAPI, but adopting it means running another engine
  to **re-derive endpoints/params we already hold as findings**, then mapping its opaque file back into
  our per-run/tenant model. The expensive part (static extraction) is done; export is thin serialization.
- **Accepted limitation:** a static spec has **no response schemas** (the one thing a traffic tool gets
  for free). Marked honestly (§4). Enriching from captured traffic is the deferred runtime path (§9).

## 3. Settled decisions

Binding for this slice unless re-opened.

1. **Approach A — on-demand, pure.** A new pure module serializes `reconstruct_run` output to an OpenAPI
   `dict`; a `GET` route generates → validates → returns per call. **No persistence, no finalize hook,
   no new blob kind.** (Persisted-artifact approach B is deferred — §9.)
2. **OpenAPI 3.0.3.** Chosen over 3.1 for the widest Burp/tooling import support. Swagger 2.0 export is
   out of scope (§9).
3. **JSON default; YAML via `?format=yaml`.** Both are free (`pyyaml` present). No new dependency.
4. **Validate before returning.** Every emitted document is run through `openapi-spec-validator`
   (`validate`, already a dep) before it leaves the process — we only ever hand back a document that
   parses as valid OpenAPI. A failure is an internal bug (500), and the host-lane tests assert it never
   happens.
5. **Honesty is enforced in the mapping (§5), never fabricated.** Certain vs inferred vs absent is
   carried in plain-language `description` fields (LLM-legible, Burp-ignored) plus a light
   `x-recon-confidence` extension (machine-readable, ignored by importers).
6. **No security asserted.** Headers/auth are not captured by the model — the spec emits no
   `securitySchemes` and no security requirements. Honestly blank, never guessed.

## 4. Components (files)

```
src/recon/probe/openapi.py          NEW  pure: build_openapi(requests, *, run_id) -> dict
                                              dump_openapi(doc, fmt) -> (bytes, media_type)   # route composes the filename from run_id
src/recon/probe/openapi_test.py     NEW  host-lane, colocated
src/recon/api/export_router.py      NEW  GET /runs/{run_id}/export/openapi?format=json|yaml
src/recon/api/export_router_test.py NEW  integration, mirrors spec_router_test.py
src/recon/api/app.py                EDIT app.include_router(export_router) (with the other routers, :29-33)
```

`build_openapi` does **no finding logic** — it shapes `ReconstructedRequest[]` into OpenAPI. It lives in
its own `probe/openapi.py` (whole-run document builder) rather than in `serialize.py` (single-request
`to_curl`/`to_http`) to keep each file single-purpose and under the size guideline. The Slice 4 threat
model will call `build_openapi(reconstruct_run(...))` directly, in-process — no HTTP.

## 5. The findings → OpenAPI mapping (honesty enforced here)

For each `ReconstructedRequest`:

- **Operation gate.** Include only `method ∈ extract.HTTP_METHODS` (`extract.py:36`). WS/WSS
  (`probeable=False`, method synthesized `WS`/`WSS` at `extract.py:594`) are **excluded from `paths`**
  and surfaced under a top-level `x-recon-websocket-endpoints` list so they are not silently lost.
- **`paths` + operation.** `method.lower()` → the operation. The `path` is **canonicalized first**
  (below) — it is *not* safe to emit as-is.
- **Path parameters — canonicalize EVERY interpolation, not just the three tokens (gate Blocker 1).**
  `normalize.py` only collapses recognized segments to `{id}`/`{uuid}`/`{hash}` and passes **every other
  interpolation through verbatim** (`template_segment`, `normalize.py:182-196`); static extraction stores
  `${…}` literally (`extract.py:96-112`, `extract_test.py:34-35`). So a real `path` routinely contains
  `${user.id}`, `{userId}`, `v${n}`, or an unbalanced `{` — each an **undeclared path-template variable
  that `openapi-spec-validator` rejects** (empirically `UnresolvableParameterError`), which would make the
  §4 self-validation 500 on the common case. The builder therefore canonicalizes each path segment before
  emit:
  - A segment that is **exactly one** recognized token (`{id}`/`{uuid}`/`{hash}`) → a path param with the
    inferred type (`{id}`→`integer`, `{uuid}`→`string`+`format: uuid`, `{hash}`→`string`).
  - A segment containing **any other** interpolation — `${expr}`, a bare `{name}`, or a mixed
    literal+interp (`v${n}`, `${a}${b}`) — → **one** synthesized path param: strip the literal `$`, and if
    the whole segment is a single clean identifier reuse it as the name (`${userId}`→`userId`), otherwise
    synthesize a positional legal name (`p1`, `p2`, …). Type defaults to `string`. The **entire segment**
    is replaced by `{<name>}`, so no stray/partial brace survives.
  - **Every** synthesized `{name}` gets a matching `parameters` entry (`required: true`); names are made
    **unique within the path** (`id`, `id2`, …); each carries an honest description ("Name synthesized /
    type inferred from a templated path segment; original name not recoverable from static analysis").
  - **Collision guard:** canonicalization can map two operations onto the same `(method, path)` (e.g.
    `GET /users/${id}` and `GET /users/{id}`) — dedupe/**merge** them (union params + body), never let one
    silently overwrite the other (loss of attack surface is worse than a merge).
  The invariant this buys: after canonicalization a path contains only balanced `{legalName}` tokens, each
  with a declared parameter — so the emitted document always validates.
- **Query parameters.** From `query_params`: `in: query`, `required: false`, `schema: {type: string}`
  (inferred), `description: "Name observed; type inferred."`. The `example` key is emitted **only when
  non-null** (`QueryParam.example` is often `None`, `reconstruct.py:100`) — never `example: null`.
- **Request body — never assert an unobserved content-type (gate note 1).** When `body_params` is
  non-empty:
  - `content_type` **set** (reconstruct sets `application/json` only for `fetch`/`axios`,
    `reconstruct.py:121`) → a `requestBody` (`required: false`) under that media type →
    `schema: {type: object, properties: {<name>: {type: string, description: "Name observed; type
    inferred."}}}` + schema note `"Property names observed statically; types inferred; not exhaustive."`
  - `content_type` **None** (jQuery/xhr — the type was *not* observed, and `ReconstructedRequest` carries
    no `kind` to guess from, `reconstruct.py:34-45`) → do **not** invent `application/json`. Surface the
    body property names honestly instead — in the operation `description` and an `x-recon-body-params`
    extension — with **no** `requestBody.content` media type asserted.
- **Responses.** OpenAPI requires ≥1 response; we observed none →
  `responses: {default: {description: "Not observed — static analysis does not capture responses."}}`.
- **`servers`.** Derived from the union of observed `hosts` (prefer scheme **and port** from `example_url`
  when it carries them — `normalize.py:229` stores `hostname` only, dropping scheme/port; otherwise
  default `https://` and mark it inferred in the server `description`). **Exclude WS/WSS hosts** — a
  `wss://` host must not become an HTTP server URL. If no host is known (all relative), omit `servers` and
  note in the top-level description that paths are as-authored.
- **`info`.** `title: "Reconstructed API — run <short id>"`, `version: "0.0.0"`, and a top-level
  `description` preamble stating: reconstructed statically from JavaScript; paths/methods/param-names are
  observed; param/body types and schemas are inferred; responses were not observed; no authentication is
  asserted (headers not captured).
- **`x-recon-confidence`** (per operation): e.g. `{path: certain, methods: observed-only,
  param-names: mixed, param-types: inferred, body: inferred|absent}`.

Worked example (YAML shown for readability; JSON is the default):

```yaml
openapi: 3.0.3
info:
  title: Reconstructed API — run 5ac48ca0
  version: 0.0.0
  description: >-
    Statically reconstructed from JavaScript. Paths, methods, and parameter NAMES are
    observed. Parameter/body TYPES and schemas are INFERRED. Response bodies were not
    observed. No authentication is asserted — request headers are not captured by static analysis.
servers:
  - url: https://api.example.com
    description: Host observed; scheme inferred (defaulted to https).
paths:
  /users/{id}/orders:
    get:
      x-recon-confidence: { path: certain, methods: observed-only, param-types: inferred }
      parameters:
        - { name: id, in: path, required: true, schema: { type: integer },
            description: Name synthesized and type inferred from a templated path segment. }
        - { name: page, in: query, required: false, schema: { type: string },
            description: Name observed; type inferred. }
      responses:
        default: { description: Not observed — static analysis does not capture responses. }
  /location/address/search:
    post:
      requestBody:
        required: false
        content:
          application/json:
            schema:
              type: object
              description: Property names observed statically; types inferred; not exhaustive.
              properties:
                street: { type: string, description: Name observed; type inferred. }
                city:   { type: string, description: Name observed; type inferred. }
      responses:
        default: { description: Not observed. }
x-recon-websocket-endpoints:
  - WSS wss://api.example.com/live
```

## 6. Route & download

`GET /runs/{run_id}/export/openapi?format=json|yaml` — mirrors `spec_router.py:26-56`:
`Depends(get_tenant_id)` (`deps.py:24-34`, `X-Tenant-Id`), the DB read (`reconstruct_run`) off the event
loop via `run_in_threadpool`, `None` → **404**. On success: `build_openapi` → `validate` → `dump_openapi`,
returned with `Content-Type: application/json` (or `application/yaml`) and
`Content-Disposition: attachment; filename="openapi-{run_id}.{json|yaml}"`. Registered in `app.py`
alongside the other routers. This is the app's first file-download route — a small new pattern, noted here.

## 7. Error handling

- Unknown / other-tenant run → **404** (RLS via `reconstruct_run` returning `None`).
- Run with zero endpoint findings → **200** with a valid document, `paths: {}`, and a description note
  that nothing was reconstructed (honest, not an error).
- Bad `format` value → **422**.
- Self-validation is wrapped in a **broad `except Exception`** (mirroring `ingest.py:129-132`) → **500**,
  because `openapi-spec-validator.validate()` can raise a bare `ValueError` — not only
  `OpenAPIValidationError` — on a malformed template (gate Blocker 2). With §5's canonicalization this is
  expected to be unreachable; host-lane tests assert emitted docs always validate.

## 8. Testing (host-lane, pure — no katana/engines)

- `probe/openapi_test.py`: each mapping rule of §5; **every emitted fixture passes
  `openapi_spec_validator.validate`** (the core correctness guarantee). Path canonicalization corpus
  (gate Blocker 1/2): `${user.id}`, `{userId}`, mixed `v${n}`, and an unbalanced `/a/{b` all canonicalize
  to a validating doc; the `${id}` vs `{id}` collision **merges** rather than drops; repeated same-type
  tokens get unique names (`id`, `id2`). Query params (incl. null-`example` omission); body → typed
  `requestBody` when `content_type` set, and jQuery/xhr (None) → `x-recon-body-params` with **no** media
  type asserted; WS/WSS excluded from `paths` but present in `x-recon-websocket-endpoints`; `servers`
  from hosts (scheme/port from `example_url`, WS hosts excluded) and the no-host case; empty-run document;
  JSON + YAML `dump_openapi` round-trip.
- `api/export_router_test.py` (integration): 200 + `Content-Disposition` + a valid body; `format=yaml`;
  unknown run → 404; other-tenant run → 404; bad `format` → 422.

## 9. Out of scope / deferred (fast-follow)

- **Approach B — persisted OpenAPI artifact.** Generate at analyze-finalize, store as a new `openapi`
  blob (`storage.BLOB_KINDS`, `:24-26`), regenerate on rescan — a reproducible/auditable snapshot for
  human review + Burp, symmetric with the shadow slice's persist + reclassify. Deferred as YAGNI (the
  export is deterministic from already-persisted findings); build when a frozen snapshot is wanted.
- **Runtime-evidence enrichment (the future big step).** Ingest Burp/HAR/mitmproxy captures — and
  possibly adopt the real `praetorian-inc/vespasian` — to enrich the spec with observed
  requests/responses (real schemas, probed methods). This route **deliberately relaxes REQ-P1/P2's
  no-active-traffic constraint** and is a conscious future architecture decision, not part of this slice.
- **Swagger 2.0 export**, **OpenAPI 3.1**, **GraphQL SDL / WSDL / gRPC** outputs.
- **Response schemas / richer type inference** (need runtime evidence we do not collect).

## 10. Open items / risks

- **Synthesized path-param names.** The real parameter name is not recoverable from a templated token;
  a consumer sees `id`/`uuid`/`hash`. Mitigated by the per-parameter description + `x-recon-confidence`.
- **Server scheme.** When only a bare host is known, the emitted `servers[].url` defaults to `https://`
  and says so; `example_url` is preferred when it carries a scheme.
- **Body content-type** is asserted **only when observed** (fetch/axios → `application/json`); jQuery/xhr
  bodies (`content_type` None) are surfaced via `x-recon-body-params` with no media type, never guessed.
- **Burp import** is asserted only by producing a valid 3.0.3 document + the validator; an actual Burp
  round-trip is a manual check, not an automated test.

## 11. REQ traceability

| REQ | How this slice touches it |
|---|---|
| Vespasian charter (`Dev Requirements:478`) | Emits the "OpenAPI spec" third of the engine's stated role; recorded as an approved spec extension. |
| REQ-S1 | Tenant-scoped read via `reconstruct_run` / RLS 404. |
| REQ-P1 / REQ-P2 | Respected — pure static serialization of stored findings; no active traffic, no new egress. The deferred runtime path (§9) is where these would consciously change. |
| REQ-D2 | Produces an API artifact — on-demand, not stored (deferred approach B would store it as a blob). |
| — (extension) | OpenAPI export itself: an approved capability beyond the 40 REQ-* IDs, the inverse of spec-ingest. |

## 12. §4 adversarial design gate (2026-07-28)

Opus adversarial reviewer, proof-bound (each objection cited exact code lines or empirical
`openapi-spec-validator==0.9.0` behavior). **Verdict: BUILD WITH CHANGES.** The architecture (pure
serializer + thin validated route, inverse of ingest), the RLS/404 seam, the route-registration order vs
the SPA catch-all (`app.py:29-33` before `_mount_spa` at `:42`), the "no security asserted" honesty claim,
and every `file:line` citation were attacked and held. Two blockers were folded into §5/§7 before this
record:

| # | Finding (proof) | Resolution |
|---|---|---|
| **B1** | §5 mapped only `{id}`/`{uuid}`/`{hash}`, but `normalize.py:182-196` passes every *other* interpolation through verbatim and `extract.py:96-112` (+ `extract_test.py:34-35`) stores `${…}` literally — so real paths carry `${user.id}`, `{userId}`, `v${n}`, unbalanced `{`, each an undeclared template variable that `validate()` rejects (`UnresolvableParameterError`) → self-validation 500 on the common case. | §5 path-parameter rule rewritten: canonicalize **every** interpolation to one balanced `{legalName}` param (+ matching declaration, per-path uniqueness, collision-merge). Invariant: emitted paths always validate. |
| **B2** | `validate()` can raise a bare `ValueError` (not `OpenAPIValidationError`) on a malformed template (e.g. `/a/{b`), so a narrow guard would leak an uncaught 500/traceback. | Brace-balancing folded into B1's canonicalization + a **broad `except Exception`** around self-validation (§7), mirroring `ingest.py:129-132`. |

Non-blocking, folded: never assert `application/json` for unobserved jQuery/xhr bodies → `x-recon-body-params`
(§5); `servers` scheme+port from `example_url` and WS-host exclusion (§5); omit null query `example` (§5).
Noted, not folded: YAML `sort_keys=False, allow_unicode=True` cosmetics; the pre-existing non-UUID
`run_id` behavior → mirror sibling `/runs/{run_id}/*` routes (out of scope). Confirmed sound (attacked,
held): RLS 404 via `tenant_session` GUC → `list_findings` None → `reconstruct_run` None → 404; SPA
catch-all cannot shadow the route; headers/auth genuinely uncaptured (`extract.py:49-59`, `_fetch`
`:475-479`); `default`-only responses, root/operation `x-` extensions, and empty `paths` all validate.
