# recon-range

`recon-range` is a deliberately-messy, self-contained JS web app that serves as the **controlled
verify vehicle** for the extension→platform convergence. Instead of pointing the pipeline at a
random live site and eyeballing the output, this target's exact API surface and planted secrets
are documented up front in a machine-readable **answer key** (`answer-key.json`), so a run can be
scored found / missed / unexpected by `scripts/score.mjs`.

**This is the on-demand scoring harness for the capture→platform pipeline.** The real MV3 extension
drives the platform end-to-end against this target and the run is scored green / red on both bundler
outputs. (It originally gated the Phase 4 `apps/capture/{api,web}` deletion — that cutover has since
landed, so the harness now serves as a repeatable regression check.) This README is the runbook for
producing that score; it does not cover the cutover itself.

The app is one shared vanilla-ESM source (`src/`) compiled two ways — once by Vite, once by
Webpack — because the extractor's chunk/source-map handling is bundler-specific and both paths
need independent proof.

## 1. Install

```bash
cd test-targets/recon-range
npm install
```

Node ≥18 (uses built-in `node --test` and global `fetch`). No test framework, no UI framework —
this installs only `axios` (a planted target dependency) plus Vite/Webpack as dev dependencies.

## 2. Build + serve each bundler

Each bundler produces its own output directory and its own origin, so the two runs never mix
assets:

| Bundler | Build | Serve | Origin |
|---|---|---|---|
| Vite    | `npm run build:vite`    | `npm run serve:vite`    | `http://localhost:4173` |
| Webpack | `npm run build:webpack` | `npm run serve:webpack` | `http://localhost:4174` |

(`npm run build` runs both builds back to back; there's no combined serve script, since the
capture/score walkthrough below is done one origin at a time.)

Each build emits an entry chunk plus 3 lazy chunks (`import()`-loaded), each with a
`//# sourceMappingURL=` comment and a same-origin `.map` carrying non-empty `sourcesContent`. If
a build is missing chunks or maps, `npm test`'s `build-invariants` check will catch it (run `npm test`).

## 3. Capture with the real extension (real Chrome only)

The in-app/embedded browser can't drive an MV3 extension, so this step is done in a normal Chrome
window with the "JS Security Extractor Pro" extension (`apps/capture/chrome-extension`) loaded. The
rest of the runbook (build, score) is automatable; this step is not.

1. Start one bundler's server from §2 (e.g. `npm run serve:vite` → `http://localhost:4173`).
2. Open the extension popup → **Settings**:
   - **Workspace URL** = `http://localhost:8000` (the platform API).
   - **CONNECTION → Sign in** with the dev default `admin` / `admin` (the default stack runs with
     auth ON, so captures are attributed to the signed-in operator's tenant — see §5. Skip this and
     the capture lands nowhere you can score).
   - **Capture scope**: add `localhost` (the extension is fail-closed by registrable domain — a
     host not in scope is silently dropped, so this step is required, not optional).
3. Click **Start** capture.
4. Load the target page (`http://localhost:4173`, or `:4174` for the Webpack run) and **scroll to
   the bottom**. The 3 lazy chunks (`inventory.js`, `social.js`, `live.js`) only load — and
   therefore only get captured — when their scroll sentinel becomes visible via
   `IntersectionObserver`. Scrolling past all three is required to capture the full surface;
   stopping early under-counts chunks and will read as missed endpoints later, not a capture bug.
5. Click **Stop** capture.

## 4. Trigger analysis

Kick off analysis for the session the extension just captured (via the popup's **Analyze** action,
or directly: `POST /api/sessions/{ext_id}/analyze/start`). Wait for progress to reach done.

**Note the returned `run_id`** — the scoring script needs it in the next step.

## 5. Resolve the operator tenant UUID (one-time, out-of-band)

> **Only needed for the header-mode fallback (§6).** The default scoring path logs in as the
> operator and derives the tenant from the session token, so you can skip the `psql` lookup below
> unless you're scoring via `RECON_ALLOW_HEADER_TENANT=1` (or an auth-off stack). The tenant model
> it explains still applies either way.

The default stack runs with **auth ON**, which changes where captures land and how you read them
back:

- **Signed-in captures land in the operator's tenant, not `capture-spike`.** `capture-spike` is only
  the fallback tenant for *unauthenticated* capture; once the extension is signed in (§3), its
  uploads are attributed to that operator's tenant — the dev default is **`QA`**. Scoring against
  `capture-spike` under the default auth-on stack finds none of the run's findings and reports a
  **false FAIL**, so resolve and score the operator tenant.
- **Reading `GET /runs/{id}/findings` needs a login token** (or the header escape hatch) — see §6.

Resolve the `QA` tenant's **UUID** — the platform 400s a non-UUID value — once via `psql`:

```bash
docker compose -f apps/platform/docker-compose.yml exec -T postgres psql -U recon -d recon -tAc "select id from tenant where name='QA'"
```

Copy the printed UUID; it's stable for the life of that tenant, so this is a once-per-environment
step, not a once-per-run step.

## 6. Score the run

`GET /runs/{run_id}/findings` is an authenticated route. Under the default auth-on stack the platform
ignores `X-Tenant-Id` and 401s unless the request carries an `Authorization: Bearer <token>` minted
by `POST /auth/login` (`admin` / `admin`). A Bearer token already names the operator tenant, so on
that path no `X-Tenant-Id` is needed — this is the primary, correct way to read findings.

`npm run score` performs that login for you:

```bash
npm run score -- --run <run_id>
```

It logs in via `POST /auth/login` (default creds `admin` / `admin` → tenant `QA`) and sends the
minted token as `Authorization: Bearer` on the findings request — no `--tenant` needed, since the
token already names the operator tenant. Override the creds with `--username`/`--password` or the
`RECON_USERNAME`/`RECON_PASSWORD` env vars to score as a different operator. Score as the **same
operator the capture was signed in as** (§3), or you'll read an empty tenant and get a false FAIL.

**Header-mode fallback.** If login can't mint a token — auth is off (`/auth/login` returns 503), or
you're on a `RECON_ALLOW_HEADER_TENANT=1` stack and aren't logging in — pass the operator tenant
UUID from §5 and the script falls back to the legacy `X-Tenant-Id` header:

```bash
npm run score -- --run <run_id> --tenant <tenant_uuid>
```

(Optional `--base http://localhost:8000` if the platform isn't on the default.) This fetches `GET /runs/{run_id}/findings` and diffs it against
`answer-key.json`, printing a JSON breakdown followed by `PASS` or `FAIL` (and a non-zero exit code
on `FAIL`).

**Reading the output:**
- `missedEndpoints` / `missedParams` / `secretMisses` / `covFail` — any of these non-empty means
  `FAIL`. Each is a real pipeline defect against the calibrated answer key, not a known limitation.
- `blindViolations` — lists any *known blind spot* (see §8) that unexpectedly resolved to a real
  finding anyway. This is informational, not a failure: the answer key plants these constructs
  specifically to document what the pipeline *can't* see, so a violation just means the pipeline
  got smarter than documented. Worth noting, never worth failing the run over.
- `unexpected` — endpoint findings that aren't in `should_find` and don't match a planted
  third-party host. Also informational: it flags noise (e.g. a stray asset request) rather than a
  missed requirement.

## 7. Repeat for the other bundler

Stop the current capture/server, then repeat §3–§6 against the other origin (`:4174` if you just
did `:4173`, or vice versa) with the *other* build. The same answer key scores both runs — run them
both and compare. Divergence between the two bundlers' scores is itself a useful signal (e.g. one
bundler mangling a call shape under minification that the other doesn't).

## 8. Calibration caveats

The answer key is calibrated to what the extractor and capture path provably do today (see the
design spec's grounded-constraints section), not to an aspirational ideal. Keep these in mind when
reading a score:

- **Blind spots are expected-missing, not bugs.** `known_blind_spots` in the answer key
  (`bs-eventsource`, `bs-concat`, `bs-variable`, `bs-wrapper`, `bs-headers`) are planted
  specifically to prove documented limits: `EventSource` calls, concatenated/variable URLs,
  untaught custom HTTP wrappers, and request headers (auth/signature) are all invisible to the
  extractor by design. Their absence from `should_find` results is correct behavior, not something
  to chase.
- **GitHub, Slack, and HMAC secret classes are informational, not gating.** Only Stripe (×2,
  including the one hidden in a preserved `/*! … */` legal comment) and AWS are *verified*
  Kingfisher rule classes in this repo; `secrets.must` only requires those. GitHub/Slack/HMAC
  tokens are planted too, but their detection is unconfirmed until a live run proves it — don't
  treat a miss on those as a regression.
- **The first run is also the first live proof that the fake secrets are actually detected.**
  The planted values are synthetic but well-formed (`sk_live_…`, `AKIA…`, etc.) and deliberately
  avoid canonical vendor example keys, which some scanners whitelist. If the very first scored run
  shows `secretMisses` for Stripe or AWS, treat that as a signal to double check the fake token
  shapes before assuming a pipeline defect.

## Reference: npm scripts

| Script | What it does |
|---|---|
| `npm run build:vite` | Build the Vite bundle to `dist/vite/` |
| `npm run build:webpack` | Build the Webpack bundle to `dist/webpack/` |
| `npm run build` | Run both builds |
| `npm run serve:vite` | Serve `dist/vite/` on `:4173` |
| `npm run serve:webpack` | Serve `dist/webpack/` on `:4174` |
| `npm run score -- --run <id>` | Score a run's findings against `answer-key.json` (logs in as `admin`/`admin`; see §6) |
| `npm test` | Build both bundlers, then run every `scripts/*.test.mjs` (build invariants, answer-key consistency, planted-surface presence, scoring logic) |
