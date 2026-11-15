# Research workflow: session scope, undecided hosts and a capture-only extension

Status: agreed 2026-11-15 (map #174: #175, #176, #177, #178, #179). Placeholders only: `acme.io`,
`cdn.acme-static.com`. AI explain and AI triage are **out of scope**: they are a deferred plan
(`plans/2026-11-15-ai-explain-and-triage-deferred.md`).

## Problem

The researcher walkthrough (#177) found 20 frictions; 11 are fixed. The rest share a cause: scope is
decided on the wrong thing and shown in the wrong place.

1. An app whose JavaScript lives on a CDN or bucket is **silently dropped**, because the extension
   asks only whether the script's own host is in scope, never which page loaded it.
2. Scope is **invisible** in the workspace, and session cards cannot be told apart (F11, F12).
3. The popup carries results and settings that belong in the workspace (F07, F08, F09, F20).
4. Opening the workspace from the popup asks you to sign in again (F04).
5. A crawl in the workspace and a capture of the same site end up as two sessions (#158).

## What the researcher gets

| Moment | Before | After |
|---|---|---|
| App JS on a CDN | Dropped; add the host, reload, re-browse | Held as an **undecided host**; capture and analysis carry on; **Add** analyzes it in one click, with no reload |
| Unrelated site in another tab | Dropped by accident | Ignored whole, by rule |
| Library file on the app's own host (`jquery.min.js`) | Dropped by the popup's default profile | Dropped by the built-in **third-party rules**, which the team can extend in workspace Settings |
| Where is my scope? | Not shown in the workspace | Hosts page: scope list, undecided hosts, ignore list, hidden hosts; session cards show their scope |
| Scope was wrong | Re-capture | Edit it: adding brings content in, removing **hides** (reversible) |
| Popup | Capture, results, export, LLM key, denylist editor | Sign-in and capture only |
| Open in workspace | A second sign-in | Signed in (after its own security review) |

## Rules (decided; see ADR-0010 for the reasoning and the review)

Per script, in order: page outside scope is ignored; known third-party host or file is dropped;
host in scope is analyzed; host on the session's ignore list is dropped; anything else is
**quarantined** (stored apart, not analyzed, not in findings) until the researcher chooses **Add**
(host joins scope, content is analyzed) or **Ignore** (content deleted, host never fetched again).
No decision for 7 days deletes it. The server crawl is **not** exempted from the egress guard: a
standard crawl lists unknown script hosts and downloads them only after Add.

## Popup after this spec

* **Stays:** sign-in and workspace URL; scope, Continue and engagement; Start, Pause, Resume, End;
  Analyze with progress; delivery health; undecided hosts; capture settings (capture every tab,
  response bodies, max asset size, auth context).
* **Moves to the workspace:** findings card becomes a link; recent captures becomes Sources; export;
  LLM provider and key (Team LLM provider); clear captured files becomes delete session.
* **Deleted:** denylist editor, Tag/Mute/Exclude, "Mute plugins & trackers". Deleting the editor
  waits until the built-in list and the team additions exist.

## Slices and their order

Each slice is one ticket; each names the frictions it closes. `->` means "blocks".

```
Quick wins (no blockers):  Q1 port allowlist   Q2 engagement has no scope   Q3 hide empty Tech tab
                           Q4 scope on session cards   Q5 popup: move results + settings out
Scope build:               S1 server quarantine -> S2 extension , S3 workspace Hosts page
Third-party rules:         R1 built-in list -> R2 team additions (also needs S3)
Cleanup:                   Q6 popup: delete denylist editor (needs R1, R2, S2)
Reviews first, then build: H1 handoff ADR+review -> H2 handoff    C1 adopt ADR+review -> C2 adopt (needs S1)
Crawl:                     K1 list unknown script hosts, fetch after Add (needs S1, S3)
Later:                     L1 Overview/Sources counts   L2 finding detail baseline   P1 rank DEBT.md
```

Tickets: Q1 #206 · Q2 #207 · Q3 #208 · Q4 #209 · Q5 #219 · Q6 #221 · S1 #210 · S2 #217 · S3 #218 · R1 #211 · R2 #220 · H1 #212 · H2 #222 · C1 #213 · C2 #223 · K1 #224 · L1 #215 · L2 #216 · P1 #214.

| Slice | Closes | Notes |
|---|---|---|
| Q1 | review finding | The egress guard has no port check; allow only 80 and 443 |
| Q2 | F06 | Workspace New engagement form and switcher stop showing a scope |
| Q3 | F18 | Hide the tab and the Overview tile when nothing was collected |
| Q4 | F12 | Each card shows its scope; an empty session is labelled, not "—" |
| Q5 | F07, F08, F09, F20 | Replaces the popup's findings card with a link, moves export, key, clear |
| S1 | foundation | Separate table and blob prefix (never a status on `run_asset`), per-(session, host) decision record with compare-and-set, ignore list enforced on ingest, 7-day sweep, caps, promote, **reference-counted delete** |
| S2 | CDN app JS | Page-aware rule from `tabs.get`; quarantine upload without credentials; quarantined files are leaves; undecided list with Add and Ignore in the popup |
| S3 | F11 | Hosts page; read-time re-labelling; removing a host hides, with a separate delete |
| R1 | third-party files | Hosts and file patterns, one shared source for extension and platform, parity test |
| R2 | custom rules | Tenant table and API, Settings UI, extension sync, "Ignore for all my sessions" |
| Q6 | F09 | After R1, R2 and S2 |
| H1, H2 | F04 | Single-use code in the URL fragment, about 30 seconds, exchanged once |
| C1, C2 | #158 | Continue gives a workspace session an extension id; signed-in, same tenant only |
| K1 | crawl gap | Verify the shape of katana's out-of-scope output first |
| L1 | F14 | Overview and Sources counts agree or say why |
| L2 | F16 | Kind, confidence, severity reason and next steps from data the platform already has (no AI) |
| P1 | none | Rank the open `DEBT.md` items into this list |

## Out of scope

AI explain and AI triage; an engagement-wide merged findings view (open item on the map); detection
of third-party code in bundles without a source map; F01 (loading the extension needs a build step)
and F13 (deliberate).

## Verification

Every slice: tests first, the repo's CI lanes, a code review, and the real-browser walkthrough run
against an isolated stack with the real worker image (the same script used for the walkthrough), so
each slice's effect on the researcher is seen, not assumed. S1, S2, H2 and C2 also need the
adversarial design review's conditions from ADR-0010 turned into tests (no existing reader can see
quarantine; delete is reference-counted; replay after Ignore cannot re-store).

## Still to verify during the build

The shape of katana's `-display-out-scope` output (K1) and Chrome's `webRequest` field semantics
against the current docs (S2).
