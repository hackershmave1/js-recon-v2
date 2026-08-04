# Shadow-API false positives in client-side JavaScript extraction

> **Status: OPEN CONCERN — to address next session (user-raised 2026-07-27).**
> This is a portable write-up of a real accuracy problem for any tool that extracts
> the API calls a front-end makes and diffs them against an OpenAPI/Swagger spec to
> find "shadow" (undocumented) endpoints. The generic write-up is below; first, how
> it maps to **this** codebase.

## How this maps to our platform (read this first)

Our platform statically reconstructs a backend API from a target's JavaScript
(Vespasian extractor → `src/recon/findings/extract.py` → endpoint/param findings,
normalized in `src/recon/findings/normalize.py`). Two touchpoints make this concern
directly relevant:

1. **Base-URL resolution is already known debt.** The REQ-C2 SHOULDs —
   *cross-file base-URL resolution* and *wrapper-teaching* (map a call shape) — are
   deferred with **no data model yet**; relative endpoints currently render against a
   `{{base_url}}` placeholder (see `docs/slice2-deferred-debt.md`, slice-3a section,
   "Cross-file base-URL resolution + wrapper-teaching (C2 SHOULDs)"). That gap is
   exactly root cause #1 below (per-service base URLs like `axios.create({ baseURL })`).
2. **Shadow detection is not built yet.** We do not yet diff extracted client URLs
   against a spec. **OpenAPI/Swagger export** is deferred (the "other half of complete
   the first chunk", `docs/slice2-deferred-debt.md` + `slice-y-progress` memory). If a
   shadow-endpoint feature is built on top of OpenAPI export, it must be designed with
   the preventions below from day one — the false-positive trap is the default outcome
   otherwise.

**Open questions for the design session:** Is Vespasian AST-based or regex-based
(prevention #5 hinges on this)? Do we resolve `axios.create`/wrapper bases today, or
only per-call literals? Should shadow detection be static-only, or gated on the
katana-crawl / runtime evidence we now have (prevention #6)? The natural home is a
new slice: brainstorm → spec → §4 gates → plan → build.

---

## The problem in one sentence

A shadow-endpoint detector flags paths the client calls but the spec doesn't
document. It produces **false positives whenever the path it recovers from the
JavaScript is not the same string the server actually receives** — most often
because the client prepends a base URL the extractor never saw.

## How shadow detection works (and where it breaks)

The detector builds two sets and subtracts:

- **Documented** = every path in the spec.
- **Called** = every URL it can pull out of the client JS (`fetch`, `axios`, XHR, …).
- **Shadow** = `called − documented`.

The diff is only as correct as the *called* set. Static extraction rarely recovers
the **full, server-received path** — it recovers a *fragment*. When a fragment
doesn't textually equal a spec path, it gets mislabeled shadow, even though the real
request is fully documented.

## Root causes (most to least common)

**1. Per-service base URLs — the big one.**
Clients configure a base per API module:

```js
const location = axios.create({ baseURL: '/location' });
location.post('/address/search', body);   // real path: /location/address/search
```

A per-call scan sees only `/address/search`; the spec documents
`/location/address/search`; no match → false shadow. A single `axios.create` can
taint dozens of endpoints at once.

**2. Global prefix / version base.**
`const API = 'https://host/api/v3'; fetch(`${API}/pets`)` → real `/api/v3/pets`. If
the spec's `servers` basePath is `/api/v3` and you compare against bare `/pets` (or
the reverse), the prefix mismatches on one side.

**3. Concatenation and variables.**
`fetch(base + '/' + resource + '/' + id)` — `resource`/`id` aren't statically known,
so you extract a partial path or a template full of holes.

**4. Placeholder-syntax mismatch.**
Client `/pets/${id}` vs spec `/pets/{petId}`. Different placeholder styles; a naive
string compare treats them as different paths.

**5. Cosmetic mismatches.** Trailing slash, query string, `%2F` encoding, host case,
`http` vs `https`.

**6. Verb mismatch.** Client `GET /x`; the spec documents only `POST /x`.

## How to prevent it

**1. Canonicalize both sides before diffing.**
Strip query/fragment/trailing slash; lowercase host; collapse path parameters to a
single wildcard on *both* sides (`/pets/{petId}` and `/pets/${id}` → `/pets/*`);
collapse purely numeric/uuid segments to `*`. Compare canonical forms, not raw
strings.

**2. Resolve base URLs — the fix for cause #1.**
Associate each call site with the client/instance it goes through and prepend that
instance's base before diffing:
- Track `axios.create({ baseURL })`, `axios.defaults.baseURL`, and any hand-rolled
  `fetch` wrapper's base.
- Map the variable/instance → its base, then join `base + relativePath`.
This needs light data-flow (bind the instance to its config), which a stateless
per-call regex cannot do — see prevention #5.

**3. Match by suffix / containment, not just equality.**
If a recovered client path is a suffix of some documented path (or vice-versa),
treat it as *probably documented*, not shadow — e.g. `/address/search` is a suffix
of `/location/address/search`. Cheap, and it kills most base-URL FPs. Caveat: suffix
matching can mask a genuinely different endpoint that happens to share a tail, so
use it to move items into a **"likely-documented, verify"** bucket, not to silently
drop them.

**4. Three buckets, never a binary.**
Classify each called URL as **documented**, **shadow (high-confidence)**, or
**unresolved**. Only call something *shadow* when you recovered a **complete,
statically-certain path** (a string literal, fully qualified) and canonicalization
still finds no spec match. Anything partial / interpolated / concatenated goes to
**unresolved → needs review**, never to shadow. This one rule removes most noise,
because the majority of FPs are partial paths being treated as if they were
complete.

**5. Parse, don't grep.**
An AST (tree-sitter, Babel, acorn) lets you resolve the `baseURL` binding, follow the
wrapper function, and reconstruct the full path. Regex is a floor, and it is exactly
why partial paths leak through. If shadow accuracy matters, an AST pass is the
highest-leverage upgrade.

**6. Prefer runtime evidence when you can get it.**
The most reliable shadow detection diffs the spec against **observed traffic**
(proxy / gateway / access logs, or a crawl of the running app), not statically
extracted client URLs. Static client extraction is a *lower bound* and inherently
FP-prone — say so in the output.

## A 30-second self-audit

Before trusting a shadow list, ask: **do most "shadow" paths appear as a suffix of
some documented path?** If yes, your base-URL resolution is missing (cause #1) and
the list is mostly false positives. That single check is how a real run was caught —
the detector reported `/address/search` as shadow while the spec plainly documented
`/location/address/search`.

## TL;DR

Shadow FPs come from comparing a **partial** client path against a **full** spec
path. Fix, in order of leverage:

1. Never label a partial/interpolated URL as shadow — bucket it as *unresolved*.
2. Resolve per-instance base URLs (`axios.create({ baseURL })`, wrapper bases).
3. Canonicalize + wildcard path params on both sides.
4. Suffix-match as a safety net (into a "verify" bucket).
5. Move to an AST if it still matters.
6. Prefer real traffic over static extraction when available.
