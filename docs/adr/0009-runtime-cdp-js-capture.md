---
status: accepted
date: 2026-08-11
---

# 9. Runtime JS capture via in-process CDP (whole target tree)

## Context and Problem Statement

The static pipeline reconstructs a target's API surface from the JS bytes that cross the
network (`recon.fetch` → engines). A growing share of that surface is invisible to it:
inline `<script>` blocks, runtime-injected scripts, `eval` / `new Function` code (whose
source is in no served byte), and logic that runs in Web Workers or Service Workers. To
recover these we must observe what the page's V8 actually *executes*, not just what it
downloads — without breaking the SSRF, single-thread-worker, and idempotency invariants.
Driving a real browser relaxes the *static-only fetch* posture and widens the egress
footprint; it sends no automated exploit traffic, so it does not touch ADR 0006's
no-active-*exploit* stance (driving the app's own controls is using it, not attacking it).

## Decision Drivers

* Completeness: reach eval'd/injected code (Coverage row C16), dedicated/shared workers
  (C7), and service workers (C8).
* Reuse the existing capture asset contract so FETCH and ANALYZE stay unchanged.
* Preserve load-bearing invariants: SSRF fail-closed (ADR 0005), the single-thread worker
  heartbeat, content-addressed idempotency, process-group kill (ADR 0008).
* Minimal dependency footprint.

## Considered Options

* **VM-level CDP capture** — `Debugger.scriptParsed` + `Debugger.getScriptSource` over a
  raw websocket.
* **Network / MITM capture** — record response bodies. Provably incomplete: eval'd code is
  in no served byte, and service-worker scripts are missed by `Network.getResponseBody`.
* **Playwright `CDPSession`** — wraps the worker/SW handshake, but heavy and version-coupled
  to the system Chromium.

## Decision Outcome

Chosen: **VM-level CDP over `websockets.sync`** — it matches the blocking single-thread
worker with no asyncio bridge and adds no heavy dependency. A per-run `crawl_mode="capture"`
routes the DISCOVER stage to the capture stage. Slice 1 attaches to the page target; slice 2
connects at the **browser** target and waterfalls `Target.setAutoAttach{flatten}` to capture
the page plus workers (C7) and service workers (C8, which attach at the browser level, not
under the page). Slice 3 adds an **interaction driver**: once the initial load settles it
autoscrolls, clicks every interactive element, and walks same-origin routes so lazily-loaded /
route-split / click-gated chunks execute and are captured. Because it navigates repeatedly and a
navigation destroys the prior document's V8 context, each source is fetched **on-parse** (the
instant `scriptParsed` fires) through one unified event pump, not in a deferred pass. Captured
scripts are written as the existing capture asset contract (`run_asset` `input` blobs + a
`discover.assets` event), so downstream is unchanged. **Default-off** behind
`RECON_ENABLE_CAPTURE_MODE`.

Capture drives a real (headless) browser at the target — a **deliberate, gated relaxation of
the *static-only fetch* posture**, not of ADR 0006's no-automated-*exploit* stance (capture
sends no exploit traffic). It runs only against an in-scope, authorized (session
`authorization_ack`), egress-guarded target, and is off by default. The interaction driver
*widens the egress footprint* (it follows the app's own navigations/requests and walks routes),
which is why request-layer egress interception — blocking off-scope requests before they are
sent — is a tracked follow-up (the egress-proxy slice), accepted as a residual for the local,
single-operator use this runs in today.

### Consequences

* Good — recovers inline / injected / eval'd / worker / service-worker JS the static path
  cannot; FETCH and ANALYZE are unchanged.
* Good — the sync raw-CDP transport is small and matches the repo ethos; the single-thread
  worker keeps its lease heartbeat through every wait loop and the blob-seeding pass.
* Bad / accepted — the browser resolves the host and loads subresources with no per-hop IP
  pin: an SSRF widening vs the static crawl, amplified by the interaction driver (it follows the
  app's own links/forms and walks routes). Mitigated by default-off + pre-launch seed validation
  + same-origin route/click scoping + per-script scope re-validation; **request-layer egress
  interception** and OS-level egress isolation are deferred (the egress-proxy slice), accepted as
  a residual for local single-operator use.
* Neutral (protocol gotchas, encoded in code so they can't silently regress) — browser-level
  auto-attach needs **two** filters because Chromium rejects a filter allowing both `tab` and
  `page`; replies are matched by **bare id** because Chrome may omit `sessionId`; a new target
  is `Debugger.enable`d **before** it is released so its first `scriptParsed` is not missed.

### Confirmation

`recon/capture/cdp.py` — transport: `CdpSession` (one global id counter, bare-id reply
matching) and `ROOT_AUTO_ATTACH_PARAMS` / `CHILD_AUTO_ATTACH_PARAMS` (the two filters).
`recon/capture/driver.py` — the browser-level waterfall, enable-before-release, bounded
per-fetch/per-eval timeout, `killpg`, and the `_Ctx` event pump that fetches each source
on-parse. `recon/capture/interaction.py` — the autoscroll / click-all / same-origin route-enum
actions driven through that pump. `recon/capture/stage.py` — `authorization_ack` gate + pre-launch
`egress.validate_target` + per-script `_in_scope` + atomic manifest + heartbeat-during-seeding +
external source-map recovery (`_augment_with_source_maps` / `_fetch_captured_source_map`: the
`scriptParsed` `sourceMapURL` → guarded `fetch_url` GET → `run_asset.source_map_ref`, a soft miss
that never fails the run, gated by `crawl_fetch_source_maps`).
Default-off `config.py` `enable_capture_mode` (+ `capture_interact` and the `capture_max_*` bounds)
+ the API gate in `api/runs_router.py`. Tests: `recon/capture/*_test.py` (host lane — incl.
`interaction_test.py` orchestration + a `driver_test.py` eager-fetch-survives-navigation guard) and
`driver_integration_test.py` (real Chromium — C1/C2/C16 page + C7 worker + C8 service worker in one
launch, and a driven capture reaching scroll / route / click-gated chunks a passive capture misses).
Source-map recovery: `stage_test.py` (host lane — external `.map` linked, inline `data:` skipped,
crafted `sourceMapURL` soft-misses without aborting the run, cancel-not-swallowed) +
`runs/assets_integration_test.py` (the `seed_captured` INSERT persists `source_map_ref`).

## More Information

Requirements: REQ-P2 (executed-script capture), REQ-P3 (authorization before active work),
REQ-C3 (runtime host resolution).
Shipped as slice 1 (PR #35) and slice 2 (PR #36), **merged to `main` 2026-08-11**; slice 3 (interaction driver) merged as PR #38. Relates to ADR 0005 (SSRF egress guard,
reused), ADR 0006 (static analysis, no automated *exploit* traffic — capture relaxes the
*static-only fetch* posture, not that exploit stance), ADR 0008 (process-group kill of
headless-browser children). Follow-ups: request-layer egress interception (the egress-proxy
slice), a managed vehicle for 403-walled targets, and runtime request capture for host/base-URL
resolution (shipped; elaborated below). Source-map recovery for captured bundles has
shipped — external `.map` files are fetched through the egress guard and linked on the asset, and
inline `data:` maps already recover from the source itself; a no-map bundle is now beautified before
endpoint extraction (`recon/findings/deobfuscate.py` — Phase 1: `jsbeautifier`, fail-soft, 1 MiB cap)
so findings get real line numbers, while deeper per-module unpacking (webcrack, Phase 2) remains a
follow-up.

**Runtime request capture for host/base-URL resolution (REQ-C3, shipped).** Capture read only the
Debugger domain; the Network domain — set aside as a *completeness* layer (it sees bytes, not eval'd
code) — is now also subscribed per session (`Network.requestWillBeSent`) to record the browser's
actually-issued XHR/fetch URLs (path-only so query-string tokens are never custodied, in-scope, deduped,
capped). A real CORRELATE stage (`recon/correlate/`) matches each observed URL to the host-less static
endpoint findings on their shared constant path segments and attaches the observed URL as a
capture-provenanced `FindingOccurrence` — so a route whose host was an unresolved runtime value (e.g. a
minified `d.A.apiHost`) surfaces its concrete URL in reconstruct/spec/export, and the REQ-C2 host-gate
treats the op as observed-absolute. It is ground-truth evidence, not a re-based guess: recorded for
labeling only, it never derives or widens egress scope (REQ-P2's "scope is never derived from observed
URLs" holds), never churns `finding_hash` (host/raw_url are off the hashed identity), and is default-off
(`RECON_ENABLE_CAPTURE_MODE`). Confirmation: `recon/capture/driver.py` (`_on_request` + per-session
`Network.enable`), `recon/capture/stage.py` (`_requests_in_scope` + the `requests_ref` blob on the
`discover.assets` event), `recon/correlate/{match,stage}.py` (the constant-segment matcher + the
CORRELATE-stage writer), `recon/probe/reconstruct.py` (`_pick_example_url` prefers the capture URL),
`recon/spec/service.py` (classify strips a leading `${var}` once its base is capture-resolved).
