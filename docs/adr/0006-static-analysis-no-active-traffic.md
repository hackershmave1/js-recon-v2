---
status: accepted
date: 2026-08-08
---

# 6. Static analysis, no automated active/exploit traffic

## Context and Problem Statement

This is a security-recon tool that reconstructs a target's API surface from its
JavaScript. A core product/safety stance must be fixed: does the platform *itself* send
active or exploit traffic at the target, or does it stay static and hand the operator a
ready-to-fire artifact to run manually (REQ-P1/P2/P3; see
`docs/REQUIREMENTS.md`)?

## Considered Options

* **Static analysis, no automated active/exploit traffic** — reconstruct requests; the
  user fires probes manually.
* **Active scanning** — the platform sends the reconstructed requests / exploit payloads
  itself.

## Decision Outcome

Chosen option: **static analysis, no automated active/exploit traffic.** The platform
reconstructs the request and hands the user a ready-to-fire artifact (an OpenAPI export, a
manual-probe request); the **user** runs the probe. The only outbound traffic the platform
itself makes is the SSRF-guarded fetch/crawl of in-scope JS assets (ADR-0005) — it does
not send the reconstructed requests at the target automatically. Analysis is "honest by
construction": a sink whose URL isn't statically resolvable is counted as *unattributed*,
never invented, and a missing engine degrades coverage honestly rather than reporting a
false "clean".

### Consequences

* Good — the platform can be pointed at a third party's app for recon without emitting
  attack traffic; exploitation stays a deliberate, manual, human-in-the-loop step.
* Good — outputs are **static exports** the user drives: OpenAPI today
  (`api/export_router.py`, `probe/openapi.py`); a served/active API is explicitly *not*
  offered. GraphQL SDL / Swagger 2.0 / gRPC are deferred *export* formats, never a live
  GraphQL endpoint.
* Neutral — static analysis cannot observe responses, so the OpenAPI spec asserts no auth
  and no response shapes ("Not observed — static analysis does not capture responses",
  `probe/openapi.py:93,166-169`).
* Bad — this bounds recall: runtime-only behaviour is invisible to a static pass. Closing
  that gap via **runtime-evidence ingest** (e.g. Burp/HAR) would *deliberately relax* this
  stance and would be a conscious future decision, not a silent drift. Note the capture
  extension already supplies runtime-*captured JS* — still static analysis of that JS, which
  is distinct from active traffic.

### Confirmation

REQ-P1/P2/P3. The API tier does no fetch/parse/probe/LLM work (`api/app.py:1-5`). Honesty
strings `probe/openapi.py:3,93,166-169`. Manual-probe is user-initiated
(`api/probe_router.py`). The one allowed egress is guarded by ADR-0005.

## More Information

Recorded retroactively 2026-08-08 (DEBT D10). **Scope note:** "no active traffic" means no
*automated active/exploit* traffic — the platform does fetch JS assets (SSRF-guarded,
ADR-0005) and does ship a user-initiated manual-probe artifact. This stance is re-asserted
across subsequent slices. See `docs/ARCHITECTURE.md`.
