# Operating the platform (internal QA guide)

A field guide for a security engineer standing up js-recon-v2 and using it for recon/QA. It
covers three things: **stand it up + first run**, **how to read what it produces**, and its
**security posture + known limitations**. For the system design see
[`ARCHITECTURE.md`](ARCHITECTURE.md); for the *why* behind load-bearing decisions see
[`adr/`](adr/README.md).

> Scope note: this build is for **internal, single-operator / trusted-operator** use. It is not
> yet hardened for untrusted multi-tenant load or live production-tenant data — see
> [Known limitations](#known-limitations) and [`DEBT.md`](../DEBT.md).

---

## 1. Stand it up + first run

Everything runs in Docker: the stores (Postgres, Redis, MinIO), a one-shot `migrate` job, the
`api`, and the `worker` (one image serves all three app roles).

```bash
cd apps/platform
docker compose up -d --build            # postgres + redis + minio + migrate + api + worker
docker compose ps                       # api healthy, worker up, stores healthy; API at :8000
```

The shipped compose sets `RECON_AUTH_SECRET`, so **login is required** and the workspace opens to
a sign-in screen. Seed the first operator off the HTTP surface, then sign in:

```bash
# 1) create a tenant (engagement owner) — prints its UUID
docker compose run --rm api python -m recon.bootstrap create-tenant "Acme Security"

# 2) seed an operator into that tenant. Dev creds admin/admin need --force because compose sets
#    RECON_ENV=docker, which the weak-password guard doesn't treat as a dev env.
docker compose run --rm api python -m recon.bootstrap seed-admin \
  --tenant-id <uuid-from-step-1> --username admin --password admin --force

# 3) open http://localhost:8000 and log in as  admin / admin
```

> Change the credentials for anything beyond a laptop, and override `RECON_AUTH_SECRET` from a
> real secret store — rotating that secret is the platform-wide "revoke all" (tokens are stateless).

### First run — static crawl

1. In the workspace, **New Recon**: enter an in-scope target host and confirm the authorization
   acknowledgment + scope (the fetcher only touches hosts you declare — scope is never inferred
   from crawled content).
2. The run walks its state machine `discovering → fetching → ingesting → analyzing → correlating →
   done`; status streams live (SSE, with an ETag/304 polling fallback).
3. When it reaches `done` (or `partial`), read **Findings**, **Hosts**, **Tech stack**, **Sources**,
   and export the **API Spec** — see [§2](#2-reading-the-output).

### First run — runtime capture (post-auth JS)

A static crawl only sees JS served over the wire. To capture the JS an app loads **after login**
(lazy/dynamic chunks, inline/`eval`'d code), use the extension:

1. `chrome://extensions` → Developer mode → **Load unpacked** → `apps/capture/chrome-extension`.
2. In the popup, **sign in** to the workspace (same operator) and pick the engagement. Captures ride
   your login token into your own tenant. (With auth on, an unauthenticated capture is refused — it
   cannot leak into the shared tenant.)
3. Browse the target normally; captured scripts upload to the platform. Hit **Analyze** to run the
   same Vespasian/Kingfisher/Sourcemapper pipeline over them.

### Host dev + tests

Running without the app containers, running the test lanes, and capture-mode runs are documented in
[`../apps/platform/README.md`](../apps/platform/README.md).

---

## 2. Reading the output

The platform is **honest by construction**: a detected network call whose URL it cannot statically
resolve is *counted, never guessed*. Learning a few terms makes the output unambiguous.

### Findings

Each finding is one of six `type`s:

| type | meaning |
|---|---|
| `endpoint` | A resolved backend call — a concrete method + URL/path recovered from a `fetch` / `XMLHttpRequest` / `axios` / jQuery-ajax / `WebSocket` sink. |
| `endpoint_unresolved` | A real network sink whose **URL didn't statically resolve** (e.g. built from a runtime variable). Reported as a *suspected* backend call, never invented. |
| `endpoint_generic` | A **suspected custom HTTP client** — a call shape that looks like a request wrapper but isn't a known sink. Also suspected, not confirmed. |
| `page_route` | A client-side navigation / document-link target (`href`/`src`/`action`, nav sinks). Deliberately **kept out of the API surface** — it's where the app links, not a backend. |
| `secret` | A secret Kingfisher matched. Stored as `provider:sha256(token)` + location — **never plaintext**; reveal is ephemeral and audit-logged. |
| `param` | A request parameter, optionally carrying advisory **risk tags** (`auth`/`admin`/`idor`/`flag`) to point you at what's worth poking first. |

- **Attributed vs unattributed** — every source file shows an attributed/unattributed coverage
  counter. *Attributed* = the sink's URL resolved to a concrete endpoint; *unattributed* = a sink was
  detected but its URL wasn't statically knowable. High unattributed on a minified-no-map bundle is
  expected, not a defect — it's the honest static ceiling (see [limitations](#known-limitations)).
- **Suspected** = the `endpoint_generic` + `endpoint_unresolved` lanes: things that look like backend
  traffic but couldn't be confirmed. Treated as leads, surfaced separately from confirmed endpoints.
- **Sightings** — a finding seen by more than one run in the same engagement shows sighting counts,
  split by origin: `platform` (a crawl/upload) vs `capture` (an extension session). This is how you
  tell "the static crawl and the post-auth capture both saw this" at a glance.

### Hosts

Every host a run touched: from fetched assets, resolved-host endpoints, the suspected lanes, tech
detection, and declared base-URL rules — each badged **in / out of scope** by the same egress guard
the crawl enforces. A separate **Suspected** column counts the generic/unresolved (suspected-backend)
hosts. `page_route` hosts (client-nav destinations) are listed but excluded from the suspected-backend
count.

### Sources

Browses both the fetched JS chunks and, when a source map was recovered, the **original sources**
(`app/src/api.js` rather than `input.js`). A finding links straight to its line in the recovered
original. Recovery runs on demand for the file you open, so large sessions stay responsive.

### Tech stack

Per-host technology detection (server, framework, CDN, JS libraries, analytics) from the enthec
fingerprint dataset. A detection made only from a JS window-global (a bundled SPA framework) reads as
**suspected** with no version — static source carries no runtime value, so those are deliberately
capped, never asserted as "certain".

### API Spec (OpenAPI export)

Exports the reconstructed surface as OpenAPI 3.0.3 (JSON or YAML) — the artifact you feed to Burp /
Postman / a future threat model. It is security-enriched: `x-recon-risk` param tags,
`components.securitySchemes` from observed request-auth headers, embedded GraphQL operations under
`x-recon-graphql-operations`, and an `x-recon-confidence` marker. Responses and severity are absent
by design — static reconstruction never observes a response, and severity is the (planned) threat
model's job.

---

## 3. Security posture

What is safe to point this at, and what it will and won't do.

- **No active/exploit traffic (REQ-P1).** The platform never fires exploit traffic. It reconstructs
  requests and hands you a ready-to-run artifact (curl / raw HTTP / OpenAPI export); *you* run the
  probe manually.
- **The only egress is fetch/crawl, SSRF-guarded and fail-closed (REQ-P2, ADR-0005).** http(s) only;
  the host must be in the session's declared scope (**never** derived from crawled or bundle content);
  every resolved IP must be globally routable; DNS is pinned per request and redirects re-validated
  per hop; userinfo and `data:`/`file:` schemes are rejected. Any failure path blocks rather than
  proceeds.
- **Secrets are hashed, not stored (REQ-S2/S4).** A found secret is kept as a one-way hash + location;
  reveal is ephemeral, just-in-time, and audit-logged — the platform is not a store of live
  third-party credentials.
- **Tenant isolation is enforced in the database (REQ-S1).** Every row and blob key is tenant-scoped
  by Postgres row-level security, enforced on a non-superuser app role — not just at the API.
- **Auth (central login).** Password + bcrypt, stateless HMAC-signed session tokens, one generic 401
  (no user enumeration, timing-equalized), and a Redis-backed brute-force throttle. The tenant comes
  from the verified token, never a client header.
- **Engines are out-of-process + hardened.** Kingfisher/Sourcemapper run under the engine harness
  (`findings/engines.py`: wall-clock timeout, output cap, explicit acceptable exit codes, non-root),
  and katana under a separate crawl harness (`discover/harness.py`: timeout, output cap, non-root,
  whole-process-group kill on timeout). Kingfisher runs offline (`--no-validate`, no network).

### Known limitations

Deliberately deferred — safe for internal single-operator use, revisit before wider/untrusted rollout
(full list + rationale in [`DEBT.md`](../DEBT.md)):

- **Egress isolation is application-level only (DEBT D18).** There's no OS/network-level sandbox
  (netns/nftables/nsjail) around the worker, and headless-Chrome capture loads subresources outside the
  per-request guard. Fine behind an internal boundary; harden before exposing to untrusted load.
- **`allow_anon_capture` defaults on but is neutralized when auth is on.** With the shipped
  `RECON_AUTH_SECRET` set, anonymous captures are refused; set `RECON_ALLOW_ANON_CAPTURE=false` to
  reject them regardless.
- **No role-based authorization yet.** Every signed-in user in a tenant is effectively an operator.
  Fine for a single trusted operator; add RBAC before multi-user tenants.
- **Migrations aren't frozen snapshots (DEBT D19).** Pre-prod only; freeze before running incremental
  upgrades against live tenant data.
- **CI is advisory, not a hard merge gate.** Branch protection isn't available on the current
  (Free-tier private) plan; the four CI lanes run on every push/PR but don't *block* merge.
- **Static recall has an honest ceiling (DEBT D29/D30).** URLs computed at runtime or passed
  interprocedurally in minified-no-map bundles stay `unattributed` rather than guessed. Runtime capture
  (post-auth JS) is the lever for what static analysis can't reach.
- **The AI threat model + consolidated recon report are planned, not built.** Only the OpenAPI export
  ships today; the "Threat Model" workspace tab is marked SOON.
