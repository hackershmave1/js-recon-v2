---
status: "accepted"
date: 2026-11-15
# Adversarial design review 2026-11-15: the crawl exception to ADR-0005 / REQ-P2 was REJECTED.
# This ADR leaves ADR-0005, ADR-0006 and REQ-P2 untouched.
---

# 10. Scope decides analysis: unknown script hosts are quarantined until the researcher decides

## Context and Problem Statement

A session's scope is what its capture treats as the target. Today the extension decides by one
question: is the **script's own host** in the scope list? It does not use the loading page. So an
app whose JavaScript lives on a CDN or bucket (`cdn.acme-static.com`) is dropped, and the
researcher must add the host, reload and re-browse. A standard katana crawl never requests such
scripts at all (it follows only `-crawl-scope` hosts); the headless/capture crawl fetches them and
then discards them (`capture/stage.py`). Decided with the researcher on 2026-11-15 (#175, #176,
walkthrough frictions F11 and F12).

## Considered Options

* **Gate only (today)** — drop anything whose host is not in scope; loses a real CDN's app code.
* **View filter only** — capture everything every tab loads; stores other sites' code.
* **Page-aware rule plus quarantine** — decide by the page first, then the host; an unknown host's
  scripts are kept aside, unanalyzed, until the researcher decides.

## Decision Outcome

Chosen option: **page-aware rule plus quarantine**. For each script, in order:

1. **Page outside the session's scope** — ignore the tab. "Page" means the **top-frame URL read
   from the tab at admission** (`chrome.tabs.get(tabId)`), never a page-supplied value. A request
   whose page cannot be determined (worker, `tabId` -1) is not quarantined.
2. **Known analytics, ad, payment widget or public library host** — drop; never stored.
3. **Host in the session's scope list** — capture and analyze.
4. **Host on the session's ignore list** — drop; not fetched. The server enforces the list on
   ingest, so a replayed batch cannot re-store it.
5. **Unknown host** — **quarantine**. Capture and analysis never wait on it.

**Quarantine is stored apart** from the run: a separate table and blob prefix, never a status on
`run_asset`. Every reader of run assets (findings, sources, hosts, coverage, completeness, tech
detection, search, export) is therefore unable to see it. **Add** promotes it by inserting normal
rows and blobs; **Ignore** and expiry delete it. A quarantined file is a **leaf**: no source map,
dependency, chunk or ESM follow-up until Add.

The researcher decides in the popup or on the Hosts page:

* **Add** — the host joins the session scope (so future content is already in scope), its
  quarantined scripts are promoted into analysis, and their findings appear. Add widens the
  session's scope, so the dialog says so and it is never automatic.
* **Ignore** — its quarantined content is deleted, the host goes on the ignore list, and its
  future scripts are never fetched.
* **No decision for 7 days** — content and list entry are deleted.

Each `(session, host)` has one decision record, changed by compare-and-set:
`undecided -> added | ignored | expired`, so Add, Ignore and the sweep cannot race.

**Caps** (values set in the build design): hosts per session (the extension already bounds its
hint list at 50), total quarantine bytes per session, and per-host size and count.

**Pages on unknown hosts** are never fetched; a link to one is listed as "linked site, not
crawled" with Add, and once added it is an ordinary scope host.

**Editing scope later** is a read-time rule, never a re-capture. Removing a host **hides** its
files and findings under "Hidden: out of scope" (reversible, like third-party code); deleting is a
separate, explicit action.

### The crawl: no exception

The standard crawl **lists** unknown script hosts as "referenced, not fetched". The headless and
runtime-capture crawl **quarantines** what its browser already loaded (no new server egress).
When the researcher presses Add, the host joins `session.scope_hosts` and the **unchanged** guard
downloads the scripts in one fetch pass. `fetch/egress.py` and ADR-0005 / REQ-P2 are untouched.

### Consequences

* Good — CDN-hosted app code is analyzed after one click: instantly for the extension and
  headless capture, after one fetch pass for the standard crawl.
* Good — unrelated sites and known third parties never reach findings; nothing unknown is
  analyzed before the researcher says so; the server never downloads from an unapproved host.
* Bad — unknown hosts' scripts are stored, briefly and unanalyzed, before approval (extension and
  headless capture only), bounded by the 7-day expiry and the caps.
* Bad — a crawl user waits one fetch pass after Add.

### Confirmation

Not built yet. When it is, the ordered rule is one pure function with a test per rule, and these
are enforced and tested:

* quarantine lives in its own table and blob prefix, and a test asserts no existing reader
  returns it (including the run completeness check and tech detection);
* delete is **reference-counted within `(tenant, run)`**: blobs are content-addressed
  (`storage.object_key`), so a blob, its `formatted/` and `originals/` derivatives and its map are
  removed only when no other asset references that digest;
* the extension fetches unknown-host scripts with `credentials: 'omit'` (today
  `content-fetcher.js` uses `include`) and does not follow their dependencies or maps;
* `documentUrl` is not used (Chrome's `webRequest` does not provide it); the page comes from
  `tabs.get`, with tests for cross-origin iframes and `tabId` -1;
* the decision record's compare-and-set, ingest-side ignore list, and Add after the capture has
  ended (a sealed run returns 409 `capture_ended`, so promotion needs its own pass) have tests;
* `fetch/egress_test.py` is unchanged and still passes.

## More Information

Tickets #175 and #176, #178 (scope set per capture in the popup), #174 (map). Related: ADR-0004
(content-addressed blobs), ADR-0005 (egress guard), ADR-0009 (runtime capture), REQ-P2, GLOSSARY
"Session scope", "Quarantine", "Undecided host", "Ignore list". Evidence: walkthrough frictions F11
and F12.

**Review outcome (2026-11-15).** The first version of this ADR proposed an exception letting the
server crawl download script files from unknown public hosts. The adversarial review rejected it:
the guard does not restrict ports (a hostile page could point it at a public Redis or
Elasticsearch port); the scope check is shared by every fetch, so a relaxation is global and
follows source maps and chunks onto more unknown hosts; a page can list thousands of hosts; and the
need is met without it (above). Its other findings are folded in: separate storage, refcounted
delete, leaf-only quarantine, credential-less fetch, the top-frame rule, the decision record and
the caps. **Still to verify during the build:** the shape of katana's `-display-out-scope` output
(needed to list unknown script hosts), and the Chrome `webRequest` field semantics against the
current docs.
