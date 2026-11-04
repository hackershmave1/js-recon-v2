# Minified HTTP clients — evidence rules, not names

Status: BUILT (2026-11-04) · QA finding #4 (recon-range scored run, no-map builds)

> **Adversarial review (SOUND WITH CHANGES, 1 BLOCKER + 4 HIGH) — what changed from the draft
> below:** (1) a member-chain factory (`t.A.create` -> `t.A`) is NOT claimed: webpack aliases are
> per-module and a sibling module reusing `t` produced confirmed FPs; per-module identity is DEBT
> D80 (user chose the safe core). (2) An inferred instance the bundle shows behaving like a
> collection (`.set`/`.has`) is dropped (Ember `E.create({baseURL})`); `setdefault` so
> `b=a.create()` keeps `a`'s base. (3) R2 is PARAMETER SUBSTITUTION over the wrapper's RETURN PATH
> only (linear cost; covers base prepending `fetch(s+e)`, positional `method: t||"GET"`, body
> `n?JSON.stringify(n):void 0`, and the `p.url`/`p.method`/`p.data` config wrapper uniformly).
> (4) The wrapper's own `fetch(p)` row is KEPT (an exported wrapper's only signal; REQ-C2).
> (5) Callable instances (`api("/x")`) dispatch too; provenance is `inferred_wrapper`, not the
> taught `wrapper`; the wrapper check runs before `.open`. Class methods deferred.
>
> **Code review (REQUEST CHANGES -> fixed):** a block-bodied wrapper must be ONLY
> `{ return fetch(...) }` (a prior `e = "/api" + e` would confirm a wrong URL); options bound
> through a parameter / spread / shorthand are read from the call site, and options or a method
> that can't be read make the call UNRESOLVED (never a defaulted GET); the unresolved skeleton and
> location mark use the argument the URL actually reads; class and import bindings now poison;
> an inferred (non-`axios.create`) client call needs a path-shaped first argument; the wrapper's
> auth headers carry over; computed keys are never decoded. A 2,625-file main-vs-HEAD corpus
> diff (2,598 real library files) adds confirmed endpoints ONLY in the recon-range chunks.
>
> **Coverage note:** the wrapper's own `fetch(p)` stays an unattributed row (it is a real
> dynamic sink), and each call whose argument can't be resolved adds one more — so a file's
> `unattributed` count can rise while confirmed recall rises too (example 02: 2 -> 7).

## 1. Problem (what users see)

On the no-source-map builds the extractor finds **nothing** in two chunks that make five real
API calls. Exact minified shapes (recon-range `dist/*-nomap`, built from `src/api/inventory.js`
and `src/api/social.js`):

```js
// vite-nomap inventory chunk (bundled axios; webpack: `const s=t.A.create(...)`, `t.A.delete`)
const T=zt(le), wt=T.create({baseURL:"/api/v2"});
async function As(){await wt.get("/inventory"),await wt.post("/checkout",{token:"t"}),
                    await T.delete("/api/v1/session")}
// social chunk (a jQuery-like shim)
const t={getJSON:a=>fetch(a).then(e=>e.json()),
         ajax:a=>fetch(a.url,{method:a.method,body:JSON.stringify(a.data)})};
t.getJSON("/api/v1/config"); t.ajax({url:"/api/v1/feedback",method:"POST",data:{msg:"hi"}});
```

| Call | Today | Why |
|---|---|---|
| `wt.get("/inventory")`, `wt.post("/checkout")` | nothing | instances are only recognized for `axios.create` (`_base_env._is_axios_create` requires the object to be named `axios`) |
| `T.delete("/api/v1/session")` | nothing | `T` is bundled axios under a mangled name; a mangled receiver fails the name gate (`_is_http_client_name`) |
| `t.getJSON(...)`, `t.ajax({...})` | only the wrapper's own `fetch(a)` → unresolved `EXPR`/`:url` | the URL is the wrapper's parameter; the call sites are never connected to it |

Principle (from #163): **never decide by name.** A mangled `wt` and a readable `api` are the
same evidence problem; what the bundle itself DOES with the value decides.

## 2. Rule R1 — a `.create({baseURL})` result is a client (and so is its factory)

During the existing base-env walk (no extra pass), a binding
`X = <F>.create(<object literal with a baseURL key>)`, where `<F>` is any identifier or
member chain other than `Object`/`Reflect`:

- `X` becomes an instance (`env.instances[X] = <baseURL literal or None>`); calls
  `X.get/post/put/delete/patch/head/request(...)` go through the existing axios-member path,
  with the base join (`/api/v2` + `/inventory`).
- `<F>` becomes an instance with an unknown base (`env.instances[F] = None`): an object
  exposing `.create({baseURL})` is the client namespace itself (axios, ofetch, apisauce), so
  `T.delete("/api/v1/session")` is a call on the default client.
- `this.http = F.create({baseURL})` marks the receiver as HTTP evidence (generalizes the
  `axios.create` case in `_receivers._mark_created`).

Evidence: `baseURL` is a client-configuration key (the axios config contract). It is the
config SHAPE, not a variable name, that decides. The existing poison set (a name bound more
than once) still applies, so a reused minified name is never resolved.

## 3. Rule R2 — one-hop wrapper inference (dataflow, not names)

A function value whose FIRST parameter `p` flows, unchanged, into the URL argument of a sink
the extractor already recognizes (`fetch(p, …)`; v1 = fetch only) is a wrapper. Forms:

| Definition | Callee key | Call site recognized |
|---|---|---|
| `const t={k:p=>fetch(p)}` / `{k(p){return fetch(p)}}` / `{k:function(p){…}}` | `t.k` | `t.k("/x")` |
| `function w(p){return fetch(p)}` / `const w=p=>fetch(p)` | `w` | `w("/x")` |
| config wrapper: `fetch(p.url, {method: p.method, body: JSON.stringify(p.data)})` | same | `t.k({url, method, data})` |

- The call site becomes a CONFIRMED `endpoint` (kind `fetch`, attribute `wrapper: <key>` as
  provenance). Method: the wrapper's literal `method` option, else (config wrapper) the call's
  `method` key, else GET. Body params: the call's `data` keys when the wrapper's body is
  `JSON.stringify(p.data)`.
- The wrapper's OWN `fetch(p)` stops being reported as an unresolved `EXPR` sink when it was
  recognized as a wrapper (its call sites carry the real URLs) — fewer junk rows.
- Bounds: only a function whose body span is ≤ 2 KB (a wrapper is small) is inspected;
  `p` must not be reassigned in the body; one hop only. A poisoned callee name is skipped.

Out of scope (stay known blind spots): a factory call receiver (`makeClient().get(...)`,
recon-range `bs-wrapper`), a function returning a URL (`pickUrl()`), two-hop chains, class
methods (`this.request(url)` — listed for review: worth including?).

## 4. Expected result on recon-range

vite-nomap and webpack-nomap: +5 endpoints (`GET /api/v2/inventory`, `POST /api/v2/checkout`,
`DELETE /api/v1/session`, `GET /api/v1/config`, `POST /api/v1/feedback`). Together with #169
(literal `+` folding), both no-map builds should PASS the answer key.

## 5. Tests (write first)

1. R1 on both minified shapes (vite `T.create`, webpack `t.A.create`), incl. base join and the
   factory's own `.delete`.
2. R1 negative: `Object.create({baseURL:...})`; `x.create({name: "a"})` (no baseURL) stays
   unknown; a poisoned instance name is not resolved.
3. R2: object-literal arrow, method shorthand, function declaration, config wrapper with
   method + body; the wrapper's own `fetch(p)` no longer unresolved.
4. R2 negative: `p` reassigned; `fetch(p + "/x")` (not the param unchanged — v1 skips);
   body over 2 KB; a function whose param goes elsewhere.
5. Real bundles: the four recon-range no-map files yield the five endpoints.
