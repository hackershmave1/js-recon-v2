# Glossary

Domain terms as this repo uses them. Use these names in code, issues, and docs; add a term when a
decision settles one (see `docs/agents/domain.md`).

## Capture session

One run of runtime JS capture in the browser extension, identified by a **capture session id**
(`sessionId`). Everything captured under that id — files, dedup set, API-call observations,
counters — belongs to it, and the platform binds the session to at most one **engagement**,
immutably. So changing the engagement means starting a new capture session, and a tenant change
starts a fresh, unbound (**standalone**) one. Owned by `CaptureSession`
(`apps/capture/chrome-extension/modules/capture-session.js`).

_Avoid_: "capture" alone for the session (a capture is one file), "run" (that's the platform's
analysis run the session's uploads accumulate into).

## Session scope

The hosts a researcher declares as the target of one **capture session**: what counts as "in
scope" for that session's hosts, findings and sources. Each session owns its own scope; an
**engagement** groups many sessions, each with its own scope, and has no scope of its own.

_Avoid_: "scope" alone for the analytics **noise hosts** filter or for the fetch guard's allowed
hosts (those are different rules), "engagement scope".

## Standalone session

A capture session with no engagement binding ("Solo · standalone" in the popup). Logout and a
different-tenant login always rotate to one.
