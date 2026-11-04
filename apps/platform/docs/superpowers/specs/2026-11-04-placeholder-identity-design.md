# Placeholder identity — path parameters by position, names as evidence

Status: DESIGN, adversarially reviewed (2026-11-04, SOUND WITH CHANGES — revisions folded in
below) · QA finding #5 (recon-range scored run) · identity v2 → v3

## 1. Problem (what users see)

A template-literal URL keeps its `${...}` substitution verbatim in the endpoint `value`, and
`value` is the finding identity (`finding_hash = sha256(v, type, value)`). The variable NAME is
not a property of the API — it is whatever the developer or the minifier called it:

| Source | Endpoint value today |
|---|---|
| `fetch(\`${API}/api/v1/users/${userId}\`)` (source map) | `GET /api/v1/users/${userId}` |
| same call, minified (`${e}`) | `GET /api/v1/users/${e}` |
| `fetch("/api/v1/users/" + 42)` / captured `/api/v1/users/42` | `GET /api/v1/users/{id}` |

Real data (local DB, 2026-11-04): `GET /api/v1/users/${userId}` and `GET /api/v1/users/${e}`
are two findings of one endpoint. So: two rows per endpoint, run-to-run diff churn on a
rename, no path-param findings (no IDOR tag on `userId`), and a dynamic query
`?${new URLSearchParams(r)}` becomes a fake query param attached to a mis-split operation.

## 2. Decision

**Identity by position, names as evidence** — narrowed after review:

1. A WHOLE path segment that is one clean `${…}` placeholder, AFTER ≥1 static segment, becomes
   `{id}`. Leading placeholders (`/${apiBase}/users`, an unresolved base) and partial segments
   (`api${PATH}`) stay VERBATIM — rewriting them broke the spec "partial" gate
   (`spec/classify.py` `is_partial`), capture correlation (`correlate/match.py` host-templated
   absorb), the spec base-strip, and would merge different backends (`${AUTH_URL}/login` vs
   `${API_URL}/login`).
2. Confirmed-lane endpoints get **path-param findings** keyed by position:
   `"<operation> path:<n>"`, `n` = 1-based ordinal among the path's placeholder-ish segments
   (`{id}`, `{uuid}`, `{hash}` — the same set OpenAPI already emits as path params). The
   suspected lane gets the positional token but NO param findings (existing invariant:
   suspected-lane params would be fabricated, `analyze.py` `_record_suspected_endpoint`).
3. Names are evidence, extracted at EXTRACTION time (tree-sitter), not regex in `normalize`:
   identifier / member-chain leaf, unwrapping `encodeURIComponent`/`encodeURI`/`String`/
   `Number`. `${t}` → `t`; `${item.DSStoragePath}` → `DSStoragePath`; a call → none.
4. **Balanced masking** of `${…}` (incl. nested templates) before `urlsplit`/`parse_qsl`/
   `split("/")` in both `normalize` and `_jsast._query_params`, so a `?` or `/` inside a
   substitution can't split the URL. A placeholder query part is dropped from query keys
   (endpoint attr `dynamic_query: true`).
5. `FINDING_HASH_VERSION` 2 → 3 **plus a migration** that re-keys session-level state.

## 3. Data written

- `RawEndpoint.placeholder_names: tuple[str | None, ...]` — one per `${…}` in the URL, in order.
- Endpoint attrs: `path_params: [{"position": n, "names": [...], "integer_only": bool}]`,
  `dynamic_query`.
- Param finding per position: attrs `{location: "path", position, names, name, risk_tags}`.
- `store._merge_attributes`: explicit union for `names` / `path_params` (by position, AND for
  `integer_only`), then RECOMPUTE `name` and `risk_tags` from merged names (catch-all is
  first-non-null, which would freeze a minified `e`).
- Display name: readable beats mangled (`_is_readable_name`), then longest, then
  alphabetical; none readable → `id`. **Risk tags come from observed readable names only** —
  never from the `id` fallback (`classify_param("id")` = idor would tag every minified `${e}`).

## 4. Read side

- One display helper (`{id}` → observed name by position) used by the findings UI, search,
  CSV export, diff, and the threat-model endpoint list (`threat_model/service.py`), so the
  IDOR hint `userId` stays visible everywhere.
- `reconstruct`: carry `path_params` (and merge them in `_merge`); `openapi._canonicalize_path`
  names the param from evidence and emits `type: integer` only when `integer_only`.

## 5. Rollout — v3 with migration (user decision 2026-11-04)

- Alembic migration re-keys `finding_triage`, `finding_spec_status`,
  `session_base_url.finding_hashes`, `threat.citations`: for each stored hash, find a
  `finding` row (type, value) → v3 value = rewrite(v2 value) → v3 hash. Collisions (two v2
  endpoints merging): newest `updated_at` wins. Pure rewrite lives in `normalize` (stdlib).
- Run-to-run diff: flag `identity_changed` when the two runs hash under different versions
  (`finding_hash(type, value) != stored`, the `reextract.py` trick), so the UI explains the
  churn instead of showing fake removed/added rows.
- D3 spec §4.1/§4.3 updated in the same PR.

## 6. Test vectors (write first)

1. `${userId}` and `${e}` at the same position → same endpoint hash; one param with names
   `[e, userId]`, display `userId`, risk tag `idor`.
2. `${e}` alone → param display `id`, NO risk tags.
3. `/users/42` + `/users/${userId}` → one endpoint; OpenAPI `userId`, `type: string`.
4. `/users/42` alone → OpenAPI `id`, `type: integer` (no regression).
5. `/${apiBase}/users/${slug}` → `GET /${apiBase}/users/{id}`; one path param.
6. `/api/items/${id}${q ? \`?${q}\` : ""}` → no split inside the substitution.
7. `?${new URLSearchParams(r)}` → no query param; `dynamic_query`.
8. Merge order independence (minified first vs mapped first).
9. Migration: a dismissed v2 triage on `${userId}` endpoint carries to the v3 hash.
