# recon-range

`recon-range` is a deliberately-messy, self-contained JS web app that serves as the **controlled
verify vehicle** for the extension→platform convergence. Instead of pointing the pipeline at a
random live site and eyeballing the output, this target's exact API surface and planted secrets
are documented up front in a machine-readable **answer key** (`answer-key.json`), so a run can be
scored found / missed / unexpected by `scripts/score.mjs`.

**This is the gate for the Phase 4 `apps/capture/{api,web}` deletion.** That deletion stays blocked
until the real MV3 extension drives the platform end-to-end against this target and the score is
green, on both bundler outputs. This README is the runbook for producing that score; it does not
cover the cutover itself.

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
a build is missing chunks or maps, `npm test`'s `build-invariants` check will catch it — see §5.

## 3. Capture with the real extension (real Chrome only)

The in-app/embedded browser can't drive an MV3 extension, so this step is done in a normal Chrome
window with the `js-security-extractor` extension loaded. The rest of the runbook (build, score) is
automatable; this step is not.

1. Start one bundler's server from §2 (e.g. `npm run serve:vite` → `http://localhost:4173`).
2. Open the extension popup → **Settings**:
   - **Workspace URL** = `http://localhost:8000` (the platform API).
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

## 5. Resolve the tenant UUID (one-time, out-of-band)

The scoring script's `X-Tenant-Id` header must be the tenant's **UUID**, not its name — the
platform 400s a non-UUID value. The capture tenant is named `capture-spike` but its UUID is
randomly generated at creation and no endpoint currently returns it, so resolve it once via `psql`:

```bash
docker compose -f apps/platform/docker-compose.yml exec -T db psql -U recon -d recon -tAc "select id from tenant where name='capture-spike'"
```

Copy the printed UUID; it's stable for the life of that tenant, so this is a once-per-environment
step, not a once-per-run step. (A fast-follow candidate — out of scope here — is a flag-gated
`GET /api/capture/tenant` endpoint so this lookup doesn't need manual `psql`.)

## 6. Score the run

```bash
npm run score -- --run <run_id> --tenant <tenant_uuid>
```

(Optional `--base http://localhost:8000` if the platform isn't on the default; `--key` to point at
a different answer key.) This fetches `GET /runs/{run_id}/findings` and diffs it against
`answer-key.json`, printing a JSON breakdown followed by `PASS` or `FAIL` (and a non-zero exit code
on `FAIL`).

**Reading the output:**
- `missedEndpoints` / `missedParams` / `secretMisses` / `covFail` — any of these non-empty means
  `FAIL`. Each is a real pipeline defect against the calibrated answer key, not a known limitation.
- `blindViolations` — lists any *known blind spot* (see §7) that unexpectedly resolved to a real
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
| `npm run score -- --run <id> --tenant <uuid>` | Score a run's findings against `answer-key.json` |
| `npm test` | Build both bundlers, then run every `scripts/*.test.mjs` (build invariants, answer-key consistency, planted-surface presence, scoring logic) |
