# js-api-recon fixture corpus — expected findings

Field model used throughout (replaces the flat 5-value `type` enum):

| Field | Values |
|---|---|
| `resolution` | `absolute` (host+path, curlable) · `relative` (path only) · `dynamic` (URL computed at runtime) |
| `at_sink` | `true` if found as the URL argument of a request sink, `false` if harvested as a loose string |
| `scope` | `first_party` · `third_party` · `unknown` |
| `kind` | `api` · `page` · `asset` · `noise` |
| `base_source` | how the host was recovered: `literal` · `const_fold` · `axios_instance` · `interceptor` · `option_baseurl` · `env` · `none` |
| `bucket` | derived view: **PROBE** · **NEEDS_HOST** · **NEEDS_RUNTIME** · **DROP** |

`bucket` is computed, not stored:

```
absolute + first_party           -> PROBE
absolute + third_party           -> PROBE (deprioritised / separate list)
relative                         -> NEEDS_HOST
dynamic                          -> NEEDS_RUNTIME
kind == noise                    -> DROP
```

---

## 01 — Vite + React, URL constants module

Your Accenture example. The base is a string literal, so const-folding resolves every derived URL.

| Finding | resolution | at_sink | scope | kind | base_source | bucket |
|---|---|---|---|---|---|---|
| `https://apigatewayazeu-dev.accenture.com/idvs/mfe/dev/v1.0/AssignedQueue` (GET) | absolute | true | first | api | const_fold | PROBE |
| `.../GDPR` (PUT) | absolute | true | first | api | const_fold | PROBE |
| `.../health` (HEAD) | absolute | true | first | api | const_fold | PROBE |
| `.../AgenticAI/GetFilteredDocumentDetails` (POST) | absolute | true | first | api | const_fold | PROBE |
| `.../DocumentDetailsPagination`, `.../Authentication`, `.../Authentication/AIG`, `.../FieldHistory` | absolute | **false** | first | api | const_fold | PROBE |
| `https://legacy-idvs.accenture.com/api/Document/Upload` | absolute | false | first | api | const_fold (fallback branch) | PROBE |

Notes worth encoding:

- The declared-but-never-called constants are the whole point of static recon. They are `at_sink: false` and still high value — `at_sink` must not gate reporting, only rank.
- `getScopeConfigurationURL` ends in `?ClientId=`. Emit the **path** as the endpoint and the dangling param name as a separate `params: ["ClientId"]` field. Don't ship `?ClientId=` as part of the URL.
- `LegacyApi` has two possible values (`import.meta.env.VITE_LEGACY_API || "..."`). Take the literal fallback, mark `base_source: const_fold` with a `branch_fallback` flag. Don't drop it as ambiguous.

## 02 — Webpack 5 minified chunk

| Finding | resolution | at_sink | scope | kind | base_source | bucket |
|---|---|---|---|---|---|---|
| `https://api.acme.io/v2/orders` (GET) | absolute | true | first | api | const_fold (`o+i`) | PROBE |
| `https://api.acme.io/v2/orders/{id}` (GET) | absolute | true | first | api | const_fold | PROBE |
| `https://api.acme.io/v2/orders/{id}/cancel` (POST) | absolute | true | first | api | const_fold | PROBE |
| `https://api.acme.io/v2/users/me` | absolute | true | first | api | const_fold | PROBE |
| `https://api.acme.io/v2/internal/orders/bulk-export` (POST) | absolute | true | first | api | const_fold | PROBE |
| `https://api.acme.io/v2/search?q=` | absolute | true | first | api | const_fold | PROBE |
| `https://cdn.acme.io/static/build/locales/en-US.json` | absolute | true | first | asset | webpack `n.p` | PROBE (low) |
| `a.get(e.settingsUrl, t)` | dynamic | true | unknown | api | none | NEEDS_RUNTIME |

The interesting mechanic: `c()` is a **wrapper function**, not a sink. Every call site passes a path fragment to `c`, which concatenates onto `s` and calls `fetch`. If the analyzer only looks at literal fetch arguments it reports one finding (`s+e`) instead of six. Needs one level of intra-file call-graph propagation: *if a function's parameter flows into a sink's URL position, treat call sites of that function as sinks.*

Also: `pk_live_9f3a2c8811d4` in a header is a secret finding, not an endpoint finding. Different output stream.

## 03 — Next.js App Router chunk

| Finding | resolution | at_sink | scope | kind | base_source | bucket |
|---|---|---|---|---|---|---|
| `/api/documents?status=` (GET) | relative | true | first | api | none | NEEDS_HOST → PROBE (same-origin) |
| `/api/documents/{id}` (PATCH) | relative | true | first | api | none | NEEDS_HOST |
| `/api/revalidate?tag=queue` (POST) | relative | true | first | api | none | NEEDS_HOST |
| `process.env.NEXT_PUBLIC_API_BASE + "/graph/summary"` | dynamic | true | unknown | api | env (unresolved in chunk) | NEEDS_RUNTIME |
| `/queue/{queueId}/review`, `/admin/users`, `/legal/gdpr` | relative | false | first | page | none | NEEDS_HOST |
| `/_next/image?url=&w=&q=` | relative | false | first | asset | none | DROP-ish |

Two rules this file argues for:

- **Same-origin promotion.** If you know the origin the bundle was served from, `relative` findings are promotable to `absolute` with `base_source: page_origin`. Keep the original as `relative` in the data and let the promotion be a view. That way a bundle pulled from a CDN and served on five hosts doesn't produce five wrong absolutes.
- `__BUILD_MANIFEST` is a free route table. Parse it as a route source, not as string harvesting — it gives you `/queue/[id]/review` with the parameter marked, which is better than the `router.push` template.

## 04 — Angular environment + interceptor

| Finding | resolution | at_sink | scope | kind | base_source | bucket |
|---|---|---|---|---|---|---|
| `https://idvs-api.acme.corp/api/DocumentTab?DocumentId=` (GET) | absolute | true | first | api | const_fold | PROBE |
| `https://idvs-api.acme.corp/api/FieldHistory` (POST) | absolute | true | first | api | const_fold | PROBE |
| `https://reporting.acme.corp/rpt/v3/export` (GET) | absolute | true | first | api | const_fold | PROBE |
| `/SiteLinks?active=true` (GET) | relative | true | first | api | interceptor | NEEDS_HOST → PROBE |
| `/Document/{id}` (DELETE) | relative | true | first | api | interceptor | NEEDS_HOST → PROBE |
| `https://login.microsoftonline.com/72f988bf-.../v2.0` | absolute | false | third | page | literal | PROBE (auth) |

The interceptor is the payoff case for the refactor. Under the old enum, `/SiteLinks` is `endpoint_suspected` — "Medium, probe candidate" — when it is in fact a **confirmed live call whose host is recoverable from elsewhere in the same file**. Under the field model it's `relative + at_sink: true`, and a resolver pass can fill in `base_source: interceptor`. That combination didn't exist in the old table at all.

Heuristic worth implementing: if exactly one `environment`-like object in the bundle has an `apiBaseUrl`/`baseUrl`/`API_URL` key, offer it as a candidate base for unresolved relatives, marked `inferred: true`.

## 05 — Nuxt / Vue `useFetch`

| Finding | resolution | at_sink | scope | kind | base_source | bucket |
|---|---|---|---|---|---|---|
| `/DocumentContent?documentId=` | relative | true | unknown | api | option_baseurl (`config.public.apiBase`, unresolved) | NEEDS_HOST |
| `/DocumentSplitDetailsPagination` (POST) | relative | true | unknown | api | option_baseurl | NEEDS_HOST |
| `/api/_nuxt/translations?locale=he-IL` | relative | true | first | api | none | NEEDS_HOST → PROBE |
| `https://idvs-legacy.acme.dev/api/ping` | absolute | true | first | api | literal | PROBE |
| `/auth/login?next=` | relative | false | first | page | none | NEEDS_HOST |

The `baseURL` option is a **sibling key in the options object**, not part of the URL expression. A detector that only inspects `arguments[0]` never sees it. Same shape in `$fetch`, `ky`, `wretch`, and `fetch` wrappers generally — worth a dedicated rule that reads `baseURL`/`baseUrl`/`prefixUrl` from `arguments[1]`.

## 06 — Axios instance with `baseURL`

| Finding | resolution | at_sink | scope | kind | base_source | bucket |
|---|---|---|---|---|---|---|
| `.../v1.0/AssignedQueue`, `/AssignedDocument`, `/Authentication`, `/Authentication/Client`, `/DocumentHistory` | absolute (after resolve) | true | first | api | axios_instance | PROBE |
| `/users/{id}` (DELETE), `/users/{id}/impersonate` (POST) via `admin` | relative | true | unknown | api | axios_instance (base `window.__CFG__.adminApi` unresolved) | NEEDS_HOST |
| `https://auth.accenture.com/oauth2/token/refresh` (POST) | absolute | true | first | api | literal | PROBE |

Two instances, one resolvable and one not, from the same file. This is why `base_source` needs to be per-finding rather than per-file. Note the second instance still yields **paths** — `/users/{id}/impersonate` is a strong lead even without a host, because you can try it against every host you do know.

## 07 — Dynamic assembly

| Finding | resolution | at_sink | scope | kind | bucket |
|---|---|---|---|---|---|
| `{proto}//{host}/{tenant}/v1/{resource}` | dynamic | true | unknown | api | NEEDS_RUNTIME |
| `new URL("/v1/{a}/{b}/settings", API_BASE)` (PUT) | dynamic | true | unknown | api | NEEDS_RUNTIME |
| `/AssignedQueue`, `/DocumentSplitDetailsPagination`, `/admin/purge-all` (from `ENDPOINTS` map) | relative | true | unknown | api | NEEDS_HOST |
| `axios(cfg.url)`, `fetch(row.href)`, `fetch(this.props.endpoint)` | dynamic | true | unknown | unknown | NEEDS_RUNTIME |
| `fetch(host + ["","api","v1","keys"].join("/"))` → `/api/v1/keys` | relative | true | unknown | api | NEEDS_HOST |

The lookup-table case is the one to get right. `ENDPOINTS[key]` is dynamic *selection* over *static values* — emit all three values as relative findings rather than one `EXPR`. The old enum forced this into `endpoint_unresolved`, throwing away three perfectly good paths including `/admin/purge-all`.

Emit `url_template` alongside `url` for the multi-hole cases: `{proto}//{host}/{tenant}/v1/{resource}` is useful to a human even though it isn't curlable.

## 08 — Router table + auth redirects

| Finding | resolution | at_sink | scope | kind | bucket |
|---|---|---|---|---|---|
| `/queue/:queueId/review`, `/admin/users`, `/admin/feature-flags`, `/internal/debug-console`, `/legal/gdpr` | relative | false | first | page | NEEDS_HOST |
| `/api/admin/users` (GET, route loader) | relative | true | first | api | NEEDS_HOST |
| `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?client_id=…&redirect_uri=…&scope=…` | absolute | true | third | page | PROBE |
| `.../v2.0/.well-known/openid-configuration` | absolute | false | third | api | PROBE |
| `https://idvs-api.acme.corp/swagger/index.html` | absolute | true | first | page | PROBE (high) |
| `https://idvs-api.acme.corp/swagger/v1/swagger.json` | absolute | false | first | api | **PROBE (highest)** |

This file is the counter-argument to `page_route = Low value`. The authorize URL carries `client_id`, `redirect_uri`, `scope` and tenant — that's the entire input surface for redirect_uri validation bugs, scope escalation, and tenant confusion. And `swagger.json` is a `page_route`-shaped string that hands you the whole API.

Suggested rule: any URL whose path matches `swagger|openapi|graphql|\.well-known|actuator|/v[0-9]+/api-docs` gets a `high_signal` flag regardless of `kind` and `at_sink`.

## 09 — Non-fetch sinks

| Finding | resolution | at_sink | scope | kind | sink | bucket |
|---|---|---|---|---|---|---|
| `https://gql.acme.io/graphql` (POST) | absolute | true | first | api | fetch | PROBE |
| `wss://rt.acme.io/socket?token=` | absolute | true | first | api | WebSocket | PROBE |
| `ws://rt-legacy.acme.io:8080/stream` | absolute | true | first | api | WebSocket | PROBE |
| `/api/events/stream` | relative | true | first | api | EventSource | NEEDS_HOST |
| `/api/telemetry/client-error` (POST) | relative | true | first | api | sendBeacon | NEEDS_HOST |
| `https://idvs-api.acme.corp/api/Document/{docId}` (DELETE) | absolute | true | first | api | XHR | PROBE |
| `https://cdn.acme.io/worker/v3/ocr-parser.js` | absolute | true | first | asset | importScripts | PROBE (fetch & re-scan) |
| `https://idvs-api.acme.corp/api/Document/Upload` (POST) | absolute | false | first | api | form.action | PROBE |

Sink list to cover: `fetch`, `XMLHttpRequest.open`, `axios*`, `$.ajax`/`$.get`/`$.post`, `WebSocket`, `EventSource`, `navigator.sendBeacon`, `importScripts`, `new Worker`, `new URL`, `form.action`, `img.src`/`script.src`, `window.open`, `location.assign/replace/href`, plus wrappers (`ky`, `got`, `superagent`, `request`).

Store the sink name. `WebSocket` vs `fetch` changes what you do next, and the old enum flattened both to `endpoint`.

GraphQL deserves its own record shape: one URL, N operations. Extract `query`/`mutation` names and emit them as `operations[]` on the endpoint rather than as separate endpoints.

## 10 — Third-party telemetry

Every one of these is `absolute + at_sink: true` — identical to your highest-value bucket under the old enum. Only `scope` separates them.

| Finding | scope | kind | bucket |
|---|---|---|---|
| `https://o1234567.ingest.sentry.io/4505123456789` (DSN) | third | api | PROBE (low) |
| `https://www.google-analytics.com/g/collect` | third | api | DROP |
| `https://cdn.segment.com/analytics.js/v1/…` | third | asset | DROP |
| `https://api.segment.io/v1/track` | third | api | DROP |
| `https://api.stripe.com/v1/payment_intents` | third | api | PROBE (low) |
| `https://api-iam.intercom.io` | third | api | DROP |
| `https://api.launchdarkly.com/sdk/evalx/6540ab/users/…` | third | api | PROBE (low) |
| `tracePropagationTargets: ["https://idvs-api.acme.corp/api"]` | **first** | api | **PROBE** |

The last row is the catch: Sentry config leaks the first-party API base as a plain config value, off-sink. A blanket "drop everything under a known-SaaS domain" rule would eat it. Filter on the **URL's** host, not the enclosing call's vendor.

Ship a maintained third-party host list (analytics, CDNs, error tracking, ad tech, fonts) as data, not code. Default: keep but demote, never silently discard — a bug bounty scope sometimes includes the vendor.

## 11 — Vendor noise

Everything here is `kind: noise`, `bucket: DROP`.

| Pattern | Why it's noise |
|---|---|
| `http://schemas.openxmlformats.org/...`, `urn:schemas-microsoft-com:...` | XML namespace identifiers — never dereferenced |
| `http://purl.org/dc/elements/1.1/`, `http://www.w3.org/2001/XMLSchema` | schema/vocabulary URIs |
| `http://www.w3.org/2000/svg`, `.../1999/xlink` | DOM namespace constants |
| `http://www.apache.org/licenses/LICENSE-2.0` | license header |
| `http://www.w3.org/TR/xhtml1/DTD/...` | DOCTYPE literal |
| `URL_RE = /^(https?:\/\/)?.../` | URL *pattern*, not a URL — string is inside a regex literal |
| `//# sourceMappingURL=xlsx.full.min.js.map` | build artifact — but see below |

Suppression rules, roughly in order of value:

1. **Namespace/schema hosts** — `schemas.*`, `*.w3.org`, `purl.org`, `purl.oclc.org`, `urn:`, `uuid:`. Hard block.
2. **Node type, not regex.** `URL_RE` is only distinguishable from a real URL by the fact that it lives inside a regex literal. tree-sitter already gives you that — use the AST node type as a suppression input.
3. **Comment context.** License URLs live in comments. Findings from comment nodes get `in_comment: true` and demote hard, but don't drop — commented-out API calls are a genuine source of dead-but-live endpoints.
4. **sourceMappingURL is not noise for your purposes.** It's `kind: asset` and `high_signal` — a reachable `.map` gives you original sources and often the server-side route names. Emit it as its own finding type.

## 12 — Encoded / obfuscated

| Finding | how | resolution | bucket |
|---|---|---|---|
| `https://api.acme.io/v2/admin/impersonate` (POST) | `atob` on base64 literal | absolute | PROBE |
| `/api/internal/debug/dump` | `atob` | relative | NEEDS_HOST |
| `/api/v1/secrets` | `\x` escapes | relative | NEEDS_HOST |
| `/api/v1/keys/rotate` (POST) | array `.join("/")` with `\u002f` | relative | NEEDS_HOST |
| `/api/v1/flags` | `String.fromCharCode` | relative | NEEDS_HOST |
| `/api/internal/debug/dump` | `.split("").reverse().join("")` | relative | NEEDS_HOST |

Worth a cheap decode pass: base64-decode any string matching `^[A-Za-z0-9+/]{16,}={0,2}$` and keep the result if it parses as a URL or path; normalise `\x`/`\u` escapes before matching; const-fold `.join()` on literal arrays and `String.fromCharCode` on literal args. Set `obfuscated: true` and rank these **up** — nobody base64s `/api/health`.

Don't build a general deobfuscator. Three or four fold rules catch most of what's actually out there.

## 13 — Service worker + manifests

| Finding | resolution | at_sink | kind | bucket |
|---|---|---|---|---|
| `/`, `/queue`, `/admin/users`, `/admin/feature-flags`, `/internal/debug-console` (PRECACHE) | relative | false | page | NEEDS_HOST |
| `/static/js/main.4f2a91c.js`, `/static/css/main.8b1.css`, `/static/js/vendor.2c9.js` | relative | false | asset | DROP (but **queue for scanning**) |
| `/api/documents/.*` (workbox route regex) | relative | false | api | NEEDS_HOST |
| `/api/sw-sync` (POST) | relative | true | api | NEEDS_HOST |
| `https://idvs-api.acme.corp/api/Notification/ack` (POST) | absolute | true | api | PROBE |
| `https://config.acme.io/idvs/prod/config.json` | absolute | false | asset | **PROBE (high)** |

Two things: the precache list is a free route enumeration including admin and debug pages, and asset URLs are `DROP` for reporting but should feed back into the crawl queue — `vendor.2c9.js` is the next file to scan. That's a pipeline signal, not a finding; keep it in a separate `discovered_scripts[]` output.

A remote `config.json` is high value — it typically contains the very base URLs that made half this corpus `NEEDS_RUNTIME`.

---

## What the corpus proves about the refactor

| Case | Old enum | Field model |
|---|---|---|
| Angular interceptor `/SiteLinks` | `endpoint_suspected` (Medium) — loses that it's a live call | `relative` + `at_sink` + `base_source: interceptor` |
| Axios instance `/AssignedQueue` | `endpoint_suspected` — loses recoverable host | resolves to `absolute` via `axios_instance` |
| `ENDPOINTS[key]` map | one `endpoint_unresolved` | three `relative` findings incl. `/admin/purge-all` |
| Sentry `tracePropagationTargets` | `endpoint_generic` (Medium) | `absolute` + `first_party` → top bucket |
| `swagger.json` | `page_route` (Low) | `high_signal` flag → top of the list |
| GA `collect` | `endpoint` (High) | `absolute` + `third_party` → demoted |
| `atob(...)` admin endpoint | not detected at all | `absolute` + `obfuscated: true` → ranked up |
| `sourceMappingURL` | noise | `asset` + `high_signal` |

Six of eight are cases where the old enum ranked a finding **backwards**, not merely too granularly.
