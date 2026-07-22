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
| Ephemeral/JIT/audit-logged secret reveal | S2 | MUST | **DONE (slice 3b)** — hash+location by default, JIT reveal by slicing the source blob, audited | — (see the slice-3b section below for residual debt) |
| Freeze migration 0001 to a static snapshot | D1 | infra | 0001/0002 use `create_all` from live metadata; 0003 guarded with `IF NOT EXISTS` | Before real prod/zero-downtime upgrades (M3) — see below |

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

## Secret reveal (S2) — DONE in slice 3b

**Closed 2026-07-22 (slice 3b, both gates passed).** REQ-S2 is discharged with
storage model A: the raw secret is **never stored** — the DB keeps only the
identity hash (`provider:sha256(token)`) + byte offsets, and the run's source blob
is the single at-rest copy. The findings read redacts secret `evidence` (keyed on
`finding.type == "secret"`, so legacy plaintext rows are masked too) and adds a
`revealable` flag. `POST /runs/{id}/findings/{hash}/reveal` re-derives the value
just-in-time by slicing the blob at the stored offsets (in analyze's exact
decode-replace byte space), re-checks `provider:sha256` (fail-closed → 409 on
drift), and returns it once — auditing **every** attempt (`secret.revealed` /
`secret.reveal_denied`, value-free `run_event`). The offset convention is verified
against the real Kingfisher engine in CI. See
`docs/superpowers/specs/2026-07-22-slice3b-secret-reveal-design.md` (§10 records
the adversarial-gate findings folded in).

## Slice-3a deferred debt (manual-probe handoff)

Slice 3a (REQ-P1) is complete on `main` (both review gates passed). Deliberately
out of scope, plus residual review nits, tracked here:

| Item | Priority | Why deferred | Trigger to revisit |
|---|---|---|---|
| S2 secret reveal (ephemeral/JIT/audit) | MUST | ~~Its own sub-slice~~ | **DONE — slice 3b (see below)** |
| Static request-header extraction (Vespasian) | SHOULD | 3a reconstructs method/path/query/body; headers/auth are the manual tester's to add | Pairs with the C2 wrapper-teaching thread |
| Postman + mitmproxy exporters | SHOULD | curl + raw-HTTP cover curl + Burp; each new format is an isolated pure serializer | On demand (team/Postman or signature-replay workflows) |
| Cross-file base-URL resolution + wrapper-teaching (C2 SHOULDs) | SHOULD | No data model yet; relative endpoints render `{{base_url}}` | The C2 SHOULD thread |
| `recon_object` materialization (persisted per-op projection) | — | 3a reconstructs on-demand at read time; no persisted projection needed yet | Slice 4, when the threat model needs a stable-id projection |

**Residual review nits (non-blocking, from the final gates):**
- `TriageStatus` StrEnum: the three status values live in the model CHECK, `VALID_STATUSES`, and the API — a `recon.domain.TriageStatus` + `_enum_check` would DRY them (matches the codebase convention).
- Migration `0004` `downgrade()` hardcodes `drop_table("finding_triage")` instead of looping `TRIAGE_TABLES` (latent if the tuple grows).
- Test coverage: no `build_requests` permutation (input-order) test though determinism is now load-bearing; only WSS (not plain WS) websocket test; no assertion on the triage note/actor **return value** after a status-only upsert (DB row is asserted); `to_http` unpacks an unused `base` var (rename `_base`).

## Slice-3b deferred debt (secret reveal)

Slice 3b (REQ-S2) is complete on `main` (adversarial design gate at the design
stage + whole-branch code review, both passed). Deliberately out of scope, plus
residual review nits, tracked here:

| Item | Priority | Why deferred / safe now | Trigger to revisit |
|---|---|---|---|
| Retention / TTL + tenant-initiated purge | S4 **MUST** | Its own slice; with model A, purge collapses to the source-blob lifecycle | **Slice 5** (retention/diff) — the TTL numbers were always slated there |
| Backfill/scrub of pre-3b `evidence` rows | S2 | No real prod data; the read redaction already masks them and reveal ignores the column | Fold into slice-5 purge, or before any real data lands |
| Re-scan to relocate offset-less secrets | S2 | Rare (Kingfisher gave no locatable offset); such a finding is `revealable:false` | If offset-less secrets show up in practice and must be revealable |
| SSE-publish of the reveal audit | S3 | Reveal audit is persisted to `run_event` (durable); live streaming is not required | If a SIEM/live-audit consumer needs reveal events on the stream |
| Per-user auth for a verifiable reveal "who" | S1/S2 | Platform-wide — `X-Tenant-Id`/`actor` are best-effort labels (same as 3a triage) | When real per-user auth lands |

**Residual review nits (non-blocking, from the gates):**
- A non-`ClientError` blob-read failure now audits a `denial="error"` and re-raises
  (500) — but if `_audit` itself raised it would mask the original error; pre-exists
  on all paths, not introduced by 3b.
- Non-UUID `run_id` in the reveal/triage/reconstruct routes raises `DataError` → 500
  rather than 404 (shared pre-existing pattern; no leak).
- Test coverage: no multi-occurrence `revealable` `any()`-branch test; no route-level
  409/410 test (covered at the service layer); happy-path route test doesn't assert
  `finding_hash` in the body.
- **Pre-existing flake (NOT slice 3b):** `integration_test.py::test_duplicate_delivery_is_idempotent`
  intermittently fails under full-suite load (shared Redis stream/consumer-group
  timing); passes 3/3 in isolation. A slice-1 worker test — trace + stabilize when
  convenient.

## Migration strategy: `create_all` vs incremental DDL

`0001_initial` and `0002_findings` build tables with
`Base.metadata.create_all(bind)` from the **live** model metadata, not a frozen
snapshot. `create_all` creates any missing *table* (with all its *current*
columns), so on a from-scratch `alembic upgrade head` migration 0001 already
stands up the entire current schema — including columns that later revisions
"add". A plain `op.add_column` in a later revision then hits `DuplicateColumn` on
a fresh DB. This bit CI: `0003` added `run.source_map_ref`, which 0001 had already
created, so the first from-scratch migrate failed (it never surfaced locally
because the dev `pgdata` volume predated the column).

- **Fix applied now (minimal):** `0003` uses `ALTER TABLE ... ADD COLUMN IF NOT
  EXISTS` — a no-op on a fresh DB (0001 made the column) and still correct on an
  older DB. Any future column-add via an incremental revision must be guarded the
  same way while this pattern stands.
- **What's still owed:** freeze `0001` to an explicit, column-by-column
  `op.create_table` snapshot and stop calling `create_all` inside migrations, so
  each revision is an immutable historical step and plain `add_column` is safe.
  Do this before the platform performs real incremental upgrades against live
  tenant data (M3). Deferred here because the build is pre-prod with no data to
  preserve, and the rewrite is large and must exactly mirror the models
  (columns, FKs, indexes, RLS).
- **Detection note:** CI catches a broken migration because `api`/`worker`
  `depends_on migrate: service_completed_successfully`, so `docker compose run api`
  re-triggers migrate and fails the job. `docker compose up -d migrate` alone
  swallows the exit code — don't rely on step 1 to surface a migration failure.
