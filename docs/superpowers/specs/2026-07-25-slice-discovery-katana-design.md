# Slice X — katana discovery (design)

- **Date:** 2026-07-25
- **Status:** approved (brainstorming); pending written-spec review, then implementation plan
- **Slice:** "Slice X" — the first half of a two-slice split that turns
  `run.target = <bare domain>` from a no-op into real crawl-based discovery.
  **Slice Y** (multi-asset fetch/analyze + the finding-schema and secret-reveal
  changes it forces) is a separate, later design.
- **Primary REQ:** REQ-A1 (enqueue a run), REQ-P2 (egress stays scoped), REQ-P3
  (authorization ack), REQ-C2 (honest discovery coverage). Touches REQ-R1/R2
  (progress + event stream), REQ-S1 (tenant RLS on the new blob). Does **not**
  touch REQ-D3 finding identity, REQ-S2 reveal, or REQ-D5 completeness — those
  are Slice Y.

## 1. Context

Today the recon pipeline is depth-first over a **single** asset: a run's
`target` is one URL (or an uploaded file), `fetch.fetch_run` downloads that one
asset through the egress guard, and `analyze.analyze_run` parses it with
Vespasian. The `DISCOVERING` stage exists but is a stub; there is no crawl, so
"point at a domain and find all its JavaScript" does not work. A bare-domain
`target` is silently a no-op (`fetch.py:152` returns when the target has no
`http(s)` scheme).

This slice makes `DISCOVERING` real: given a session's in-scope domain, crawl it
with **katana** (headless) to enumerate the in-scope `.js` asset URLs, and record
them as a manifest surfaced in the UI. It deliberately **stops before** wiring
those assets into fetch/analyze — see Scope.

### 1.1 Why this is only half the work (the design-gate split)

The original single-slice design (domain → crawl → multi-asset findings) failed
its §4 adversarial design gate (verdict: *do not build as specified*) on three
code-verified points, all of which live in the **multi-asset** half:

1. **Attribution vs. dedup are mutually exclusive under the current schema.** The
   occurrence identity (`store.py:39-52`) has no asset dimension, and
   `finding.path` is inside `finding_hash` (`store.py:81`). So "one finding + N
   occurrences attributed to the real asset URL" cannot hold without a schema
   change.
2. **Multi-asset silently breaks the shipped S2 secret reveal.** `reveal.py:138`
   slices a single `run.input_ref`; per-asset blobs would make it read the wrong
   bytes.
3. **`PARTIAL` completeness is unreachable today.** `coordinator.advance` hardcodes
   `DONE` with `{fetch_ok: true, analyze_ok: true}`, and any stage error routes to
   `FAILED`.

Those are Slice Y's problem. Splitting isolates the katana / headless-Chrome /
egress **infrastructure** risk (this slice) from the **data-model** risk (next
slice), so we never carry a half-done migration next to unproven crawl infra.

## 2. Settled decisions

Decided during brainstorming; binding for this slice unless re-opened.

1. **Crawl mode: headless (Chrome-rendered).** Chosen over plain-HTTP for SPA
   coverage, accepting a heavier image and larger egress surface.
2. **Egress control: application-level, residual risk accepted.** katana is
   scope-locked and every emitted URL is re-validated through `egress.validate_target`
   before it enters the manifest; the crawl's own subresource loads (headless
   Chrome) are **not** routed through the egress guard, and that crawl-time SSRF
   surface is documented as accepted debt (hardening path = egress proxy, then
   netns/nftables). See §6.
3. **Eventual fan-out (Slice Y): model A — one run = one crawl.** One domain crawl
   is one run; the same endpoint across N bundles must dedupe to one finding with
   N occurrences. This slice only produces the manifest that Slice Y will consume;
   it is recorded here so Slice X's manifest shape already fits.
4. **Discovery only; fetch stays single-asset.** A domain-target run in this slice
   flows `DISCOVER (real) → FETCH no-op → ANALYZE no-op → DONE`, with the assets
   manifest as the deliverable. No state-machine, fetch, or analyze changes.
5. **OpenAPI export is out of scope** — a separate later slice.

## 3. Scope

**In scope**

- A real `DISCOVERING` stage: crawl `run.target` (a domain) with katana, headless.
- A **heartbeating crawl harness** (not `run_engine`) so a long crawl cannot be
  reclaimed and double-run (gate Obj 3); with process-group kill on timeout so
  headless Chrome grandchildren are reaped (gate Obj 5).
- An `assets` **manifest blob** of in-scope `.js` URLs, each re-validated through
  `egress.validate_target`.
- A `GET /runs/{id}/assets` read endpoint and UI: a "crawl a domain" input mode
  plus a discovery inventory ("we found N JS assets", with crawl status).
- Docker: install system chromium; run katana with `-system-chrome` + `-no-sandbox`.
- `storage.BLOB_KINDS` += `assets`; crawl config caps in `config.py`.

**Out of scope (→ Slice Y or later)**

- Multi-asset fetch/analyze (loop the manifest), the occurrence asset-dimension
  migration, secret-reveal routing to per-asset blobs, and `PARTIAL` completeness.
- Crawl parallelism (stays single-process; the fetch DNS-pin single-thread
  invariant is untouched here anyway).
- OpenAPI/Swagger export.
- Any egress hardening beyond application-level (proxy / netns).

## 4. Architecture

```
POST /runs {target: "acme.io"}          (existing endpoint; target = a domain)
    -> coordinator.start_run -> enqueue DISCOVERING            (unchanged)

worker.process_message(stage=DISCOVERING)
    -> discover.discover_run(...)                              (NEW real work)
         1. load session; require authorization_ack (REQ-P3)
         2. assert run.target is within session.scope_hosts (REQ-P2)
         3. crawl.run_katana(domain, scope_hosts, settings)    heartbeating harness
              -> katana subprocess (headless) -> JSONL on stdout
         4. katana.parse_assets(jsonl) -> candidate .js URLs
         5. keep only URLs that pass egress.validate_target(scope_hosts)
         6. cap to settings.crawl_max_assets; storage.put_blob(kind="assets")
         7. record_event "discover.assets" {count, assets_ref, status}
    -> advance -> FETCHING (no-op on bare domain) -> ANALYZING (no-op) -> DONE
```

**New module `recon/discover/`** (mirrors `recon/findings/` engine wrappers):

- `katana.py` — pure functions: build the argv, parse katana JSONL into a
  de-duplicated, ordered list of asset URLs. No I/O beyond parsing. Colocated
  `katana_test.py` runs against captured JSONL fixtures.
- `crawl.py` — orchestration: `discover_run(redis, *, tenant_id, run_id)`. Owns
  the authorization/scope gate, the heartbeating subprocess harness, egress
  re-validation, the cap, the manifest write, and the event. Colocated
  `crawl_test.py` with a mocked subprocess.

**Worker wiring** (`worker/main.py`): in the existing stage dispatch, add
`if stage == RunStage.DISCOVERING: discover.discover_run(...)`. `DISCOVER` is
already in `SERVED_QUEUES`.

## 5. The discover stage

### 5.1 katana invocation

Discovery-only: we use katana to enumerate JS **asset URLs**, not to parse
endpoints (`-jc`) — our own Vespasian owns parsing, and that is Slice Y. Flags
(verified against katana docs during the design gate; **re-verify against the
vendored katana version at build time** via `katana -h` / context7):

- `-u <domain>` (from `run.target`), `-hl` headless.
- `-system-chrome -system-chrome-path <path> -no-sandbox` — use the image's
  chromium, not katana's auto-download, and disable the sandbox for the non-root
  container user.
- `-jsonl` — structured output; **parse the JSONL, do not rely on `-f/-field`**
  (deprecated).
- `-em js` — extension-match JavaScript.
- Scope: `-field-scope rdn` plus `-crawl-scope` / `-crawl-out-scope` regexes
  derived from `session.scope_hosts`.
- Bounds: `-depth <settings.crawl_depth>`, `-crawl-duration <settings.crawl_duration_seconds>`,
  `-timeout <per-request>`.

### 5.2 Heartbeating harness (gate Obj 3 + Obj 5)

`run_engine` is unusable for a crawl: it is a single blocking `subprocess.run`
that cannot heartbeat, and the job lease equals `heartbeat_stall_threshold_seconds`
(30s) while a crawl runs far longer — so a peer worker would reclaim the RUNNING
job and launch a **second** headless crawl (double egress at the target). The
config already warns of this exact hazard for blocking fetch (`config.py`
fetch-timeout NOTE).

So `crawl.run_katana` runs katana via `subprocess.Popen(..., start_new_session=True)`
and polls in a loop, calling `progress.beat(...)` on `crawl_heartbeat_interval_seconds`
(**shorter than the stall threshold**), until the process exits or a hard
wall-clock backstop is hit. katana is given `-crawl-duration crawl_duration_seconds`
so it normally self-terminates first; the harness backstop fires at
`crawl_duration_seconds` + a small fixed grace (~15s) and is the belt-and-braces
kill. On backstop/cancel it kills the whole process group
(`os.killpg(os.getpgid(pid), SIGKILL)`) so headless Chrome grandchildren are
reaped rather than orphaned. Output is read with a size cap
(`crawl_max_output_bytes`, reusing the `engines` output-cap posture). This harness
lives in `discover/`, not `engines.py`; `run_engine` stays as-is for the
self-contained Kingfisher/Sourcemapper binaries.

### 5.3 Idempotency

Discovery is idempotent across a stage retry: if a `discover.assets` event already
exists for the run, `discover_run` returns without re-crawling (a full headless
crawl is expensive and must not repeat on redelivery). This mirrors `fetch`'s
"no-op if the input was already fetched" posture. The manifest blob is
content-addressed, so even a forced re-crawl yielding the same URLs writes the
same key.

### 5.4 Progress and honesty

- Heartbeats keep the run visibly alive during the crawl (coarse: a monotone
  `beat` on the poll interval; katana does not report granular progress on stdout).
- A `discover.assets` event carries `{count, assets_ref, status}` where `status`
  ∈ `ok | capped | timeout` — so the UI (and Slice Y's REQ-D5) can tell a clean
  crawl from one that hit the asset cap or wall-clock. This is the discovery
  analogue of analyze's existing source-map honesty.

## 6. Egress & security

**Enforced controls**

- Discover refuses to run unless `session.authorization_ack` is true (REQ-P3),
  reusing the fetch gate's posture.
- `run.target`'s host must be within `session.scope_hosts` (REQ-P2), validated
  before katana launches; otherwise the stage fails fast.
- katana is scope-locked with `-field-scope` / `-crawl-scope` / `-crawl-out-scope`
  from `scope_hosts`, and bounded by depth / duration / output caps.
- **Every URL katana emits is re-validated through `egress.validate_target(scope_hosts)`
  before it is written to the manifest.** The design gate confirmed this guard
  blocks link-local (incl. `169.254.169.254`), RFC1918, loopback, IPv6 ULA/link-local,
  CGNAT, NAT64, IPv4-mapped IPv6, and requires *all* resolved IPs be public
  (`egress.py:54-106`, table-tested in `egress_test.py`). So a katana bug or a
  scope-escape can never place an internal/out-of-scope URL in the manifest.

**Accepted residual risk (documented; hardening deferred)**

During the headless crawl, Chrome loads page subresources / in-page requests that
are **not** routed through `egress.py`. A malicious in-scope page could therefore
induce crawl-time SSRF from the worker's network position. This is accepted for
this slice. Mitigations, in order of strength, become debt-ledger items:
(1) a cheap **deployment control now** — run the worker with no network route to
cloud-metadata / RFC1918; (2) a forced egress proxy enforcing `egress.py`'s
allow-list; (3) netns + nftables confinement.

**Process isolation:** katana + Chrome run out-of-process, non-root, in their own
process group (§5.2), with wall-clock + output caps.

## 7. Storage & data

- `storage.BLOB_KINDS` gains `"assets"`. The manifest is a content-addressed JSON
  blob (`{tenant}/{run}/assets/{sha256}`), covered by the same tenant-scoped key
  convention (REQ-S1). No new table, no new run column.
- Manifest shape:
  ```json
  { "domain": "acme.io",
    "status": "ok",
    "assets": [ { "url": "https://acme.io/static/app.js", "source": "katana" } ] }
  ```
- The run references the manifest via the `discover.assets` event payload
  (`assets_ref` = the blob key), not a new column. `GET /runs/{id}/assets` reads
  the latest `discover.assets` event for that run, fetches the blob, and returns
  the manifest (tenant-scoped like every other read).

## 8. Config

New `config.py` settings (defaults below; all overridable by env). Defaults are
starting points to tune against the fixture site during implementation, not
load-tested values.

- `crawl_depth: int = 3` (katana's own default)
- `crawl_duration_seconds: float = 120.0` (passed to katana `-crawl-duration`)
- `crawl_max_assets: int = 500` (manifest cap; drives `status = "capped"`)
- `crawl_max_output_bytes: int = 32 * 1024 * 1024` (katana stdout cap)
- `crawl_heartbeat_interval_seconds: float = 10.0` (must stay well under
  `heartbeat_stall_threshold_seconds = 30.0`)
- `system_chrome_path: str` (chromium path in the image, e.g. `/usr/bin/chromium`)

The harness wall-clock backstop (§5.2) is `crawl_duration_seconds + 15s`; it is
derived, not a separate knob.

## 9. UI (Mode A)

- **NewRunPanel:** add a "crawl a domain" mode beside the existing single-URL /
  file-upload modes; entering an in-scope domain posts `POST /runs {target: domain}`.
- **Run workspace:** a discovery phase that shows the crawl running (live via the
  SSE stream), then the **assets inventory** — the list of discovered `.js` URLs,
  the count, and the `ok | capped | timeout` status badge. Findings stay empty for
  a domain run in this slice (that is Slice Y); the inventory is the payoff.
- Ends with the mandated live visual walkthrough against a **local fixture site**
  served in the Docker stack (we do not crawl the internet in the walkthrough).

## 10. Testing

- **Unit (colocated):** `katana.py` JSONL parser (fixture → ordered, de-duped URL
  set; extension filtering); `crawl.py` orchestration with a mocked subprocess
  (authorization/scope gate; heartbeat called on the poll interval; timeout →
  process-group kill; cap → `status="capped"`).
- **Security regression:** a katana output containing an out-of-scope / internal
  URL must be filtered by the `egress.validate_target` pass and never appear in
  the manifest. This is the test that backs the accepted-risk decision.
- **Integration (real katana, gated like the Kingfisher round-trip,
  `RECON_REQUIRE_ENGINES=1` + docker):** crawl the local fixture site, assert the
  discovered `.js` set matches, and assert the run reaches `DONE` with a populated
  assets manifest.
- **Front-end:** NewRunPanel domain mode + the inventory view (Vitest), matching
  the slice-UI0 patterns.

## 11. Review gates (§4)

- **Gate 1 (adversarial design):** the combined design was reviewed; this slice
  folds in the infra-relevant objections (Obj 3 heartbeating harness, Obj 5
  process-group kill, Obj 6 chromium/BLOB_KINDS/config, deprecated `-f`). The
  data-model objections (Obj 1, 2, 4) are explicitly deferred to Slice Y. A short
  re-review of *this* narrowed spec is worthwhile before coding.
- **Gate 2 (higher-model code review):** whole-diff review after implementation.

## 12. Debt ledger additions

To `docs/slice2-deferred-debt.md` on landing:

- Egress hardening for the headless crawl: deployment network control → egress
  proxy → netns/nftables.
- **Slice Y:** multi-asset fetch/analyze (loop the manifest), occurrence
  asset-dimension migration, secret-reveal routing to per-asset blobs, `PARTIAL`
  completeness (REQ-D5).
- Crawl parallelism (queue fan-out; model C) — scale milestone.
- OpenAPI/Swagger export — the other half of "complete the first chunk".

## 13. As-built amendments

_(none yet — filled in during implementation.)_
