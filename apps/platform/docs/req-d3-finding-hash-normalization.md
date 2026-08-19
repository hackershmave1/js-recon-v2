# REQ-D3 — finding-hash normalization spec

> The finding-hash identity spec. Load-bearing for REQ-A3 (exactly-once outbox),
> REQ-D3 (finding identity), REQ-D5 (partial-aware diff), and REQ-C2 (honest
> coverage). Requirements: `docs/REQUIREMENTS.md`.
>
> **Status:** design gate PASSED (v1, 2026-07-20). This revision incorporates the
> adversarial design review: host removed from hashed identity (C1), occurrences added
> so merges are never silently dropped (C2), path normalization hardened (H2/H3), secret
> token/provider stabilized (M1/M2), evidence idempotency defined (M3), query dedup
> (L1). `UNIQUE(run_id, finding_hash)` confirmed correct (cross-run uniqueness would
> break D5).
>
> **v2 (2026-08-19, `FINDING_HASH_VERSION = 2`): source path removed from hashed
> identity.** The same logical finding seen in several source files is now ONE finding
> with several occurrences — the C1 treatment (host) applied to path. A version tag is
> hashed into every id, so v2 hashes are disjoint from v1; rollout is **new-runs-only**
> (no backfill — runs analyzed under v1 keep their stored hashes, and an in-place
> out-of-band re-extract onto a v1 run is refused rather than duplicating it). Rationale:
> users saw the same call in 3 bundles as 3 rows; and path-in-identity actually *broke*
> D5 rebuild-survival (a bundle re-chunk that moves a call between files churned its
> hash). Every distinct source path survives as `occurrence.source_path` (REQ-C2 honesty
> unchanged). Design review verdict: PROCEED-WITH-CHANGES — the two required fixes
> (union finding-level `attributes` on merge so no observed auth header is dropped; the
> new-runs-only re-extract guard) landed with this change.

## 1. Why this exists

REQ-D3 (MUST): *"Findings are content-addressed over stable fields only
(identity-version + type + value), excluding volatile per-sighting detail (host,
source path, col/evidence), so a retry — or the same finding in another source file —
yields the same hash. This hash keys the exactly-once outbox write (REQ-A3)."*

- **REQ-A3** — a stage stages findings in one transaction keyed by `finding_hash`;
  partial-commit-then-retry re-emits the *same* hash ⇒ idempotent.
- **REQ-D5** — run N+1 vs N diffs `finding_hash` sets; the hash must survive
  **rebuilds** of the target, or every deploy looks like "all removed + all added."
- **REQ-C2** — coverage/attribution "honesty is a MUST": a normalization collision
  must never silently erase attack surface.

## 2. Model — hard identity vs tracked occurrences

Two layers. The **hard identity** is hashed and stable. **Occurrences** capture the
mutable, per-sighting detail; they are tracked (never dropped) so a merge is visible.

```
Finding (one row per run_id + finding_hash)
├─ HARD IDENTITY  → finding_hash = sha256(canonical {v, type, value})   # v = FINDING_HASH_VERSION
│    v      identity-algorithm version (2) — disjoint hash space per bump
│    type   "endpoint" | "secret" | "param"
│    value  normalized, per-type (§4) — NO host, NO path for endpoints
│
└─ OCCURRENCES (1..N child rows, keyed (finding_hash, occurrence_hash))
     host, source_path, raw_url / raw_value, offset_start/end,
     line, col, evidence/snippet, engine, confidence, verified
```

Locked decisions:
- **Host → occurrence, not hashed** (C1). Endpoint identity is re-resolution-proof:
  REQ-C2's set-base-URL can change host without churning the hash.
- **Source path → occurrence, not hashed** (v2, the C1 treatment applied to path).
  The same finding across bundles is one row + many occurrences; each distinct
  `occurrence.source_path` is preserved (no silent loss), and a bundle re-chunk no
  longer churns D5. `normalize_source_path` (§3) still runs — its output is the
  stored `occurrence.source_path`, just not a hash input.
- **Balanced, entropy-aware templating**; **query keys only**, sorted + de-duped.

## 3. Source-path normalization (all types)

Pick ONE canonical source per finding (raw source-map `sources` entry preferred over a
tool's on-disk mirror), then:

1. If a URL: lowercase scheme + host, drop the scheme, **keep the authority/namespace**
   (webpack adds `[namespace]` precisely to prevent path collisions — do not drop it).
   `webpack://app/src/a.js` → `app/src/a.js`; `https://Cdn.X/static/app.js` →
   `cdn.x/static/app.js`. Handle `webpack:`, `webpack:/`, `webpack://`.
2. Collapse content-hash tokens **by position + entropy**. Split each path segment
   on `.`/`-`/`_`; the **filename stem (first component) and extension (last) are
   never collapsed**, so distinct camelCase files (`Base64Encoder.js`,
   `Utf8Decoder.js`) keep separate identities. A remaining component collapses to
   `{hash}` when it is (a) hex `[0-9a-f]{6,}` containing a digit (so hex-letter words
   like `decade` stay literal), or (b) `[A-Za-z0-9]{8,}` with Shannon entropy ≥ 3.0.
   Catches `[contenthash:6]` and rollup's 8-char base64url; a pure-hex stem
   (`9f8e7d6c.js`) still collapses.
3. Normalize separators to `/`; resolve `./` and `../`; drop a leading `/`.
4. Do **not** lowercase the path body (source names can be case-sensitive).
5. Distinguish absent cases with explicit sentinels: no source map at all →
   `{no-map}`; source map present but `sources` entry is `null` (allowed by ECMA-426)
   → `{null-source}`. Never conflate the two with `""`.

Best-effort caveat: a build-tool switch (webpack→vite) or a `devtoolModuleFilename
Template` change rewrites paths; D5 across such a boundary will over-report churn.
Documented, acceptable.

## 4. Per-type `value` normalization

### 4.1 endpoint
`value` = `METHOD + " " + templated-path [+ "?" + sorted-keys]`. **No host, no scheme.**

- **Method**: uppercased HTTP verb, or `WS` / `WSS` for WebSocket. Always in identity.
- **Path templating (balanced, entropy-aware)** — per segment, first match wins:
  | Segment matches | → |
  |---|---|
  | `^\d+$` (pure integer) | `{id}` |
  | RFC-4122 UUID | `{uuid}` |
  | `^[0-9a-fA-F]{16,}$` | `{hash}` |
  | `^[A-Za-z0-9]{24,}$` (contiguous, no `-`/`_`) **and** has a digit **and** entropy ≥ 4.0 | `{hash}` |
  | otherwise | literal (case preserved) |
  The rule is deliberately conservative — a hyphen/underscore marks word-separator
  structure (a slug), and the digit+entropy gate separates random tokens from long
  identifiers, so `/org/acme-corporation-holdings` and `oauth2callbackhandler` stay
  literal (fixes H1 over-merge). An over-merge silently loses attack surface, so
  ambiguous segments stay literal by design. Pure-numeric API versions (`/api/2/`) still
  collapse to `{id}` — accepted, but now **visible**: the distinct raw paths are
  recorded as occurrences and countable for REQ-C2.
- **Trailing slash** stripped (except root `/`).
- **Query**: parse, **de-dupe** keys, sort ascending, join with `&` (no values); array
  keys canonicalized (`ids[]`, `ids[0]` → `ids`). Empty ⇒ nothing appended.
- **Fragment** dropped. Percent-decode unreserved chars before templating.

Example: `POST https://API.acme.io:443/users/4821/orders/f47ac10b-58cc-4372-a567-0e02b2c3d479?sort=asc&page=2&sort=desc`
→ value `POST /users/{id}/orders/{uuid}?page&sort` (host `api.acme.io` → occurrence).

### 4.2 secret
`value` = `provider + ":" + sha256(normalized-token)`.

- **normalized-token**: the matched secret with surrounding delimiters/quotes stripped
  before hashing (M2 — engines may capture a trailing `"`); the raw match stays on the
  occurrence. The token itself is never stored in the hash input in cleartext.
- **provider**: mapped from the engine's rule id via a table **pinned to the engine
  version** (M1 — Kingfisher has 950+ evolving rules); lowercased slug.
- Path is NOT in identity (v2). The same secret in two files ⇒ one finding, two
  occurrences; twice in one file ⇒ one finding, two occurrences (distinct offsets) —
  never a drop (M1). Cross-asset distinctness is preserved by `asset_url` in the
  occurrence identity, not by path.

### 4.3 param
`value` = `operation + " " + location + ":" + name`.

- **operation**: owning endpoint's `METHOD + " " + templated-path` (no query, no host).
- **location**: `path` | `query` | `header` | `body` | `cookie`.
- **name**: parameter name, case preserved.

## 5. Hash construction

```
tuple = {"v": FINDING_HASH_VERSION, "type": <type>, "value": <value>}
finding_hash = sha256(json.dumps(tuple, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8")).hexdigest()
occurrence_hash = sha256 over the occurrence's identifying volatile fields
                  (raw_url/value, host, source_path, offset_start, offset_end, line, col, asset_url)
```

- `value` is Unicode **NFC-normalized** before serialization, so two builds differing only in composition form don't churn the D5 diff (review LOW-5).
- `v` (`FINDING_HASH_VERSION`) ⇒ a future identity change bumps it, giving a disjoint hash space and a clean new-runs-only rollout (no false "all removed + all added" against older runs).
- Canonical JSON (sorted keys, no whitespace, UTF-8) ⇒ deterministic bytes.
- `type` in the tuple ⇒ no cross-type collisions.
- 64-char lowercase hex.

## 6. Persistence & the A3 / C2 / D5 contracts

Inside the stage's staging transaction (REQ-A3):

```
INSERT INTO finding (run_id, finding_hash, type, value, path, first_stage, …)
  VALUES (…) ON CONFLICT (run_id, finding_hash) DO NOTHING;         -- idempotent
INSERT INTO finding_occurrence (finding_hash, occurrence_hash, host, raw_url, …)
  VALUES (…) ON CONFLICT (finding_hash, occurrence_hash) DO NOTHING;-- append, no loss
```

- **A3**: both inserts idempotent; a retry re-emits identical hashes → no-ops.
- **C2 honesty**: a normalization merge shows up as a finding with >1 occurrence
  bearing distinct `raw_url`s; surfaced alongside the un-attributed-call counter.
- **D5**: diff = set difference of `finding_hash` between two runs. Partial-aware:
  absent in a *partial* run ⇒ `unknown`, never `removed`; only
  `completeness.fetch_ok && analyze_ok` on both runs licenses `removed`.
- **UNIQUE(run_id, finding_hash)** — per-run, NOT global (a finding must recur with
  the same hash across runs for D5 to work).

## 7. Edge cases recorded

- Numeric API versions (`/api/2/`) collapse to `{id}` — visible via occurrences.
- `v1`/`v2` stay literal (letter present).
- Relative vs absolute call to same path → same identity now (host is not hashed).
- GET vs POST same path → different identity (method hashed). Correct.
- WS/WSS handled like HTTP with method `WS`/`WSS`.
- Duplicate/array query keys de-duped and canonicalized; empty keys dropped.
- **Schemeless authority** (`fetch("api.x/users")`, no `//`) can't be told from a
  path by `urlsplit`, so the extractor must resolve calls to absolute or
  root-relative URLs before normalization; a bare `host/path` is treated as a path
  (review LOW-3).

## 8. Test vectors (author before code — TDD)

| type | input | expected `value` | expected `path` |
|---|---|---|---|
| endpoint | `GET`, `https://API.X/users/42?b=1&a=2`, src `webpack://app/src/api.js` | `GET /users/{id}?a&b` | `app/src/api.js` |
| endpoint | rebuild: src `.../app.9f8e7d6c.js` vs `.../app.1a2b3c4d.js` | (n/a) | `…/app.{hash}.js` (equal) |
| endpoint | `/org/acme-corporation-holdings-emea` (low entropy) | `GET /org/acme-corporation-holdings-emea` | — |
| endpoint | `/t/aZ9kQ2mB7xL4wP0rT6uY1eC5` (contiguous, digit, high entropy) | `GET /t/{hash}` | — |
| endpoint | host differs, path same | identical `value` (host → occurrence) | — |
| secret | `sk_live_ABC"` (trailing quote), stripe | `stripe:` + sha256(`sk_live_ABC`) | file |
| secret | same value twice in one file | 1 finding, 2 occurrences | — |
| param | `POST /login`, `token`, `body` | `POST /login body:token` | — |
| dupe | same finding, different col/evidence | identical `finding_hash` | — |

## 9. Build plan

1. `recon/findings/normalize.py` — pure fns: `shannon_entropy`, `normalize_source_path`,
   `template_segment`, `normalize_endpoint`, `normalize_secret_value`, `normalize_param_value`,
   `finding_hash`, `occurrence_hash`. Colocated `normalize_test.py` seeded from §8 (TDD).
2. `recon/db/models.py` + Alembic migration — `finding` (RLS,
   `UNIQUE(run_id, finding_hash)`) + `finding_occurrence` child (RLS,
   `UNIQUE(finding_hash, occurrence_hash)`).
3. Wire into the ANALYZE/CORRELATE staging-transaction outbox (REQ-A3).
