# Enrichment slice — design

Status: DESIGN (pre-§4 gate). Branch: cut `feat/enrichment` off `spike/platform-ingest`.
Date: 2026-08-07. Grounded against the current extractor/export code (file:line below).

## Goal

Make the reconstructed API surface *richer* for the downstream consumers (the LLM
threat-model feed and Burp) without any new active traffic and without weakening the
platform's static-analysis honesty. Three additive enrichments over what the Vespasian
extractor already produces:

- **A · Param risk-tags** — classify already-extracted params by NAME into
  `auth / admin / idor / flag`.
- **B · Header capture** — statically read `headers:` on fetch/axios calls and express
  the auth surface as OpenAPI `securitySchemes` + per-operation `security` (names +
  scheme only; token values are never asserted).
- **C · GraphQL** — locate GraphQL documents, parse them with `graphql-core`, and emit
  each operation as an `x-recon-graphql-operations` annotation in the OpenAPI export.

DROPPED (explicitly out of scope): HTTP-method guessing, regex endpoint harvesting,
±N-char proximity association, Postman export.

## Locked decisions (user, 2026-08-07)

1. **GraphQL = export-only annotation**, not first-class findings. No new `FindingType`,
   no migration, no frontend work. GraphQL ops surface only in the OpenAPI export.
2. **Header capture = yes**, names + schemes only; token values never asserted. The
   export's "no authentication asserted / headers not captured" honesty statement is
   revised to be precise (not deleted).
3. **Risk-tags = query/body params only** in v1 (finding-side). Path-segment `id`→idor
   is a fast-follow (a different code site — the export's synthesized path params).

## No migration

Both A and B ride `Finding.attributes` (JSONB, `models.py:295-297`), which is NOT part
of `finding_hash` identity (`normalize.py:312-325`) and already flows verbatim to the API
(`queries.py:411`, `findings_router.py:42`). C persists a run-level artifact, not findings.
Latest migration is `0010`; this slice adds none. (First-class GraphQL findings — the
rejected option B — would have needed `0011` to relax `ck_finding_type`, `models.py:278`.)

---

## A · Param risk-tags  [S]

**New module** `src/recon/findings/risk_tags.py` + colocated `risk_tags_test.py`, modeled
on the dependency-free rule module `findings/wrappers.py`. One pure function:

```python
def classify_param(name: str) -> tuple[str, ...]:
    """Zero or more risk tags for a param NAME. Sorted, deduped, order-stable."""
```

Taxonomy (case-insensitive; matches on whole tokens / suffixes, NOT naive substring — see
FP guard):

| tag   | matches (name, tokenized on `_`, `-`, camelCase) |
|-------|--------------------------------------------------|
| auth  | token, auth, authorization, apikey, api_key, jwt, bearer, session, sessionid, secret, credential, otp, mfa, password, passwd |
| admin | admin, superuser, root, sudo, privileged, impersonate |
| idor  | a token equal to `id`, or ending `id` (userId, account_id, objectId, uuid, guid) |
| flag  | flag, feature, toggle, enabled, disabled, beta, experimental, a `is`/`has`/`can`/`allow` boolean prefix |

**FP guard (load-bearing):** `idor`/`flag` are the false-positive-prone tags. Match on a
tokenizer, never `"id" in name` (which would tag `width`, `valid`, `candidate`, `video`).
Tokenize the name (split on `_`,`-`, and camelCase boundaries, lowercase) and match a tag
only if a whole token equals/ends-with the pattern. The classifier returns `()` for the
common case (most params are untagged) — a missing tag is honest; an over-eager tag is noise.

**Write site** — `analyze.py:_record_endpoint` (`:497-508`), the shared core also used by
the out-of-band re-extract (`reextract.py:27,90`), so both paths tag with one edit:

```python
tags = risk_tags.classify_param(param.name)
attributes={"location": param.location, "name": param.name,
            **({"risk_tags": list(tags)} if tags else {})}
```

Only add the key when non-empty (keeps `attributes` clean; passthrough surfaces it on
`GET /runs/{id}/findings` with zero router change).

**Export surfacing** — `reconstruct.py` already reads a param's `attributes` at `:149-157`;
carry `risk_tags` onto `QueryParam`/body entries and let `openapi.py._operation_object`
emit an `x-recon-risk` extension aggregating the operation's param tags (mirrors the
existing `x-recon-body-params` at `openapi.py:156`). Advisory metadata, not OpenAPI-schema.

Tests (pure, `extract_test`-style): tokenizer FP cases (`width`/`valid`/`candidate` → no
idor; `userId`/`account_id`/`uuid` → idor); `token`/`authorization` → auth; multi-tag
(`admin_token` → admin+auth); empty for plain names; write-through asserted in
`analyze_test` (a param finding carries `attributes.risk_tags`); export `x-recon-risk` in
`openapi_test`.

---

## B · Header capture  [M]

**Extract** — in `extract.py`, at the fetch options object (`_fetch`, `:497-498`) and the
axios config object (`_axios_from_config`, `:565-574`), read the `headers:` object with the
existing `_object_pairs(options).get("headers")` helper (`:132-150`). Capture only the header
NAMES (object keys) and, when the VALUE is a string literal, its scheme keyword
(`Bearer`/`Basic` prefix). Add a field to `RawEndpoint` (`:52-66`):

```python
headers: tuple[HeaderRef, ...] = ()   # HeaderRef(name: str, scheme: str | None)
```

In-memory only. A dynamic header value (a variable) → `scheme=None` (still record the name).

**Auth allow-list** — only headers that describe an auth surface become security schemes;
everything else (Content-Type, Accept, …) is ignored. Curated set (case-insensitive):
`authorization, authentication, proxy-authorization, x-api-key, api-key, apikey,
x-auth-token, x-access-token, x-amz-security-token`.

**Persist** — the ENDPOINT finding written in `_record_endpoint` carries the auth headers in
its `attributes` (e.g. `attributes["auth"] = [{name, scheme}]`). `attributes` is display-only
extra (`models.py:295`) — not identity — so it never churns `finding_hash`.

**Export** — `reconstruct.py` carries the endpoint's auth headers onto `ReconstructedRequest`
(`:34-45`, read at `:166`); `openapi.py` gains:
- `build_openapi` (`:261-299`): a `components.securitySchemes` object (deduped across the
  run) — currently no `components` key exists at all.
- `_operation_object` (`:138-162`): a `security: [{<schemeName>: []}]` list for the op's
  captured auth headers.

Scheme mapping (OpenAPI 3.0.3, all `validate()`-safe — the doc is validated at
`openapi.py:298`):
- `Authorization` + `Bearer` literal → `{type: http, scheme: bearer}`
- `Authorization` + `Basic` literal → `{type: http, scheme: basic}`
- any other auth-list header, or `Authorization` with an unknown value →
  `{type: apiKey, in: header, name: <HeaderName>}` (honest: "a credential rides this header",
  no scheme asserted).

**Honesty revision (required)** — update the docstring `openapi.py:5-8` and
`_INFO_DESCRIPTION` `:165-170`. Current text literally says *"No authentication is asserted —
request headers are not captured by static analysis."* New text: request-header SHAPES are
captured (names + scheme keyword) and expressed as `securitySchemes`/`security`; credential
VALUES are never captured or asserted (they are supplied at runtime).

Tests: `extract_test` (a fetch/axios call with `headers:{Authorization:"Bearer "+t}` →
`RawEndpoint.headers` has `(Authorization, bearer)`; a dynamic value → scheme None; a
non-auth header is not captured as auth); `openapi_test` (securitySchemes emitted; per-op
security; apiKey-in-header fallback; the doc still `validate()`s).

---

## C · GraphQL  [M] — export-only

**New dependency** `graphql-core` (add to `pyproject.toml:10-41`, then `uv lock`;
precedent: `openapi-spec-validator`). Parsing only — no network, no schema.

**New module** `src/recon/findings/graphql_ops.py` + `graphql_ops_test.py`:

```python
@dataclass(frozen=True)
class GraphQLOperation:
    op_type: str        # "query" | "mutation" | "subscription"
    name: str | None    # operation name, if any
    fields: tuple[str, ...]   # top-level selection fields
```

- `extract_documents(source, parser)` — walk the JS AST for GraphQL sources:
  - a `call_expression` whose function identifier is `gql`/`graphql`/`graphql-tag` and whose
    `arguments` field is a `template_string` node (NOTE: this tree-sitter grammar has NO
    `tagged_template_expression` node — `gql\`...\`` parses as `call_expression` +
    `template_string`; read `child_by_field_name("arguments")` and branch on
    `type == "template_string"`, do NOT reuse `_args()` which returns the template fragments);
  - an object-literal `pair` whose key is `query`/`mutation` and whose value is a string or
    template (reachable via `_object_pairs` + `_string_value`, `:132-150`/`:121-123`).
- `parse_operations(graphql_source) -> tuple[GraphQLOperation, ...]` — `graphql.parse()` the
  document; on a `GraphQLSyntaxError` return `()` (a malformed/partial template is a SOFT
  MISS — never fail analyze; mirror the source-map soft-miss invariant).

**Persist (export-only, no findings)** — GraphQL ops are a run-level artifact, NOT findings,
so they never pollute the HTTP-endpoints UI:
- analyze stores a `graphql` blob (`storage.put_blob(tenant, run, "graphql", json)`) and
  records an `analyze.graphql` event (mirrors the discover assets-manifest pattern in
  `discover/crawl.py:98-110`). Empty → skip (no blob, no event).
- `export_router` loads the latest `graphql` artifact for the run and passes the ops into
  `openapi.build_openapi`, which emits a root `x-recon-graphql-operations` extension
  (alongside `x-recon-websocket-endpoints`, `openapi.py:294`): a list of
  `{op_type, name, fields, source_path}`. GraphQL ops are NEVER emitted as OpenAPI `paths`
  (a GraphQL op is not an HTTP path) and NEVER counted as unattributed HTTP endpoints.

Tests: `graphql_ops_test` (gql`` tagged form; `graphql()` call form; `{query:`...`}` body
key; multi-op document; malformed → `()`); an `analyze`/export integration case that the
extension appears and no spurious ENDPOINT/param finding is created for a gql`` literal.

---

## Invariants / traps

- **T1 (idor/flag FP):** tokenize, never substring — pinned by `risk_tags_test` FP cases.
- **T2 (GraphQL soft-miss):** a `GraphQLSyntaxError` returns `()`; analyze never fails on a
  partial/interpolated GraphQL template.
- **T3 (honesty):** capturing header names/schemes REQUIRES updating both honesty strings in
  `openapi.py`; leaving them stale would be a false claim. No token VALUE is ever emitted.
- **T4 (validate):** every emitted `securitySchemes`/`security`/extension must keep the
  exported doc passing `openapi.py`'s `validate()` (`:298`) — covered by `openapi_test`.
- **T5 (shared core):** the risk-tag + header writes live in `_record_endpoint`, which
  `reextract.py` also drives — a wrapper re-extract must produce identical `attributes`
  (no finding_hash drift, since attributes is non-identity).
- **T6 (no new active traffic):** everything is static; GraphQL parsing is offline; header
  capture reads literals only.

## Fast-follows (out of scope this slice)

- Path-segment `id`/`uuid` → idor tagging on the export's synthesized path params
  (`openapi.py:_canonicalize_path` `:66-90`).
- First-class GraphQL findings (new `FindingType.GRAPHQL` + migration 0011 + FE) if GraphQL
  ops should appear in the workspace UI/D5 diff.
- Cookie-based auth as a securityScheme (noisy; deferred).

## Acceptance

- New host-lane unit tests green (risk_tags, graphql_ops, extract header cases) + updated
  analyze/reconstruct/openapi tests.
- The OpenAPI export for a sample bundle carries `securitySchemes`, per-op `security`,
  `x-recon-risk`, and `x-recon-graphql-operations`, and still `validate()`s.
- Both §4 gates (adversarial design review of THIS doc; higher-model code review of the diff).

## §4 design-gate verdict (2026-08-07): BUILD WITH CHANGES

Adversarial design review ran against the live code + runtime (graphql-core, openapi-spec-validator 0.9.0, tree-sitter). Architecture CONFIRMED sound (attributes-as-non-identity; export-only run artifact for GraphQL; shared `_record_endpoint`; validator-safe security/extension output). Apply these BEFORE building — the enrichment slice is currently PARKED behind the repo-hardening slice (user picked harden-first, 2026-08-07); resume here.

Must-fix:
- **M1 (idor FP — load-bearing).** The spec's idor "ends-with `id`" clause tags `valid`, `grid`, `android`, `solid`, `rapid`, `hybrid`, `liquid` → contradicts the spec's own `valid`→no-idor test. Replace with: idor = a token **== `id`** (tokenizer already yields `userId→[user,id]`, `account_id→[account,id]` correctly, and rejects `valid`/`grid`) **plus** an explicit whole-token allow-list `{uuid, guid}`. Pin `valid`/`grid`/`android` as negative tests.
- **M2 (GraphQL call form).** Two AST shapes, not one: `gql\`…\`` → `call_expression` whose `arguments` FIELD is a `template_string` (use `child_by_field_name("arguments")`); `graphql(\`…\`)` → `call_expression` whose `arguments` is a normal `arguments` node holding the template (use `_args(call)[0]`). Handle BOTH (the spec's "never use `_args()`" drops the call form, one of its own test cases).
- **M3 (blob kind).** Add `"graphql"` to `storage.BLOB_KINDS` (`storage.py:24-26`) or `put_blob` raises `ValueError`.
- **M4 (multi-asset GraphQL).** A crawl run's `_analyze_blob` runs once per asset, each emitting its own `analyze.graphql` event; "read latest" drops every asset's ops but the last. Export must UNION all `analyze.graphql` events for the run (or aggregate to one run-level artifact). Single-upload runs unaffected.
- **M5 (axios headers).** `headers:` at `_fetch` + `_axios_from_config` misses `axios.get/post(url, …, {headers})`, which dispatches through `_axios_member` (`extract.py:533-557`; config = `args[1]` GET-family / `args[2]` POST-family). Capture there too or explicitly de-scope.

Should-fix:
- **S1** high-FP auth/admin tags: `token`/`session` tag `nextToken`/`pageToken`/`sessionStorage`; `root` tags `rootId`/`rootMargin`. Trim these tokens or document as accepted noise.
- **S2** filter GraphQL selections to `FieldNode` before `.name.value` (an inline fragment has no `.name` → `AttributeError`).
- **S3** `validate()` does NOT enforce a per-op `security` name exists in `components` (a dangling ref passes) — pin scheme-name consistency in a test.
- **S4** honesty strings (T3) — both `openapi.py:5-8` and `_INFO_DESCRIPTION:165-170` must be revised, else a false claim ships.
- **S5** don't thread risk onto `body_params` (a `tuple[str,...]`, would ripple the body-merge); carry a single operation-level `name→tags` risk map on `ReconstructedRequest`, emit as `x-recon-risk`. Matches the [S] sizing.
- **Note** `on_conflict_do_nothing` (`store.py:101`) means enrichment attributes are NOT backfilled onto pre-existing findings — they appear on fresh runs / findings created after the change. State this.
