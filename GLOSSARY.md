# Glossary

Domain terms as this repo uses them. Use these names in code, issues, and docs; add a term when a
decision settles one (see `docs/agents/domain.md`).

## Capture session

A researcher's ongoing runtime-JS capture of one target in the browser extension, identified by a
**capture session id** (`sessionId`). It holds every **capture** of that target ("Continue" adds a
capture to it). The platform binds it to at most one **engagement**, immutably, so changing the
engagement means starting a new capture session, and a tenant change starts a fresh, unbound
(**standalone**) one. Owned by `CaptureSession`
(`apps/capture/chrome-extension/modules/capture-session.js`).

_Avoid_: "run" for the session (a session holds many).

## Capture

One stretch of capturing inside a capture session, from **Start capture** to **End capture** (or
the automatic end after a day without new files). It is exactly one platform run. "Analyze now"
analyzes a capture without ending it: files that arrive later join the same capture, and its
analysis updates. A capture that has been analyzed but not ended is **capturing**.

_Avoid_: "capture" for a single uploaded file (say "captured file"), "round".

## Capturing

A capture between analysis passes: its findings can be read, and more files may still arrive. It
is not finished. Only End (or the automatic end) finishes it, which the workspace shows once, as
Done.

## Session scope

The hosts a researcher declares as the target of one **capture session**, set in the popup when a
**capture** starts: what counts as "in
scope" for that session's hosts, findings and sources. Each session owns its own scope; an
**engagement** groups many sessions, each with its own scope, and has no scope of its own.

Scope decides by the **page first, then the host**: a tab outside scope is ignored whole; on an
in-scope page, a script is analyzed if its host is in scope, dropped if it is a known third party
or on the **ignore list**, and otherwise **quarantined** until the researcher decides (ADR-0010).
Editing scope afterwards never re-captures: removing a host hides its files and findings
(reversible), adding one brings its content in.

_Avoid_: "scope" alone for the analytics **noise hosts** filter or for the fetch guard's allowed
hosts (those are different rules), "engagement scope".

## Quarantine

Where a script from an **undecided host** waits: fetched and stored apart from the run's files
(its own table and blob prefix), so it is not analyzed and no findings, sources or hosts view can
see it. Capture and analysis carry on without it. **Add** promotes it into analysis; **Ignore**, or
seven days without a decision, deletes it. A quarantined file is a leaf: nothing it references is
fetched until Add.

_Avoid_: "pending" (that already means an asset not yet fetched or analyzed).

## Undecided host

A host that served script files to an in-scope page but is neither in the session's scope, on the
**ignore list**, nor a known third party. Listed for the researcher to Add or Ignore. A link to a
page on an unknown host is listed the same way, as "linked site, not crawled", and is never fetched.
A standard crawl lists unknown script hosts as "referenced, not fetched" and downloads them only
after Add, through the unchanged egress guard.

## Ignore list

The hosts a researcher chose not to add for one **capture session**. Their scripts are dropped and
never fetched again. It lives with the session, so Continue keeps it.

## Third-party rules

What decides that a script or file is someone else's code and is dropped at capture: a built-in
list of hosts and file patterns, plus the team's additions in workspace Settings (ADR-0010). A host
entry never drops a host the researcher typed into the scope list. Different from the **ignore
list**, which is per session and hosts only.

The team's additions live in the `tenant_third_party_rule` table (migration 0038), edited in Settings >
Third-party rules (`/settings/third-party-rules`). They only add: nothing switches a built-in entry off.
The platform merges built-in plus team at read time (`recon.findings.visibility`); the extension
downloads the merged list at sign-in and capture start and keeps its last saved copy.

_Avoid_: "denylist" (the retired popup editor), "noise rules".

## Standalone session

A capture session with no engagement binding ("Solo · standalone" in the popup). Logout and a
different-tenant login always rotate to one.

## App code

The target's own code: every source file that isn't **third-party code**. A bundle with no source
map can't be split, so its code is unclassified rather than assumed to be either.

_Avoid_: "first-party" (ambiguous with first-party hosts), "core".

## Third-party code

Code from packages the target depends on, recognised by its package path (`node_modules`, pnpm's
`.pnpm`, Yarn's `.yarn`) or by the source map's own ignore list. It is analyzed like app code but
hidden by default; a researcher can treat a package as app code for a session (a private
`@acme/*` package is the usual case). A third-party finding that points at an in-scope host stays
visible.

_Avoid_: "vendor" (already means analytics/CDN hosts in the noise filter), "dependency", "library
code".
