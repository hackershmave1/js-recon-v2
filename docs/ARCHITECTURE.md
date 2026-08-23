# Architecture

This repo (`js-recon-v2`) is **one product** — the recon **platform** — plus the surviving v1
**Chrome extension**, a client that feeds it. Both reconstruct a backend's API surface from
JavaScript; the platform does it from **static crawl/upload**, the extension adds **runtime,
post-authentication capture** and hands what it grabs to the platform for the same analysis.

> **This document is the *what*; the [`adr/`](adr/README.md) trail is the *why*.** The
> load-bearing decisions behind the design below — the Redis Streams broker, DB-enforced
> row-level security, the fail-closed SSRF egress guard, the static / no-active-traffic
> stance, the v1 convergence, and more — are recorded as Architecture Decision Records
> (MADR) with a pointer to the code that enforces each.

```
apps/
├── platform/   The recon platform — the whole product. Upload/crawl a target's JS -> Vespasian
│               tree-sitter AST analysis -> content-addressed findings -> OpenAPI rebuild
│               (an evidence-grounded threat model over that surface is a planned next stage,
│               marked SOON in the workspace). Async spine (FastAPI API + Redis-Streams worker +
│               MinIO/S3 blobs + multi-tenant Postgres with row-level security). Frontend: the
│               Recon Workspace — React + Vite + TypeScript. API :8000.
└── capture/
    └── chrome-extension/   MV3 extension — the one v1 piece carried forward. Intercepts the JS
                            the browser actually loads (behind auth) and pushes it to the
                            platform's ingest contract. Default backend http://localhost:8000.
```

## The platform (`js-recon-v2`)

The platform is the sole backend **and** the workspace UI. It is a requirements-driven rewrite
(the 41 `REQ-*` IDs in [`REQUIREMENTS.md`](REQUIREMENTS.md)).

**Async spine.** The API is a thin accept/validate/enqueue/read tier (`api/app.py` — "no route
does crawl/fetch/parse/LLM/probe work"): `POST /runs` persists a run and enqueues, returning in
<200ms; heavy work runs in the **worker** process. The two communicate over a **Redis Streams**
broker — one stream per work class, a `workers` consumer group, at-least-once delivery with
backoff-retry and a per-queue dead-letter stream (`queue/streams.py`). A run is a persisted state
machine (`queued→discovering→fetching→ingesting→analyzing→correlating→done`, plus `paused`/
`cancelled`) advanced by the worker at cancel/pause-checkpointed stages (`worker/main.py`).

**Immutable runs + edit-&-re-run.** A run is an immutable snapshot of its config, so re-running
never mutates it: `POST /runs/{id}/rerun` clones a specific run's config into a *new* run and
applies the operator's edits, leaving the source untouched (`runs/coordinator.py::edit_and_rerun`).
The editable fields are target, crawl mode (the capture toggle), scope hosts, and the per-run fetch
cap; any field the caller omits is inherited from the source, so the UI prefills from
`GET /runs/{id}/config` (RLS-confined — a run the tenant can't see is a 404, which is also the
cross-tenant IDOR gate). A re-run reuses the source's session **unless** the scope value actually
changed or the edited target falls outside it, in which case it forks a fresh session that requires
a new `authorized_by`: a widened scope is re-attested by the operator, never carried over from the
source's ack (REQ-P2/P3). An upload source instead re-analyzes a fresh copy of the stored bytes (its
target is only a REQ-C2 base-URL hint). The legacy `POST /sessions/{id}/rerun` (whole-session
re-run) now delegates through the same clone path, so a capture re-run keeps its `crawl_mode`
instead of silently reverting to a static crawl.

**Per-run fetch cap.** Each run carries an optional `max_fetch_bytes` (migration `0013`);
`config.clamp_fetch_bytes` resolves the effective per-asset cap and fails *closed* — a `None`, `0`,
or negative override falls back to the global default (10 MiB), and the result is hard-clamped to
`max_fetch_bytes_ceiling` (32 MiB, the engine output cap and the real analyze-memory bound). The cap
threads through every fetch site (the static crawl's asset and source-map fetches, plus the capture
stage's per-script cap), and an override above the ceiling is rejected with a 422 rather than
persisted. This is the principled knob for an oversized bundle: re-run with a larger cap (up to
32 MiB) instead of silently truncating — a bundle past 32 MiB needs an ops ceiling-plus-engine
raise, not just the per-run override.

**Data + isolation.** State lives in **Postgres**, isolated per tenant by **row-level security**
enforced in the database, not just the API: every tenant-scoped table has an RLS policy keyed on
`current_setting('app.current_tenant')`, and the only supported access path is `tenant_session()`,
which sets that GUC for one transaction (`db/base.py`). Large/binary artifacts (raw JS, source
maps, reconstructed sources, reports) never sit in a row — they go to **S3/MinIO** under a
content-addressed, tenant-scoped key `{tenant_id}/{run_id}/{kind}/{sha256}` (`storage.py`, one
boto3 client, path-style + s3v4 so the same code hits MinIO locally and S3 in production).

**Engines.** Three analysis engines, plus katana for discovery:

| engine | role | how it runs |
|---|---|---|
| **Vespasian** | tree-sitter JS AST walker; traces `fetch` / `XMLHttpRequest.open` / `axios.*` / jQuery `$.ajax\|$.get\|$.post` / `new WebSocket` into endpoints, methods, URLs, and statically-resolvable params, and separately recovers client-side **page routes** (`href`/`src`/`action` values, nav sinks, and off-sink absolute-URL literals) as a distinct `page_route` finding kept out of the API surface (engine tag `vespasian`) | **in-process** (`findings/extract.py`, `findings/analyze.py` — `tree_sitter` + `tree_sitter_javascript`) |
| **Kingfisher** | MongoDB Kingfisher secret scanner (`kingfisher-bin==1.106.0`), run offline (`--no-validate --no-update-check --no-dedup`); the raw token is hashed into the finding, never stored in the identity | **out-of-process** binary (`findings/kingfisher.py`) |
| **Sourcemapper** | recovers a bundle's original sources from its source map so findings attribute to real paths (`app/src/api.js`) instead of the `input.js` placeholder (`github.com/denandz/sourcemapper`, Go-built) | **out-of-process** binary (`findings/sourcemapper.py`) |
| katana | JS-asset discovery crawler for the DISCOVER stage (discovery-only argv; "Vespasian parses, not katana") | out-of-process (`discover/katana.py`) |

The out-of-process engines run through one hardened harness (wall-clock timeout, output-size cap,
explicit acceptable exit codes, non-root container user) so the safety controls live in one place
(`findings/engines.py`). Analysis is honest by construction (REQ-C2): a detected sink whose URL
isn't statically resolvable is counted as *unattributed*, never invented; a missing engine binary
degrades coverage honestly rather than silently reporting "clean". The reconstructed OpenAPI
export is security-enriched (the enrichment slice): each operation carries `x-recon-risk` param
tags, observed request-auth headers become `components.securitySchemes` + per-op `security`, and
embedded GraphQL operations surface as an `x-recon-graphql-operations` root extension — never as
HTTP paths or findings.

**Cross-chunk resolution + lazy-chunk enumeration.** Vespasian's base extractor resolves a URL
per file, so a bundler that splits a `fetch`/`axios` call from the string constants it is built
from (a base URL in one chunk, a path in another) would leave it *unattributed*. A static ESM
module graph (`findings/_modulegraph.py`) plus a run-level cross-module index
(`analyze.py::CrossModuleIndex`) fold those cross-chunk operands back together across all three
bundle shapes — source-map-recovered ESM, minified ESM (vite), and minified webpack (`require.d`
export registries + `n(id).member` require-aliases) — still honestly (only a statically-certain
string literal crosses a module boundary; anything dynamic stays unattributed). Separately, a
webpack bundle's lazy chunks have URLs *computed at runtime* by its `__webpack_require__.u`
builder, invisible to both the static pass and katana; `findings/chunkenum.py` reconstructs those
URLs **statically** (folding the `.u` template + inline chunk→hash map, no execution) and the
FETCH stage fetches each through the egress guard so its endpoints are recovered — content-derived
URLs never widen scope, the `crawl_max_assets` cap is re-applied at the seed site, and out-of-scope
chunks are dropped. Executing *arbitrary/obfuscated* chunk-builders in a JS sandbox is a deliberate
future posture change, deferred with its security contract (DEBT D29).

**API surface + frontend.** The API mounts routers for sessions, engagements, runs, findings,
manual-probe, sources, the OpenAPI spec + diff, export, base-URL overlay, wrapper-teaching,
per-host tech detection, and a discovered-hosts inventory (`api/app.py`) — driven day-to-day at `localhost:8000/sessions`, `/runs`, `/runs/{id}/findings`,
etc. The **Recon Workspace** frontend (React 19 + Vite + TypeScript, `apps/platform/web/`) is the
operator UI over that surface. Status streams back over SSE with an `ETag`/`304` polling fallback.
Its **Sources** page browses both the fetched JS chunks and the source-map-recovered originals —
the recovered list is derived from persisted `occurrence.source_path` (no subprocess fan-out; the
Sourcemapper binary reruns on demand only for the one file being viewed) — with lazy syntax
highlighting, and a finding occurrence links straight to its line in the recovered original. Its
**Hosts** page enumerates every host a run discovered — from assets, resolved-host endpoints,
suspected-backend calls (the generic/unresolved lanes), tech detection, and declared base-URL rules —
each badged in/out of the session's declared scope by the same egress guard the crawl enforces (`GET
/runs/{id}/hosts`; host-less endpoints and suspected calls are reported, not hidden).

**Runtime JS capture (server-side, default-off).** A run can set `crawl_mode="capture"` to route the
DISCOVER stage away from katana and into an in-process CDP capture stage (`recon/capture/`) instead.
It drives the headless Chromium already baked into the worker image over raw CDP
(`websockets.sync`) and records every script V8 actually parses — `Debugger.scriptParsed` +
`Debugger.getScriptSource`, fanned out across the whole target tree (the page, dedicated/shared
workers, and service workers) via a browser-level `Target.setAutoAttach{flatten}` waterfall. This
recovers inline, runtime-injected, and `eval`'d JS — and worker / service-worker code — that crosses
no network response and so is invisible to the static fetch path. A capture run also **drives the
page** — autoscroll, click-all, and same-origin route-enum — so lazily-loaded / route-split /
click-gated chunks execute and are captured (each source is fetched on-parse, so repeated navigations
don't strand earlier routes). Captured scripts are written as the same capture asset contract the
extension produces (`run_asset` `input` blobs pre-marked `fetch_ok` + a `discover.assets` event), so
FETCH no-ops and ANALYZE is unchanged. Each script's external source map — the `sourceMapURL` V8
reports on parse — is fetched through the same egress guard the static crawl uses and linked on its
asset, so a captured minified bundle recovers its real source paths in ANALYZE (inline `data:` maps
are already recovered from the source itself). It is **default-off** behind `RECON_ENABLE_CAPTURE_MODE`
and
relaxes the *static-only fetch* posture (not ADR 0006's no-automated-*exploit* stance — it sends no
exploit traffic): it runs only against an in-scope, `authorization_ack`-ed target, with a pre-launch
egress-scope validation and a per-script in-scope re-check. Driving interaction widens the egress
footprint, so request-layer egress interception is a tracked follow-up (the egress-proxy slice). See
[ADR 0009](adr/0009-runtime-cdp-js-capture.md).

## Authentication (central login)

**Central login.** `POST /auth/login` (`api/auth_router.py`) is the one route that reads credentials
and the one that resolves a tenant *without* a prior tenant context. It verifies a username + bcrypt
password (`auth/service.py`, `auth/passwords.py`) and mints a **stateless HMAC-signed session token**
(`auth/token.py`) whose payload names the user, tenant, role, and an 8h expiry
(`{typ:auth, sub, t, role, exp}`). Verification requires the `typ:auth` discriminator, so any other
Bearer token that happens to share this compact `Authorization: Bearer` wire format can never verify
as a login. A failed login is one generic `401` — unknown user, bad password, and
cross-tenant-ambiguous username are indistinguishable (no user enumeration), equalized with a dummy
bcrypt compare so response time can't confirm a username.

**Opt-in by config, tenant-from-token.** Auth is gated on `RECON_AUTH_SECRET`. **Empty ⇒ auth
disabled**: `/auth/login` soft-fails `503` and every route falls back to the legacy `X-Tenant-Id`
header stand-in — this is how dev/test run, so the header-based tests need no change. **Set ⇒ login
required**: `api/deps.get_principal` verifies the Bearer token and `get_tenant_id` derives the tenant
*from the verified token*, never from a client header (the `allow_header_tenant` escape hatch is
default-off). Rotating the secret is the platform-wide "revoke all" — the token is stateless, with no
per-token store. Capture ingest resolves the same way (`capture_router._resolve_ingest_tenant`): auth
token, then — only while auth is *disabled* — the shared capture tenant; a set
secret disables the anonymous fallback entirely, so post-auth JS can never leak into the shared
tenant (fail closed).

**User store + seed.** Logins live in `app_user` (`password_hash` added by migration `0014`), unique
per `(tenant, email)`. The login lookup is the platform's one legitimate cross-tenant read — it runs
on the RLS-bypassing admin connection because the tenant isn't known until the user is found — and
fails closed on an ambiguous (cross-tenant duplicate) username. Usernames are **case-insensitive**:
`normalize_username` trims + lowercases on both the seed write and the login read, so "Admin" and
"admin" are one operator. There is no self-serve signup; an operator is seeded out-of-band, off the
HTTP surface: `python -m recon.bootstrap seed-admin --tenant-id <uuid> --username <u> --password <pw>`
(idempotent — a re-run doubles as a password reset). A weak-password guard refuses the `admin/admin`
dev default unless `RECON_ENV` is explicitly a dev env or `--force` is passed, so a prod host that
forgot to set `RECON_ENV` can't silently seed a guessable admin.

**Web + extension.** The SPA wraps everything in an `AuthGate` (`web/src/auth/`): a `LoginScreen`
until signed in, then the token rides as `Authorization: Bearer` on every API/SSE call and a global
`401` handler logs out. The token is mirrored into `recon.tenantId` so the existing `TenantProvider`
is unchanged, and the TopBar shows the signed-in user · tenant with a Log out control. The Chrome
extension signs in from its popup; the login token becomes its
upload Bearer and capture is gated on being signed in — so a capture lands in the operator's own
tenant and is visible in their workspace.

**Brute-force throttle.** `/auth/login` is rate-limited by a Redis-backed failed-attempt counter
(`auth/login_rate_limit.py`), checked *before* the bcrypt verify so a login flood can't burn CPU (the
no-enumeration equalizer spends a bcrypt on every attempt). It counts failures per rolling window and
clears on success, keyed **per-username plus a global backstop** — never by client IP, which behind
the expected ingress proxy would collapse to a single self-DoS bucket. It **fails open** on a Redis
error (via a short-timeout client, `deps.get_login_redis`): the throttle is defense in depth, not the
access gate — the password + signed token stay fail-closed — so a Redis blip must not lock every
operator out. Config-gated by `RECON_LOGIN_RATELIMIT_MAX_ATTEMPTS` (`<=0` disables; defaults 10 per
5-minute window, global 60).

**Roadmap.** Google OAuth is the intended second identity path — the stateless-token seam is built
for it (a verified OAuth identity mints the same session token). Multi-tenant login needs a workspace
selector or a globally-unique login identity before a second tenant can reuse a username (tracked).

## The capture extension (the surviving v1 client)

The MV3 Chrome extension (`apps/capture/chrome-extension/`) is the **only** capability carried
forward from v1. It does what a static crawl cannot: it runs *in the browser*, so it sees the JS
the app actually loads **after authentication** — lazy/dynamic chunks, post-login bundles, and
their source maps — and uploads that to the platform. It is a pure client: it captures and pushes;
all analysis (Vespasian / Kingfisher / Sourcemapper) happens on the platform, on the same async
spine as a native crawl. Its default backend is `http://localhost:8000` (`modules/batch-uploader.js`,
`modules/workspace-client.js`), overridable in the popup's Settings.

## The extension <-> platform ingest contract

The extension talks to the platform over a small `/api` ingest surface (distinct from the
platform's native `/sessions` + `/runs` routes). It is **flag-gated**: `capture_router.py` is
mounted only when `RECON_ENABLE_CAPTURE_INGEST` is enabled (`config.py` `enable_capture_ingest`,
**default on**; the mount is conditional at `api/app.py`). Because that makes the ingest an
always-on *unauthenticated* write surface, two guards (below) make it safe and route each capture
to the right tenant.

| route | purpose |
|---|---|
| `POST /api/save-files` | batched JSON push of captured files; accumulates into the capture session's single open run — one `run_asset` row per file, pre-marked `fetch_ok`, blobs to S3. Binds a valid `projectId`→engagement defensively (an unknown id is ignored, never dropping the batch). |
| `POST /api/sessions/{id}/analyze/start` | emits `discover.assets` and enqueues one worker walk; the pre-fetched assets make DISCOVER/FETCH no-op (no katana, no network egress of captured URLs), so only ANALYZE does real work. |
| `GET /api/sessions/{id}/analyze/progress` | adapts the run's per-asset status into the popup's counts+files `job` shape. |
| `GET\|POST /api/projects` | adapts platform engagements to the extension's project shape (a bare JSON array, `id`, a synthesized `defaults.scope`). |
| `GET /api/health` | capture-ingest liveness (distinct from the platform's own `/healthz`). |

The path is idempotent by construction — content-addressed blobs + `(run_id, url)` conflict-skip —
so the extension's whole-batch retries never duplicate a run or an asset. Per-file source maps the
extension sends are stored per asset (`run_asset.source_map_ref`, migration `0010`) and recovered
with the tolerant `source_map_origin="capture"`, so a malformed map falls back to bundle analysis
instead of dropping the asset's findings.

## Routing captures to the operator (the capture <-> app link)

The ingest doesn't *require* auth, but the extension does log in to the platform: a signed-in
operator's capture rides their central-login session token as `Authorization: Bearer` into their
own tenant, while an unauthenticated write falls back (fail-closed) to the shared `capture-spike`
tenant. A stacked slice series makes that always-on surface safe and routes each capture to the
*right* tenant:

- **Origin-lock (anti-CSRF).** A state-changing ingest POST carrying an `http(s)` `Origin` is
  rejected `403` (`_enforce_origin_lock`); default-on kill-switch `capture_ingest_origin_lock`. The
  MV3 worker's `null` Origin is allowed.
- **Login -> operator tenant.** A logged-in operator's capture rides their central-login session
  token (`recon.auth`) as `Authorization: Bearer` on **all** tenant-resolving ingest calls, routing
  the capture into their own tenant; no/invalid Bearer falls back to a fixed `capture-spike` tenant
  (fail-closed, never a hard drop). `save-files` returns a `paired` flag the popup surfaces.
- **Live indicator.** `save-files` best-effort emits `capture.received` on the run's SSE stream, so
  the operator's run workspace shows a live "receiving from extension" chip.

**Cross-run sightings (read model).** The same JS reached by both a platform crawl and a paired
capture yields duplicate findings across two runs (REQ-D5 keeps findings per-run). `list_findings`
collapses this at read time: each finding carries **sightings** — counts of *other runs in the same
engagement* sharing its `finding_hash`, split by origin (`capture` = extension session, `platform` =
crawl/upload), or `null` when the run's session has no engagement (surfaced in the workspace as a
per-finding badge or a "group under an engagement" hint).

## Convergence history (v1 retired)

Before the cutover, `apps/capture/` was a full v1 app in its own right — the older *JS Security
Extractor*:

- `apps/capture/api/` — a single-user, threaded FastAPI backend (no Redis/worker/S3; local-disk
  storage; Postgres) that served `:3000`, with its own REP-style endpoint/secret extractors and
  jsluice integration.
- `apps/capture/web/` — the original "RECON Workspace" SPA, built and served by that backend.

Both were **retired and deleted** in the convergence: the platform already ships its own analysis
(Vespasian + Kingfisher + Sourcemapper), Sources viewer, and findings/OpenAPI pipeline per the v2
requirements, so v1's backend and UI were redundant. Their one durable capability — runtime capture
followed by analysis — is now served by the platform through the ingest contract above, built
incrementally behind `RECON_ENABLE_CAPTURE_INGEST` (Phase 1 ingest → Phase 2 endpoint parity →
Phase 3 source-map robustness) so the extension was never broken mid-migration. The cutover then
repointed the extension's default backend from `:3000` to `:8000` and removed `apps/capture/{api,web}`.

v1-only tooling was **not** carried into v2: jsluice, the REP-style regex extractors, and the
asset-provenance graph appear in zero REQ-* items and were left behind, not unioned in. (An earlier
draft called the two analysis cores "complementary" and proposed unioning them; that was a
v1-porting assumption, unsupported by the requirements, and is not part of the converged product.)

## Running

Everything is one full stack — the stores (Postgres, Redis, MinIO), a one-shot `migrate` job, the
`api`, and the `worker` (one image serves all three app roles):

```bash
cd apps/platform
docker compose up -d --build   # postgres + redis + minio + migrate + api + worker; API at http://localhost:8000

# create a tenant (privileged admin connection, off the HTTP surface)
docker compose run --rm api python -m recon.bootstrap create-tenant "Acme Security"
```

The capture-extension ingest surface is mounted **by default** (`RECON_ENABLE_CAPTURE_INGEST`, on;
set it `false` to disable). Load the extension unpacked:

```bash
# chrome://extensions -> Developer mode -> Load unpacked -> apps/capture/chrome-extension
# the extension's default backend is already http://localhost:8000; sign in from its popup
```

To run a **server-side capture** run instead of a static crawl, enable the capture flag on the API +
worker and start the run in capture mode:

```bash
# RECON_ENABLE_CAPTURE_MODE=true in the api/worker env (add RECON_ALLOW_LOCAL_EGRESS=true for a local
# target), then start a run with crawl_mode="capture" — the New Recon "Runtime capture" toggle in the
# workspace, or capture:true on POST /runs. The API rejects capture:true while the flag is off.
```

Per-app detail lives under each app (`apps/platform/README.md`; the extension's own docs under
`apps/capture/chrome-extension/`).
