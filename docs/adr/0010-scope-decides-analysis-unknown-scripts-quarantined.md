---
status: "proposed"
date: 2026-11-15
# Amends (does not supersede) ADR-0005: one narrow exception to "scope is never derived from content".
---

# 10. Scope decides analysis: unknown script hosts are quarantined until the researcher decides

## Context and Problem Statement

A session's scope is what its capture treats as the target. Today the extension decides by one
question: is the **script's own host** in the scope list? It never asks which **page** loaded the
script. So an app whose JavaScript lives on a CDN or bucket (`cdn.acme-static.com`) is silently
dropped and the researcher must add the host, reload and re-browse. The browser-driven crawl drops
the same scripts. Analytics and unrelated sites are dropped only because they are out of scope.
Decided with the researcher on 2026-11-15 (#175, #176, walkthrough frictions F11 and F12).

## Considered Options

* **Gate only (today)** — drop anything whose host is not in scope. Fail-closed, but loses a real
  CDN's app code until a reload.
* **View filter only** — capture everything every tab loads and filter at read time. Never misses
  a script, but stores other sites' code, including other tenants'.
* **Page-aware rule plus quarantine** — decide by the page first, then the host; an unknown host's
  scripts are kept aside, unanalyzed, until the researcher decides.

## Decision Outcome

Chosen option: **page-aware rule plus quarantine**. For each script, in order:

1. **Page outside the session's scope** — ignore the whole tab (unrelated sites are never captured).
2. **Known analytics, ad, payment widget or public library host** — drop; never stored.
3. **Host in the session's scope list** — capture and analyze.
4. **Host on the session's ignore list** — drop; not fetched.
5. **Otherwise (unknown host)** — **quarantine**: fetch and store, do not analyze, keep out of
   findings, list it as an **undecided host**. Capture and analysis never wait on it.

The researcher decides, in the popup or on the Hosts page:

* **Add** — the host joins the session scope (so future content is already in scope), its
  quarantined scripts are analyzed, and their findings appear.
* **Ignore** — its quarantined content is deleted, the host goes on the ignore list, and its
  future scripts are never fetched.
* **No decision for 7 days** — the content and its list entry are deleted. A per-host size and
  count cap applies.

Pages on unknown hosts are never fetched. A link from an in-scope page to one is listed as a
"linked site, not crawled" with Add; once added it is an ordinary scope host.

Editing scope later is a read-time rule, never a re-capture. Removing a host **hides** its files
and findings under "Hidden: out of scope" (reversible, like third-party code); deleting them is a
separate, explicit action.

### Exception to ADR-0005 (needs adversarial design review before build)

ADR-0005 and REQ-P2 say the host set is declared, **never derived from crawled or bundle
content**. This ADR carves one narrow exception for the **server crawl**: it may download
**script files only**, referenced by in-scope pages on unknown **public** hosts, into quarantine.
All other guard checks stay: http/https only, every resolved IP globally routable, DNS pinning,
manual redirects through the guard, size and time caps. The extension path needs no exception (the
browser already loaded the script). The residual risk is that a hostile target can make the
platform issue GET requests for script URLs of its choosing on public hosts, bounded by the caps.

**Fallback if the review rejects the exception:** the crawl only lists unknown script hosts as
"referenced, not fetched" and downloads them after Add; the extension path is unchanged.

### Consequences

* Good — a CDN or bucket that serves the app's code is captured without a reload; one click
  decides it.
* Good — unrelated sites and known third parties are never analyzed; nothing unknown reaches
  findings before the researcher says so.
* Bad — unknown hosts' scripts are stored (briefly, unanalyzed) before approval; mitigated by
  quarantine, the 7-day deletion and the caps.
* Bad — a documented invariant of ADR-0005 is weakened for the crawl until the review concludes.

### Confirmation

Not yet built. When it is: the ordered rule is one pure function with a test per rule; the
quarantine state is enforced by the analyze stage skipping it and by the findings, sources and
hosts read models excluding it; the retention sweep and caps have tests; the crawl exception has
its own egress tests (`fetch/egress_test.py`, `discover/crawl.py` re-validation) showing every
non-scope check still blocks.

## More Information

Tickets #175 and #176 (resolution comments), #178 (scope set per capture in the popup), #174 (map).
Related: ADR-0005 (egress guard), ADR-0009 (runtime capture), REQ-P2, GLOSSARY "Session scope",
"Quarantine", "Undecided host", "Ignore list". Evidence: researcher walkthrough frictions F11 (scope
not visible in the workspace) and F12 (sessions indistinguishable).
