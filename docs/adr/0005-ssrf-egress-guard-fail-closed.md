---
status: accepted
date: 2026-08-08
---

# 5. SSRF egress guard, fail-closed

## Context and Problem Statement

The one place the platform makes outbound requests is fetching a target's JavaScript
(crawl seed + katana-discovered URLs + declared assets). That fetch is a classic SSRF
sink: a malicious target could point it at `169.254.169.254`, an internal host, or use
DNS rebinding. What may be fetched must be tightly and safely constrained (REQ-P2 / RT-02;
see `docs/REQUIREMENTS.md`).

## Considered Options

* **Fail-closed allow-list guard** — scheme + a pre-declared host set + every resolved IP
  must be globally routable, with DNS pinning; any ambiguity blocks.
* **Blocklist** — allow anything except known-bad ranges.
* **Trust the crawler** — let katana / Sourcemapper fetch whatever they discover.

## Decision Outcome

Chosen option: a **fail-closed egress guard** (`fetch/egress.py`). Policy: the scheme must
be http/https; the host must be in `session.scope_hosts` (declared, **never** derived from
crawled/bundle content); every DNS-resolved IP must be globally routable (loopback,
RFC1918, link-local, CGNAT, NAT64, and cloud-metadata ranges are blocked). DNS is pinned
and re-validated per hop, and redirects are followed manually through the same guard.
Every failure path blocks rather than proceeds.

### Consequences

* Good — SSRF via crawled / source-map URLs is defeated: scope is never widened by
  content, and a single non-public resolved IP raises `EgressBlocked` (`egress.py:266-268`).
* Good — fail-closed everywhere: a DNS failure is an egress *block*, not an uncaught error
  (`egress.py:253-262`); an invalid scope entry is dropped so a malformed `scope_hosts`
  can't accidentally widen egress (`egress.py:93-123`).
* Bad — **accepted residual**: there is no OS/network-level egress isolation, and katana
  does its own unpinned DNS mid-crawl, so a rebinding window remains that the app-level pin
  can't fully close (`egress.py:11-14`, `crawl.py:9-11`; tracked as DEBT D18).
* Neutral — `EgressBlocked` is a deterministic `FatalError`, not retried
  (`fetch/fetch.py:229-231`), so a blocked host fails fast instead of burning the budget.

### Confirmation

`fetch/egress.py` (policy docstring L1-15; `is_valid_scope_entry` L93-123; `host_in_scope`
L160-174; `is_public_ip` L177-217; `validate_target` L220-269) with `egress_test.py`. Fetch
wiring + DNS pin `fetch/fetch.py:70-139,229-231`. Crawl seed + emitted-URL re-validation
`discover/crawl.py:54-68,120-128`. Requirement REQ-P2 / RT-02.

## More Information

Recorded retroactively 2026-08-08 (DEBT D10). This is the mechanism that constrains the
one narrow egress allowed under the product's no-active-traffic stance (ADR-0006). See
`apps/platform/README.md` (egress) and `docs/ARCHITECTURE.md`.
