# Slice-2 deferred debt

Slice 2 is **"one JS file → findings"** (upload or a single in-scope target URL).
Auditing all 40 REQ-* IDs against that contract, three items were consciously
deferred with the user's sign-off. This ledger keeps "later" from becoming
"never": each carries what's missing, why it's safe to defer now, and the trigger
that should pull it back in.

| Item | REQ | Priority | Status now | Trigger to revisit |
|---|---|---|---|---|
| OS/network-level egress isolation | P2, T2 | **MUST** (deferred) | App-level guard only | Before running any net-emitting engine (Sourcemapper URL-fetch, Kingfisher validators) or exposing the fetcher to untrusted multi-tenant load |
| Automated asset discovery (katana crawl, gau archive, robots.txt) | C1, Q5 | SHOULD | DISCOVER stage stubbed | When scope moves from "one asset" to "crawl a host" (M3 scale) |
| Ephemeral/JIT/audit-logged secret reveal | S2 | MUST (reveal half) | Storage half done (hash + location) | Slice 3 (manual-probe handoff / workspace) — reveal is a workspace interaction |

## OS/network-level egress isolation (deferred MUST — the one to watch)

REQ-P2 says metadata/RFC1918 are "blocked at the **network layer**"; REQ-T2 wants
net-emitting engines in a "scoped egress sandbox". Today enforcement is
**application-level** (`recon/fetch/egress.py`): scheme + in-scope host + all
resolved IPs globally routable, DNS-pinned per request, redirects re-validated per
hop, scope never derived from crawled URLs.

- **Why deferred is acceptable now:** the app guard already defeats the actual
  SSRF threat for the only outbound traffic we make (the fetch stage). Kingfisher
  runs with `--no-validate` (no network); Sourcemapper's external-URL fetch is not
  wired. So no engine currently makes un-guarded outbound requests.
- **What's still owed:** OS-level isolation (network namespace + egress firewall,
  seccomp, nsjail) as defense-in-depth against a compromised worker or a shelled-
  out engine that ignores our host argument. This is the belt-and-suspenders the
  spec's "network layer" wording asks for.
- **Do not** wire Sourcemapper's external `.map` fetch (or any new net-emitting
  engine) without either routing it through the app guard or landing this
  isolation first.

## Automated asset discovery (katana / gau / robots)

The DISCOVER stage exists in the pipeline but is a stub. Crawl needs headless
Chrome, a CGO build of katana, gau, and per-host politeness at crawl scale
(REQ-Q3's robots.txt handling belongs here — it's only meaningful once multiple
paths on a host are being fetched). This is the M3 "scale" story, not "one JS
file". The `< 4 min` SLA is explicitly defined for bounded input (≤ N assets,
single host).

## Secret reveal (S2 reveal half)

Secrets are already custodied safely: finding identity is `provider:sha256(token)`
(never plaintext in the hash), the raw match lives only on the RLS-scoped
occurrence row (a reviewed decision, `docs/req-d3-finding-hash-normalization.md`
§4.2). The remaining reveal UX — ephemeral, just-in-time, audit-logged disclosure —
is a workspace interaction and lands with the slice-3 manual-probe handoff.
