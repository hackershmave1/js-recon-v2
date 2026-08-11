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
downloads — without breaking the SSRF, single-thread-worker, and idempotency invariants,
and while being honest about the "no active traffic" posture (ADR 0006).

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
under the page). Captured scripts are written as the existing capture asset contract
(`run_asset` `input` blobs + a `discover.assets` event), so downstream is unchanged.
**Default-off** behind `RECON_ENABLE_CAPTURE_MODE`.

Capture drives a real (headless) browser at the target — a **deliberate, gated relaxation of
the static / no-active-traffic posture (ADR 0006)**, not a reversal: it runs only against an
in-scope, authorized (session `authorization_ack`), egress-guarded target, and is off by
default.

### Consequences

* Good — recovers inline / injected / eval'd / worker / service-worker JS the static path
  cannot; FETCH and ANALYZE are unchanged.
* Good — the sync raw-CDP transport is small and matches the repo ethos; the single-thread
  worker keeps its lease heartbeat through every wait loop and the blob-seeding pass.
* Bad / accepted — the browser resolves the host and loads subresources with no per-hop IP
  pin: an SSRF widening vs the static crawl. Mitigated by default-off + pre-launch seed
  validation + per-script scope re-validation; OS-level egress isolation is deferred (the
  egress-proxy slice).
* Neutral (protocol gotchas, encoded in code so they can't silently regress) — browser-level
  auto-attach needs **two** filters because Chromium rejects a filter allowing both `tab` and
  `page`; replies are matched by **bare id** because Chrome may omit `sessionId`; a new target
  is `Debugger.enable`d **before** it is released so its first `scriptParsed` is not missed.

### Confirmation

`recon/capture/cdp.py` — transport: `CdpSession` (one global id counter, bare-id reply
matching) and `ROOT_AUTO_ATTACH_PARAMS` / `CHILD_AUTO_ATTACH_PARAMS` (the two filters).
`recon/capture/driver.py` — browser-level waterfall, enable-before-release, bounded per-fetch
timeout, `killpg`. `recon/capture/stage.py` — `authorization_ack` gate + pre-launch
`egress.validate_target` + per-script `_in_scope` + atomic manifest + heartbeat-during-seeding.
Default-off `config.py` `enable_capture_mode` + the API gate in `api/runs_router.py`. Tests:
`recon/capture/*_test.py` (host lane) and `driver_integration_test.py` (real Chromium — C1/C2/C16
page + C7 worker + C8 service worker in one launch).

## More Information

Requirements: REQ-P2 (executed-script capture), REQ-P3 (authorization before active work).
Shipped as slice 1 on branch `feat/runtime-capture-stage` (PR #35) and slice 2 on
`feat/capture-workers-ui` (PR #36) — **unmerged as of 2026-08-11**. Relates to ADR 0005 (SSRF
egress guard, reused), ADR 0006 (static / no-active-traffic, deliberately relaxed here under
gates), ADR 0008 (process-group kill of headless-browser children). Follow-ups: an interaction
driver, source-map recovery, and a managed vehicle for 403-walled targets.
