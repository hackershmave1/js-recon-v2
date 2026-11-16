---
status: "accepted"
date: 2026-11-16
# Adversarial review 2026-11-16: ACCEPTED WITH CONDITIONS (C1-C5 folded in below).
# Accepted 2026-11-16: the owner approved this design, with the review conditions below and the always-click "Continue as X" step kept (silent sign-in was rejected).
---

# 11. A single-use handoff code lets "Open in workspace" sign the researcher in

## Context and Problem Statement

The researcher signs in to the capture extension, then clicks **Open in workspace**. The workspace
is a different origin with its own login (the SPA keeps its session in `localStorage`,
`recon.authToken`, `apps/platform/web/src/auth/AuthProvider.tsx`), so it asks for the password
again (friction F04, #212). The extension already holds a valid login token for the same user and
tenant. The question: how does the workspace learn who the researcher is without putting a
long-lived credential somewhere it can leak (URL, history, logs), and without opening a new way in?

Facts the design rests on (all in this repo):

* The login token is a stateless HMAC `typ:"auth"` token (`recon/auth/token.py`) with **no server-side
  revocation**; rotating `RECON_AUTH_SECRET` is the only "revoke all". Lifetime is 8 hours
  (`auth_token_ttl_seconds`, `config.py`).
* `POST /auth/login` is the only credential-reading route and is throttled in Redis
  (`recon/auth/login_rate_limit.py`).
* The app has no CORS middleware (`recon/api/app.py`), so a web page on another origin cannot read
  responses; credentials travel as a `Bearer` header, never a cookie, so there is no ambient
  authority for a page to ride.
* Redis (7, append-only) is already a required dependency (`docker-compose.yml`).

## Decision Drivers

* A URL is copied, logged, synced and shown. A long-lived bearer must never be in one.
* No new way to *gain* access: the code must only move an identity the extension already proves.
* Fail closed: any doubt means the normal login screen, never a degraded sign-in.
* Smallest thing that works (no new table, no new dependency).

## Considered Options

* **A. Put the 8-hour token in the URL fragment.** No server change. Rejected: a fragment is kept in
  history, session restore and the address bar for the whole token life, and a pasted URL is a
  working login for 8 hours.
* **B. Code in the query string.** Rejected: sent to the server in the request line, so it lands in
  access logs, proxies and `Referer`.
* **C. Extension injects the token into the workspace page** (content script / `externally_connectable`).
  Rejected: the workspace URL is user-configured, so the extension would need broad host access or a
  static manifest origin that cannot match; both widen the extension's reach.
* **D. Server cookie.** Rejected: the SPA uses Bearer headers; a cookie would add ambient authority
  and a CSRF surface the app does not have today.
* **E. Single-use code in the URL fragment, in Redis.** Chosen.

## Decision Outcome

Chosen option: **E**. The extension asks the platform for a short-lived, single-use code, opens
`<workspace>/?capture=<id>#handoff=<code>`, and the SPA exchanges it once for a normal login token.

**Issue — `POST /auth/handoff`.** Requires a valid `Bearer` login token (`get_principal`); header
tenant mode never applies. Returns `{code, expires_in}` with `Cache-Control: no-store`. The code is
256 bits from `secrets.token_urlsafe(32)`.

**Storage.** Redis key `handoff:code:<sha256(code)>` (hashed at rest, so a Redis dump or `MONITOR`
never yields a usable code), value a **signed `typ:"handoff"` blob** `{sub, t, exp}` (same compact HMAC format as `token.py`, signed with `RECON_AUTH_SECRET`, verified on exchange), created with `SET NX EX 30`. Signing means write access to Redis alone cannot mint a login (review C1); a planted key without a valid signature is refused. **TTL 30 seconds**,
enforced by Redis, not by a clock in our code.

**Single use.** The exchange consumes with one atomic `GETDEL` (Redis 6.2+; the image is 7). A
second or racing exchange gets nothing. No read-then-delete.

**Exchange — `POST /auth/handoff/exchange`**, body `{code}` (JSON body, never a URL), unauthenticated
by nature. It consumes the code, then **re-reads the user from the database** by id with the admin
connection: the user must still exist and still belong to the bound tenant; the role and username
come from the database, not from the old token. It mints a fresh login token for that user and
returns the same shape as `/auth/login`. The new token's `exp` is **`min(now + ttl, exp of the token
that issued the code)`** (the blob carries that `exp`; the exchange refuses when no life remains). This needs token-API changes (review C3): `AuthClaims` gains `exp`; `mint` gains an absolute-expiry path; the issue route reads the claims directly because `Principal`/`get_principal` do not carry `exp`: a handoff can move a session, never lengthen it, so a leaked token cannot
be renewed by chaining codes. Every failure (unknown, used, expired, user gone, tenant changed) is
the same `401 "invalid or expired code"`; nothing says which.

**Rate limits and input checks.** Issue: 10 per user per 60 s, `429` beyond (bounds codes outstanding). Exchange: **no failure limiter** (review C4): a 256-bit code cannot be guessed, a failed exchange costs one hash and one `GETDEL`, and a global lock would let 30 junk POSTs a minute disable handoff for everyone. Instead the body model rejects anything that is not exactly 43 base64url characters (`422`) before Redis is touched. **Fail closed, short timeout** (review C5): both routes use the short-timeout Redis client (`get_login_redis`, 0.25 s), so an outage fails fast; Redis errors give `503` and the researcher uses the login screen; with auth disabled (`auth_secret` empty) both routes return `503` like `/auth/login`.

**The workspace (SPA).** `main.tsx` imports a tiny module first. At import time, before the router,
React or any other app module runs, it reads `location.hash`, and if it holds `handoff=<code>` calls
`history.replaceState(null, "", pathname + search)` so the code leaves the address bar and the history
entry, keeps the code in a module variable, and starts **one** exchange (a module-level promise, so
React StrictMode's double effects cannot spend the code twice). Success stores the token exactly as
a password login does. Failure of any kind shows the normal login screen **with no error message**.
**Every handoff needs one click** (review C2): the SPA exchanges first (the code is opaque, so who it names is only known after the exchange), holds the new token in memory only, and shows "Continue as *user* · *tenant*" (plus "you are currently signed in as *other*" when that is so). Only the click stores the token; cancel discards it. This is the defence against login CSRF (below); the owner may relax it to silent only when no session exists, which re-accepts that risk. Handoff settles before `bootstrapUser`/`getMe` run and a "pending" state keeps the login screen from flashing (review N3).

**The extension.** `openWorkspace` asks the background worker for a code using the saved token, and
opens the URL with the fragment only if the workspace URL is `http(s)` and the same origin the code was issued from. The background builds the final URL from `resolveApiBase()` (the popup and background resolve the setting differently today; a base containing `#` or a path is handled), review N4. On any failure it opens the bare URL, as today. The code is never stored,
logged or shown.

**Logging.** Server events carry user id, tenant id and outcome only. The code is only ever in a
request body or a response body, never a path or query, and no log line, exception message or metric
label may contain it. The ADR's condition is a test that scans captured logs for the code.

**Workspace origin differs.** The code is not bound to a browser origin (the server cannot know the
SPA's public origin behind a proxy). A code opened on another platform's origin is inert there (its
own Redis has no such key). The extension refuses to attach a code when the workspace URL's origin
is not the API origin it issued from.

**Attacker walkthrough.**

| Attacker has | Can do | Cannot do |
|---|---|---|
| A leaked extension token | Issue codes and use the API | Gain anything the token does not already give; extend life (exp cap) |
| A leaked/seen code, not yet used | Win the race for up to 30 s, once | Reuse it; use it after the legitimate exchange (it is gone) |
| A used or expired code | Nothing | Anything |
| A malicious web page | Nothing: cannot read the fragment of another origin, cannot call `/auth/handoff` (no token, no CORS) | |
| Write access to Redis (published without a password in the dev compose) | Plant keys | Mint a login: the stored blob must verify under `RECON_AUTH_SECRET` (C1) |
| Another extension with `tabs`/host access | See the fragment before the page clears it and race it once | Anything it could not already do: it can read the origin's `localStorage` |
| A valid account and a way to get the victim to open `<workspace>/#handoff=<attacker code>` (a page can fetch a fresh code from its own server) | Try login CSRF | Sign the victim in without a click (C2: always confirm, identity shown). The 30 s TTL does not hinder this |

### Consequences

* Good, because Open in workspace lands signed in; the only secret in the URL is single-use and dead in 30 s.
* Good, because no new table or migration; Redis TTL is the expiry.
* Bad, because it is a new unauthenticated endpoint (the exchange) and a new Redis dependency for sign-in (login itself does not need Redis).
* Bad, because a code seen during its 30 s can be raced (accepted; see Review outcome).
* Bad, because one extra click replaces the password (C2).
* Accepted residuals: after a Redis crash within about a second of the `GETDEL`, append-only fsync can bring a consumed code back for the rest of its 30 s (N1; no replicas today); `replaceState` rewrites the session-history entry but it was not verified that browser-global or synced history drops the original URL, so safety rests on single use, not erasure (N6); handoff works only against the served SPA, not the Vite dev server, whose proxy omits `/auth` (N8); our own capture content script reads `location.href` at `document_start` (the workspace origin is already excluded in `background.js`), so the log-scan test covers it (N7).

### Confirmation

Tests (H2, `recon/auth/handoff_test.py`, `recon/api/auth_handoff_test.py`, `web/src/auth/handoffFragment.test.ts`,
extension `tests/test_handoff_open.mjs`): single use under a race; expiry; wrong or deleted user and
tenant mismatch; role from the database; exp cap (a code from a near-expiry token gives no longer session; zero remaining life is refused); a planted unsigned Redis key is refused; issue rate limit; malformed code rejected before Redis; auth-disabled and Redis-down give 503; click required and cancel discards; Redis stores
only the hash; the code never appears in captured logs; identical 401 for every failure; fragment
cleared before the router; StrictMode spends the code once; different-user needs confirm.

## More Information

Spec `apps/platform/docs/superpowers/specs/2026-11-15-research-workflow-scope-and-popup-design.md`
(slices H1, H2), tickets #212 (this), #222 (build), map #174, ADR-0010 "More Information". Related:
the central login (`recon/auth/`), ADR-0002 (the exchange resolves the user cross-tenant by id through
the admin connection, as `/auth/login` does).

**Review outcome (2026-11-16).** An adversarial review (separate subagent, every objection tied to repo lines or official docs) found no blocker: the mechanism holds (256-bit code, hashed at rest, `SET NX EX`, atomic `GETDEL`, fragment transport, database re-read, no CORS and Bearer-only so no ambient authority, popup commands admitted only from extension pages). Verdict **accepted with conditions**, all folded in above:

* **C1** Redis write access would have meant minting a login for any user (`docker-compose.yml` publishes Redis without a password; `token.py` says verification needs no database) -> stored blob is HMAC-signed.
* **C2** login CSRF is silent for a signed-out victim, and the TTL does not help -> every handoff needs one click.
* **C3** the exp cap cannot be built on `AuthClaims`/`Principal`/`mint` as they are -> the changes are listed.
* **C4** a global exchange failure limiter was a free denial of service with no benefit -> removed; input shape checked first.
* **C5** the plain Redis client has no timeout and auth-disabled behaviour was unspecified -> short-timeout client, 503.
* **N1-N8** (Redis crash replay, exchange-before-confirm, getMe race, URL building, other extensions, history wording, our content script, Vite dev) are in Consequences, Confirmation or the SPA/extension paragraphs.

**Not verified by the review, to check during the build:** whether FastAPI accepts a JSON body without a `Content-Type` (which would let a web page fire the exchange without a preflight; harmless without a code, but test it) and whether the fake Redis used in unit tests supports `GETDEL` (else the race test runs on real Redis). Doc URLs were cited from knowledge, not re-fetched. **Decision needed from the owner:** approve this design, and confirm the always-click interstitial (C2) or choose silent-when-signed-out.
