# crawl-enhancements — design spec

Date: 2026-08-05 · Status: proposed (no code written; awaiting §4 adversarial gate + user approval) · Branch: `spike/platform-ingest`

> Companion to `docs/superpowers/specs/2026-08-05-recon-range-design.md` (the controlled verify vehicle). This spec closes three measured gaps so the platform's own `katana → fetch → analyze` pipeline recovers a JS app's API surface **without the browser extension**. Every technical claim below is grounded at a cited `file:line`; where the code contradicts the intended change, it is called out explicitly (see §9).

## 1. Purpose

Today the only path that reaches a green recon-range score is the **extension capture** path (`apps/capture/chrome-extension/` → `POST /api/save-files`), because the extension does the JS execution, lazy-chunk loading, and source-map grabbing that the native crawler does not. The native crawl (`recon.discover.crawl` → `recon.fetch.fetch` → `recon.findings.analyze`) is the path we actually want to keep after the convergence, but on its own it under-recovers.

Empirical baseline, measured last session against `test-targets/recon-range` (answer key = 15 `should_find` items, `answer-key.json:4-20`):

| pipeline configuration | recon-range score |
|---|---|
| standard crawl (current `main`) | ~9 / 15 |
| + katana `-jc` (lazy/dynamic `import()` chunks) | ~10 / 15 |
| + external `.map` fetch stage (source-map recovery) | ~15 / 15 |

The source-map stage carries the large jump: recon-range's minified chunks defeat the extractor's shape matching (the recon-range final review confirmed the webpack inventory chunk minifies to `t.A.create({baseURL:"/api/v2"})`, which `_is_axios_create` cannot recognize — `ep-inv`/`ep-checkout` detection **depends on** source-map recovery of the original source). This mirrors the extension path, which already recovers sources via `source_map_origin="capture"` (`analyze.py:224`).

The third change is an enabler for **local** verification: recon-range serves on `http://localhost:4173`, which the SSRF egress guard blocks by design. A default-off flag lets the pipeline reach loopback **only when explicitly enabled**, so the whole crawl→fetch→analyze→score loop can be exercised on a developer box without the extension.

Non-goals: no new detector forms, no OS/network egress isolation (still deferred), no cutover of `apps/capture`.

## 2. Grounded constraints (what the code does today)

### 2.1 Discovery — `recon.discover`
- `katana.build_argv` builds a **discovery-only** argv and *deliberately omits* `-jc`: the module docstring states "we drive katana purely as a JS-asset discovery crawler … so we never pass ``-jc``" (`katana.py:1-14`), and `test_build_argv_standard_by_default` pins `assert "-jc" not in argv` with the comment "discovery-only: Vespasian parses, not katana" (`katana_test.py:15`). ⇒ **REQ-CE1 overturns a deliberate, tested decision** (see §9).
- The argv base list is `katana.py:33-39`; the headless block is `katana.py:42-53`; `crawl.discover_run` calls `build_argv` at `crawl.py:68-74` with `settings` read at `crawl.py:67`.
- `parse_assets` dedupes by URL via a `seen` dict and keeps only `http(s)` `.js` URLs (`katana.py:57-76`); `assets.seed_pending` further dedupes with `on_conflict_do_nothing(index_elements=["run_id","url"])` (`assets.py:44-45`); the manifest is capped at `settings.crawl_max_assets` (`crawl.py:84`, default 500 `config.py:75`). ⇒ any asset-volume increase from `-jc` is bounded and de-duplicated.

### 2.2 Fetch — `recon.fetch`
- `_fetch_assets` fetches each not-yet-terminal asset's JS through the egress guard and stores it as an `input` blob, then marks the row `fetch_ok` (`fetch.py:302-321`). **It never inspects the JS for a `sourceMappingURL` and never fetches a `.map`.** ⇒ REQ-CE2's new work lives here.
- `fetch_url` enforces the full policy on every hop (`egress.validate_target` at `fetch.py:107`), pins DNS to the validated IP (`fetch.py:114`, `_pin_dns` `fetch.py:53-84`), caps the body at `max_bytes`, and classifies errors into `EgressBlocked` / `FatalError` / `RetryableError` (`fetch.py:87-142`). ⇒ REQ-CE2 reuses `fetch_url` verbatim for the `.map` GET, so the map fetch inherits the SSRF guard.
- Politeness: `_fetch_assets` acquires a per-host slot before each asset via `_await_host_slot` (`fetch.py:298-301`, `config.py:60-61`).

### 2.3 Source maps — `recon.findings`
- `sourcemapper.extract_inline_map` handles inline `data:` maps but returns `None` for an **external** `//# sourceMappingURL=app.js.map`, with the comment "external reference — deferred to the fetch stage" (`sourcemapper.py:70-71`); the module docstring says external is "out of scope until the fetch stage exists" (`sourcemapper.py:8-9`). The URL regex `_SOURCE_MAPPING_URL_RE` (`sourcemapper.py:33`) and the last-match-wins convention (`sourcemapper.py:66-69`) are reusable.
- `analyze._resolve_source_map` already resolves a stored `source_map_ref` blob with a caller-supplied origin, else falls back to inline, else none (`analyze.py:463-474`). `_analysis_units` recovers original sources from the map and, on a bad map, **falls back to bundle analysis** for the tolerant origins `("inline","capture")` while re-raising for `"uploaded"` (`analyze.py:433-460`, esp. `:451-452`).
- `_analyze_assets` already passes **`source_map_origin="capture"`** for every crawl/capture asset and reads `asset.source_map_ref` (`analyze.py:218-229`). ⇒ **the analyze side needs no change**: if the fetch stage sets `run_asset.source_map_ref`, analyze recovers sources automatically (see §9).
- The capture-ingest path is the pattern to mirror: it stores the map with `storage.put_blob(..., "source_map", ...)` (`capture_router.py:289`) and links it via `assets.set_source_map_ref(session, row.id, map_key)` in the same tx that marks the asset `fetch_ok` (`capture_router.py:224-228`, setter at `assets.py:79-83`). Blob kind `"source_map"` is already registered (`storage.py:24-26`); `AssetRow.source_map_ref` already exists (`assets.py:24-35, 60`).

### 2.4 Egress guard (the SSRF boundary) — `recon.fetch.egress`
- `is_public_ip` accepts an address only if `is_global and not is_reserved and not is_multicast`; loopback, RFC1918, link-local, and IPv4-mapped/6to4-wrapped internal addresses are all rejected (`egress.py:144-170`). This is the checkpoint that blocks `127.0.0.1` and `::1`.
- `is_valid_scope_entry` requires **≥2 LDH labels**, so a single-label host (`localhost`) and IP literals are rejected structurally (`egress.py:66-92`, esp. `:86-87, :90-91`). This is the checkpoint that stops `localhost` from ever being a valid scope entry.
- `host_in_scope` builds its allow-set from entries that pass `is_valid_scope_entry` (`egress.py:140`); `validate_target` calls `host_in_scope` (`egress.py:199`) then `is_public_ip` on every resolved IP (`egress.py:209-211`); `normalize_scope_entry` gates persistence on `is_valid_scope_entry` (`egress.py:109`).
- **Blast radius beyond egress.py** (why REQ-CE3 is wider than the two functions): a `localhost` scope entry is dropped at session-create by `_resolve_scope_hosts` → `normalize_scope_entry` (`sessions/service.py:104-113`), whose docstring explicitly documents "A target whose host is not itself a valid scope entry (an IP literal, ``localhost``) does NOT seed scope" (`service.py:100-101`); and a `localhost` crawl target is refused at run-create by the fail-fast `host_in_scope` check (`runs_router.py:63`). All three layers must consult the flag or the loopback crawl fails closed *before* egress runtime.

### 2.5 Config + scoring harness
- All crawl/fetch tunables live in `config.py` (`Settings`, `env_prefix="RECON_"`, `config.py:16-17`); the crawl block is `config.py:63-78`, the fetch block `config.py:49-61` (`max_fetch_bytes = 10 MiB`, `config.py:54`).
- The score harness reads `GET /runs/{id}/findings` and consumes a single `coverage` object (`score.mjs:4`, `score-cli.mjs:12`). For a multi-asset (crawl) run, `_latest_coverage` **sums** counts across every asset's `analyze.coverage` event (`findings/queries.py:257-283, 305-333`) — so `sources_recovered` and `unattributed` are run-wide totals — **but `source_map` is not merged**: it takes the highest-id (last-analyzed) asset's value (`findings/queries.py:331`, `payloads[0]` after `order_by(RunEvent.id.desc())`). recon-range's `coverage_asserts.source_map_ok = ["capture","inline"]` (`answer-key.json:29`) therefore reads one asset's status (see §9 / §8 risk).

## 3. Requirements

| id | requirement |
|---|---|
| **REQ-CE1** | Katana discovers lazy/dynamic `import()` chunks by parsing JS (`-jc`), config-gated, default on. |
| **REQ-CE2** | The crawl/fetch path discovers a fetched JS asset's external `//# sourceMappingURL=`, fetches the `.map` **through the egress guard**, stores it as a `source_map` blob, and links it to the asset via `source_map_ref` with the tolerant `"capture"` origin — so analyze recovers original sources. A bad/unfetchable map must never drop the asset's bundle findings. |
| **REQ-CE3** | A **default-off** environment flag (`RECON_ALLOW_LOCAL_EGRESS`) that, and only when explicitly enabled, relaxes the loopback/RFC1918 + single-label-host blocks so the pipeline can reach a local target for testing. Never per-request, never from a URL/query param. Link-local/cloud-metadata stays blocked even when enabled. |
| **REQ-CE4** | Each change is verifiable end-to-end against `test-targets/recon-range` via the existing `scripts/score.mjs` harness, plus colocated platform unit tests. |

## 4. REQ-CE1 — lazy-chunk discovery via katana `-jc`

**What `-jc` does.** katana's `-jc` / `-js-crawl` enables endpoint parsing *inside* JavaScript files, so katana follows chunk URLs referenced in the bundle (webpack/vite chunk maps, `import()` targets) without executing them. That is exactly how a standard (non-headless) crawl surfaces recon-range's lazy chunks (`inventory`/`social`/`live`), which are otherwise only loaded by an `IntersectionObserver` scroll the static crawler never triggers.

**Where the flag goes.** Add a gated append in `build_argv` after the base list (`katana.py:39`), before the headless block (`katana.py:42`):

```
Before (katana.py:33-39):          After:
argv = [                            argv = [ …same base list… ]
  katana_bin, "-u", target,         if js_crawl:
  "-jsonl", "-silent",                  argv += ["-jc"]
  "-depth", str(depth),             # headless block unchanged (katana.py:42-53)
  "-crawl-duration", …,
  "-field-scope", "rdn",
]
```

`build_argv` gains a keyword `js_crawl: bool = True`. `crawl.discover_run` passes `js_crawl=settings.crawl_js_crawl` in the existing `build_argv` call (`crawl.py:68-74`).

**Config.** Add to the crawl block (`config.py:63-78`):

```
crawl_js_crawl: bool = True   # env: RECON_CRAWL_JS_CRAWL
```

**Config-gated (default on) vs unconditional — decision.**

| option | pro | con |
|---|---|---|
| **config-gated, default `True` (RECOMMENDED)** | matches house style (every crawl tunable is a `Settings` field — `crawl_headless`, `crawl_depth`, `config.py:72-77`); keeps the measured +1 on by default; leaves a kill-switch if a future katana build makes `-jc` pathological on some target | one more field |
| unconditional | one fewer field | no escape hatch; katana flag semantics "drift between releases" (explicit warning, `katana.py:11`), so a hard-wired `-jc` has no off-ramp if a version regresses |

Recommend **config-gated, default `True`**.

**Downstream effect (asset volume, dedup).** `-jc` increases discovered URLs (more paths parsed out of JS). Volume is bounded and de-duplicated at three existing layers: `parse_assets` `seen`-dict (`katana.py:57-76`), `seed_pending` `on_conflict_do_nothing` (`assets.py:44-45`), and the `crawl_max_assets` cap (`crawl.py:84`). More assets ⇒ more fetch/analyze work per run, paced by the existing politeness gate (`fetch.py:298-301`). No dedup change needed.

## 5. REQ-CE2 — external source-map fetch on the crawl/fetch path

### 5.1 Flow

```
_fetch_assets (fetch.py:302-321) — the .map stage runs ONLY on the JS-SUCCESS path
(after fetch.py:318 put_blob), inside its OWN inner try/except that NEVER re-raises,
so it can never reach the outer except at fetch.py:307 that would fetch_fail the asset:
  content ─▶ scan for //# sourceMappingURL=<ref>   (reuse sourcemapper regex)
             │
             ├─ none / data: ref ─▶ do nothing (inline handled at analyze time)
             │
             └─ external ref ─▶ map_url = urljoin(asset.url, ref)          ┐ NO DB tx open
                                 fetch_url(map_url, scope, allow_local)     │ (mirror fetch.py:318)
                                   │  ← EGRESS GUARD                        │
                                   │  (ANY exception ─▶ swallow, log,       │
                                   │   NO re-raise, source_map_ref unset)   │
                                   ▼                                        │
                                 storage.put_blob(tenant, run, "source_map", map_bytes) ┘
                                   ▼
                                 with tenant_session:  ── ONE tx (mirror fetch.py:319-320)
                                   set_fetch_ok(asset.id, js_key)
                                   set_source_map_ref(asset.id, map_key)
                                                                                     │
analyze._analyze_assets (UNCHANGED) reads asset.source_map_ref, origin="capture" ◀──┘
  ─▶ recovers original sources ─▶ findings attributed to real per-file paths
```

### 5.2 Design points (each grounded)

- **Discovery of the `sourceMappingURL`.** Reuse `sourcemapper._SOURCE_MAPPING_URL_RE` (`sourcemapper.py:33`) and the last-match-wins rule (`sourcemapper.py:66-69`). Propose a small pure helper in `sourcemapper.py`, `external_map_url(js: str) -> str | None`, returning the **last** ref iff it is present and does **not** start with `data:` (a `data:` ref needs no fetch — analyze's inline path already handles it, `analyze.py:471-473`). This keeps all `sourceMappingURL` parsing in one module and leaves `extract_inline_map` untouched.
- **Fetching the `.map`.** Call the existing `fetch.fetch_url(map_url, engagement.scope_hosts, timeout_s=settings.fetch_timeout_seconds, max_bytes=settings.max_fetch_bytes, allow_local=…)` (`fetch.py:87-142`). This is mandatory: it is the SSRF guard (scope + all-IPs-public + DNS pin), so the `.map` GET is validated exactly like the JS GET. The map URL is resolved with `urljoin(asset.url, ref)` so a relative ref stays same-origin and in scope.
- **Size cap.** Reuse `settings.max_fetch_bytes` (10 MiB, `config.py:54`) as the `.map` cap (a `.map` with `sourcesContent` can be large but is bounded like any fetched asset). Recovery is separately capped by `engine_max_output_bytes` at recover time (`sourcemapper.py:106`, `config.py:47`).
- **Blob storage.** `storage.put_blob(tenant_id, run_id, "source_map", map_bytes)` — kind already registered (`storage.py:24-26`), content-addressed key (`storage.py:29-34`).
- **Placement — the entire `.map` stage lives in the JS-SUCCESS path, and its network GET is OUTSIDE the DB transaction.** The `_fetch_assets` per-asset body is `try: content = fetch_url(...)` (`fetch.py:302-305`) → `except (EgressBlocked, FatalError, RetryableError): set_fetch_failed(...); continue` (`fetch.py:307-317`) → JS blob put + `fetch_ok` tx (`fetch.py:318-320`). The `.map` work is appended to the **JS-success** branch, *after* the outer `except` can no longer fire for this asset, and it mirrors the existing blob-put-then-tx shape (`fetch.py:318-320`): (1) discover the ref + `fetch_url` the `.map` + `storage.put_blob(...,"source_map",...)` all happen with **no DB session open**; (2) *then* a single `with tenant_session` block runs `set_fetch_ok(s, asset.id, key)` **and** `set_source_map_ref(s, asset.id, map_key)` (`assets.py:79-83`) together, so the map ref and `fetch_ok` commit atomically — mirroring capture ingest's first-wins linkage (`capture_router.py:224-228`). The `.map` network GET never runs inside the transaction (a slow/hung map fetch must not hold a DB tx open), exactly as the JS `put_blob` precedes its tx at `fetch.py:318`.
- **Origin — pick `"capture"`, justified.** The crawl asset must pass `source_map_origin="capture"` semantics. `_analysis_units` treats `("inline","capture")` as tolerant (a malformed map ⇒ fall back to bundle analysis, status `"capture-error"`) and only `"uploaded"` as strict (re-raise, failing the asset) (`analyze.py:451-453`). A crawl-fetched map is opportunistic (best-effort, exactly like the extension's post-auth grab), so it must be tolerant. **No analyze change is needed** — `_analyze_assets` already hard-codes `source_map_origin="capture"` for every asset (`analyze.py:224`); the fetch stage only needs to populate `source_map_ref`. (A dedicated `"crawl"` origin would be cleaner for coverage honesty but would require touching the tolerant-origin tuple at `analyze.py:451` **and** the harness's `source_map_ok` list — deferred as a minor, see §8.)
- **Bad/unfetchable map must not drop findings — two independent layers.**
  1. *Fetch layer:* the `.map` stage is wrapped in its **own inner** `try/except` that swallows **everything** — `(egress.EgressBlocked, retry.FatalError, retry.RetryableError)` plus any other exception (a malformed ref, a `urljoin` error, a storage hiccup). This inner handler **MUST NOT re-raise**: it logs and leaves `source_map_ref` unset, and control continues to the normal `set_fetch_ok` tx. This is the load-bearing invariant — the outer `except` at `fetch.py:307` marks the asset `fetch_failed` (`set_fetch_failed`, `fetch.py:309`) and thereby **drops the asset's JS finding entirely**. Because the `.map` stage runs strictly *after* the JS fetch succeeded and inside its own non-re-raising `try/except`, a `.map` failure can **never** propagate up to that `fetch.py:307` handler. The asset stays `fetch_ok` with its JS blob; analyze then runs on the minified bundle (`_analysis_units` → `("input.js", source), "none"`, `analyze.py:441`).
  2. *Analyze layer:* even a fetched-but-unparseable map falls back because the origin is `"capture"` (`analyze.py:451-452`).
  A `.map` fetch failure is **never** promoted to a per-asset `fetch_failed` — it is a soft miss, not an asset failure.
- **Politeness.** The `.map` is a second outbound request to the asset's host; acquire a host slot with the existing `_await_host_slot` before the `.map` fetch (`fetch.py:298-301`) so the anti-hammer guarantee holds. (If deferred, note as a documented minor — one extra request per asset under a default-off local flag.)
- **Config (optional).** A `crawl_fetch_source_maps: bool = True` gate (`config.py` crawl block) gives a kill-switch symmetric with REQ-CE1; recommended but optional (the fetch is already best-effort and self-limiting).

**Scope.** REQ-CE2 targets the **multi-asset crawl path** (`_fetch_assets`). The legacy single-URL path (`fetch_run`, `fetch.py:162-206`) already has a `Run.source_map_ref` slot that analyze reads (`analyze.py:107`); mirroring the map fetch there is a straightforward follow-up but out of scope for this slice (its origin would default to strict `"uploaded"`, `analyze.py:329` — a separate decision).

## 6. REQ-CE3 — default-off local-egress flag (SSRF-sensitive)

### 6.1 The flag

`config.py` (fetch block, near `config.py:49`):

```
# SSRF guard override — DEFAULT OFF. When true, the egress guard additionally
# permits loopback + RFC1918 targets and single-label hosts (e.g. localhost) so
# the crawl→fetch→analyze pipeline can be run against a LOCAL test target.
# Link-local / cloud-metadata (169.254.169.254, fe80::/10) stays BLOCKED even
# when enabled. NEVER enable outside a developer box (see spec §9 risks).
allow_local_egress: bool = False   # env: RECON_ALLOW_LOCAL_EGRESS
```

### 6.2 Mechanism — explicit `allow_local` parameter (not global read)

Thread an explicit keyword `allow_local: bool = False` through the guard, read **once** from config at each stage entry point and passed down. This keeps the egress predicates pure and unit-testable (the existing `egress_test.py` calls them as pure functions and monkeypatches `getaddrinfo`), and makes the relaxation visible at every call site.

| function | change | line |
|---|---|---|
| `is_public_ip(ip_str, *, allow_local=False)` | insert an `allow_local` branch AFTER the existing IPv4-mapped/6to4 unwrap (`egress.py:166-169`): first reject link-local/multicast, then accept loopback/private; else fall through to the existing strict rule. **Must NOT test `is_reserved` in the allow branch** — IPv6 `::1` is `is_reserved`, so gating on it there would block localhost on a dual-stack resolver. Exact ordering below the table. | `egress.py:144-170` |
| `is_valid_scope_entry(entry, *, allow_local=False)` | when `allow_local`, accept a **single-label** LDH host (`localhost`); still reject non-LDH, empty labels, IP literals, public-suffix denylist | `egress.py:66-92` (`:86-87`) |
| `host_in_scope(host, scope_hosts, *, allow_local=False)` | pass `allow_local` into the `is_valid_scope_entry` allow-set build | `egress.py:140` |
| `normalize_scope_entry(entry, *, allow_local=False)` | pass `allow_local` into `is_valid_scope_entry` so `localhost` persists at create | `egress.py:109` |
| `validate_target(url, scope_hosts, *, allow_local=False)` | pass `allow_local` into `host_in_scope` (`:199`) and `is_public_ip` (`:210`) | `egress.py:173-212` |

**`is_public_ip` — exact predicate ordering (SSRF-critical).** The `allow_local` relaxation is inserted *between* the existing unwrap and the existing strict return, in this exact order. A wrong order re-opens the cloud-metadata SSRF or breaks IPv6 localhost:

```
def is_public_ip(ip_str: str, *, allow_local: bool = False) -> bool:
    ... parse to `ip` ...
    if isinstance(ip, IPv6Address):          # (i) UNCHANGED — unwrap FIRST (egress.py:166-169)
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
    if allow_local and (ip.is_link_local or ip.is_multicast):
        return False                          # (ii) metadata / link-local / multicast STAY BLOCKED
    if allow_local and (ip.is_loopback or ip.is_private):
        return True                           # (iii) loopback + RFC1918 now allowed
    return ip.is_global and not ip.is_reserved and not ip.is_multicast   # (iv) UNCHANGED strict rule (egress.py:170)
```

Why this exact order, and why **no `is_reserved`** in the allow branch:
- **Unwrap stays first (i).** Otherwise `::ffff:169.254.169.254` (IPv4-mapped) and `2002:a9fe:a9fe::` (6to4 of 169.254.169.254) sail past the loopback/private test behind their `is_global=True` wrapper. Unwrapping first means step (ii) sees the real `169.254.169.254`.
- **Reject before accept (ii before iii).** `169.254.169.254` is link-local, so it is rejected *before* the "accept all private" step (iii) could ever let it through. This is the cloud-metadata block, and it fires *because* (ii) precedes (iii).
- **No `is_reserved` gate in (iii).** IPv6 loopback `::1` reports `is_reserved=True`. If step (iii) also required `not ip.is_reserved`, it would refuse `::1` and localhost would break on any dual-stack resolver. The strict rule (iv) keeps `not is_reserved` for the flag-OFF path (where NAT64 leakage matters); the allow branch must not.

### 6.3 Exact checkpoints that must consult the flag (the full chain to reach `http://localhost:4173`)

1. **Persist scope** — `sessions/service.py:104-113` (`_resolve_scope_hosts`): read `get_settings().allow_local_egress` in `create_session` and thread it in. `_resolve_scope_hosts` calls `normalize_scope_entry` at **TWO** sites and BOTH need the `allow_local` plumb:
   - **(1a)** the explicit-entry loop — `normalize_scope_entry(entry)` (`service.py:105`): without the flag threaded here, a `scope_hosts=["localhost"]` create raises `SessionInvalid`.
   - **(1b)** the blank-scope target-derived default — `normalize_scope_entry(egress.host_of(target))` (`service.py:111`): without the flag threaded here, a **blank-scope** create with `target="http://localhost:4173"` silently fails to seed `localhost` (the default host normalizes to `None` and is dropped), leaving the session with an empty scope even with the flag ON. Missing this site is a silent no-seed, not a loud error — so it is the easier one to overlook.

   Update the docstring at `service.py:100-101` (which documents the opposite — that an IP-literal / `localhost` target "does NOT seed scope").
2. **Create-time fail-fast** — `runs_router.py:63` (`host_in_scope(host_of(target), scope_hosts)`): pass `allow_local=get_settings().allow_local_egress`, or a `localhost` target 400s before the worker runs.
3. **Crawl seed** — `crawl.py:63` (`egress.validate_target(_seed_url(target), scope_hosts)`): pass `allow_local`. Note `settings` is currently read at `crawl.py:67`, *after* this call — the flag read must be hoisted above `:63`.
4. **Crawl re-validation** — `crawl.py:112` (`_revalidate` loops `validate_target` over katana output): pass `allow_local`, or katana's `localhost` URLs are filtered back out.
5. **Fetch runtime** — `fetch.py:107` (`validate_target` per hop in `fetch_url`): add `allow_local` param to `fetch_url`; its callers `fetch_run` (`fetch.py:194`) and `_fetch_assets` (`fetch.py:303`, and the REQ-CE2 `.map` fetch) read the flag from `settings` and pass it.

### 6.4 What stays closed even when the flag is on

- **Link-local / cloud-metadata** (`169.254.169.254`, `fe80::/10`, and 6to4/IPv4-mapped wrappers of them): stay BLOCKED even with the flag ON. This is not incidental — it is enforced by the **explicit link-local/multicast reject as step (ii)** of the `is_public_ip` ordering in §6.2, which runs *before* the loopback/private accept in step (iii). Because the unwrap (step i) precedes it, `::ffff:169.254.169.254` and `2002:a9fe:a9fe::` are reduced to the real `169.254.169.254` and then rejected. The highest-value SSRF pivot stays blocked in every mode. This is a **deliberate narrowing** vs the loose reading "relax all private" (see §9). `crawl_integration_test.py:51` already pins the metadata block and stays green.
- **Scheme, userinfo, malformed URL, DNS-pin** (`egress.py:191-198`, `fetch.py:114`): unchanged.
- **No per-request / URL / query-param override** exists or is added. The relaxation is *only* the process-level env flag; a target URL or request body can never widen the guard. This is the single most important invariant of REQ-CE3.

### 6.5 How it stays off in production

- `Settings.allow_local_egress` defaults `False` (`config.py`); the env var must be *explicitly* `RECON_ALLOW_LOCAL_EGRESS=true` to flip it.
- Documented as developer-local-only in the config comment (§6.1) and this spec's risk section (§8).
- Every guard signature defaults `allow_local=False`, so any un-migrated call site fails closed.

## 7. Touched files

**New behavior (production code — to be written after approval):**
- `apps/platform/src/recon/config.py` — add `crawl_js_crawl: bool = True` (crawl block, `:63-78`), `allow_local_egress: bool = False` (fetch block, `:49`), optional `crawl_fetch_source_maps: bool = True`.
- `apps/platform/src/recon/discover/katana.py` — `build_argv` gains `js_crawl` kw + gated `-jc` append (`:33-39`); update module docstring (`:1-14`).
- `apps/platform/src/recon/discover/crawl.py` — pass `js_crawl` to `build_argv` (`:68-74`); pass `allow_local` to seed `validate_target` (`:63`, hoist settings read) and `_revalidate` (`:108-116`).
- `apps/platform/src/recon/fetch/fetch.py` — `_fetch_assets` (`:302-321`): fetch + store + link the external `.map`; `fetch_url` (`:87-142`) gains `allow_local`; `fetch_run` (`:194`) + `_fetch_assets` (`:303`) read + pass the flag.
- `apps/platform/src/recon/fetch/egress.py` — `allow_local` kw on `is_public_ip` (`:144-170`), `is_valid_scope_entry` (`:66-92`), `host_in_scope` (`:129-141`), `normalize_scope_entry` (`:95-111`), `validate_target` (`:173-212`).
- `apps/platform/src/recon/findings/sourcemapper.py` — new pure `external_map_url(js)` helper; update module docstring (`:8-9`).
- `apps/platform/src/recon/sessions/service.py` — thread `allow_local` through `_resolve_scope_hosts` (`:104-113`) into **both** `normalize_scope_entry` calls: the explicit-entry loop (`:105`) and the blank-scope target default (`:111`); update docstring (`:100-101`).
- `apps/platform/src/recon/api/runs_router.py` — pass `allow_local` to the create-time `host_in_scope` (`:63`).

**Reused unchanged (grounding the "already supported" claims):**
- `apps/platform/src/recon/findings/analyze.py` — `_analyze_assets` origin `"capture"` (`:224`), `_resolve_source_map` (`:463-474`), `_analysis_units` tolerant fallback (`:433-460`). **No change.**
- `apps/platform/src/recon/runs/assets.py` — `set_source_map_ref` (`:79-83`), `AssetRow.source_map_ref` (`:24-35,60`). **No change.**
- `apps/platform/src/recon/storage.py` — `put_blob` + kind `"source_map"` (`:24-26,62-67`). **No change.**

**Tests to add/update:**
- `apps/platform/src/recon/discover/katana_test.py` — flip `:15` (`-jc not in argv`) to assert `-jc` present by default + a gated-off case.
- `apps/platform/src/recon/fetch/egress_test.py` — `allow_local=True` variants (loopback + `localhost` scope allowed; metadata still blocked); existing default-off assertions unchanged.
- `apps/platform/src/recon/fetch/fetch_test.py` (or a new `fetch_source_map_test.py`) — external-map discovery, egress-guarded `.map` GET, `source_map_ref` set, and the two bad-map fallbacks.

## 8. Test plan

### 8.1 Against `test-targets/recon-range` (the answer-key + `scripts/score.mjs` harness)

Preconditions: build + serve recon-range at `http://localhost:4173` (`npm run build:vite && npm run serve:vite`), with the API + worker started with `RECON_ALLOW_LOCAL_EGRESS=true`.

Native-crawl runbook (no extension):
1. Create a session with `scope_hosts=["localhost"]` and an authorization ack (with the flag on, `localhost` now persists — §6.3 checkpoint 1).
2. `POST /runs` with `target="http://localhost:4173"` (explicit `http` — recon-range serves plain HTTP, and `build_argv`/`_seed_url` default to `https://` otherwise, `katana.py:32` / `crawl.py:138-141`). The create-time check passes (checkpoint 2).
3. Worker walks: **discover** (katana `-jc` finds the entry + lazy chunks — REQ-CE1), **fetch** (each `.js` + its external `.map` fetched through the guard, `source_map_ref` set — REQ-CE2), **analyze** (origin `"capture"` recovers original sources).
4. `npm run score -- --run <run_id> --tenant <tenant_uuid> --base http://localhost:8000` (the `--tenant` is the UUID of whatever tenant created the session, passed as `X-Tenant-Id`, `score-cli.mjs:12`).

Per-change verification:
- **REQ-CE1**: hold `RECON_CRAWL_FETCH_SOURCE_MAPS=true` **fixed** (so source-map recovery is not the confound) and toggle **only** `-jc`. Assert on **discovered-asset presence, not endpoint count**: with `RECON_CRAWL_JS_CRAWL=false` the lazy-chunk asset URLs (`inventory`/`social`/`live` `.js`) are **absent** from the run's asset manifest; with the default (`true`) katana parses the entry bundle's `import()`/chunk map and those three `.js` assets **enter** the manifest. The asset-presence delta is the `-jc` proof.
  - *Why not assert on endpoint count:* §1 attributes only **+1** endpoint to `-jc` alone (9→10) and **+5** to source-maps (10→15). The 6-endpoint swing of the lazy chunks (`ep-inv`, `ep-checkout`, `ep-session`, `ep-config`, `ep-feedback`, `ep-ws` — `answer-key.json:11-16`) only fully materializes once those chunks' `.map`s are *also* recovered (their minified shapes defeat the extractor otherwise, §1). So the 6-endpoint delta holds with source-maps ON but is **confounded** by source-maps — endpoint count is a poor `-jc` signal. Asset presence is the clean one: without `-jc` those three chunks are never discovered → never fetched → never map-recovered, so their endpoints are unreachable regardless of the source-map flag.
- **REQ-CE2**: rely on the two **robust** signals that aggregate correctly across a multi-asset run, not on the fragile one:
  - **Robust (primary):** `cov.sources_recovered > 0` (`score.mjs:33`) — summed across every asset by `_merge_coverage_payloads` (`findings/queries.py:330`, `sum(...)`) — **and** ≥1 endpoint/param occurrence with `source_path != "input.js"` (`score.mjs:35-36`), which is derived from the findings themselves, not from the coverage blob. These two are the load-bearing REQ-CE2 proof.
  - **Fragile (secondary, do not depend on):** `cov.source_map ∈ {capture,inline}` (`score.mjs:32`, `answer-key.json:29`). `source_map` is **not merged** across assets — `findings/queries.py:331` takes only the highest-id (last-analyzed) asset's value (`payloads[0]` after `order_by(RunEvent.id.desc())`), so it can read `"none"` even when sources were recovered for earlier assets (§8.3). Treat a green `score.mjs:32` as corroborating, never as the gate.
  With the `.map` fetch stage the two robust signals pass; disable it (`RECON_CRAWL_FETCH_SOURCE_MAPS=false`) → `sources_recovered=0` and the minified-shape endpoints (`ep-inv`/`ep-checkout`) miss, confirming the stage is load-bearing.
- **REQ-CE3**: with the flag **off**, step 1 fails (`SessionInvalid` on `localhost`) / step 2 400s — proving the guard is closed by default. With it **on**, the loop completes. A negative check: a run targeting `http://169.254.169.254` stays blocked in both modes.
- **Overall**: `PASS = all should_find + params + must-find secrets + source-map re-extraction` (`score.mjs:49`). The same run is the acceptance gate.

### 8.2 Platform unit tests (host lane, colocated)
- `katana_test.py`: `-jc` present by default; absent when `crawl_js_crawl=False`.
- `egress_test.py`: `is_public_ip("127.0.0.1", allow_local=True) is True`; `is_public_ip("169.254.169.254", allow_local=True) is False` (metadata stays blocked); `is_valid_scope_entry("localhost", allow_local=True) is True`; `validate_target("http://localhost/x.js", ["localhost"], allow_local=True)` resolves; **all default-off calls unchanged**.
  - IPv6 cases pinning the exact `is_public_ip` ordering (§6.2) — these are the ones a naive "reject reserved / relax all private" predicate gets wrong:
    - `is_public_ip("::1", allow_local=True) is True` — IPv6 localhost. Guards against gating on `is_reserved` in the allow branch (`::1` is `is_reserved`), which would break localhost on a dual-stack resolver.
    - `is_public_ip("::ffff:169.254.169.254", allow_local=True) is False` — IPv4-mapped cloud-metadata; proves the unwrap (step i) runs before the reject (step ii).
    - `is_public_ip("2002:a9fe:a9fe::", allow_local=True) is False` — 6to4 of `169.254.169.254`; same unwrap-then-reject path.
    - `is_public_ip("fe80::1", allow_local=True) is False` — IPv6 link-local stays blocked with the flag on.
- fetch source-map test (mock `httpx` transport, like `fetch_test.py`): a JS body ending `//# sourceMappingURL=app.js.map` → the `.map` is fetched via `validate_target`, stored as `source_map`, `set_source_map_ref` called; a 404/blocked `.map` → asset stays `fetch_ok`, `source_map_ref` unset, no `fetch_failed`.

### 8.3 Known harness caveat (not a code change)
`coverage.source_map` is **not** aggregated across assets — `_latest_coverage` reports the highest-id asset's value (`findings/queries.py:331`). For recon-range every chunk carries a `sourcesContent` map (enforced by `build-invariants.test.mjs`), so if the `.map` fetch succeeds for every asset, the last-analyzed asset is `"capture"` and the gate passes. If one chunk's `.map` were missing, the gate could read `"none"` while `sources_recovered` (summed) is still `>0`. Documented as a risk (§8/§9), not fixed here.

## 9. Risks / debt

1. **SSRF (top risk — REQ-CE3 is the guard's off-switch).** `RECON_ALLOW_LOCAL_EGRESS=true` relaxes the loopback/RFC1918 block that exists precisely to stop the fetcher becoming an SSRF pivot into internal infrastructure. If it leaks into a shared/staging/prod environment (a copied `.env`, an inherited container env, a CI secret), a hostile in-scope target that resolves to an internal address could reach internal services. Mitigations, all in this design: default `False` + explicit env var; **link-local/cloud-metadata (`169.254.169.254`, `fe80::/10` + their 6to4/IPv4-mapped wrappers) stays blocked even when the flag is ON** — enforced by the explicit link-local/multicast reject at step (ii) of the `is_public_ip` ordering (§6.2/§6.4), which is checked *before* the loopback/private accept, so the metadata pivot is never reachable regardless of the flag; **no per-request/URL override** (the flag is process-level only); the DNS-pin and scheme/userinfo checks are untouched; loud documentation in the config comment and here. Residual: OS/network egress isolation is still deferred (`egress.py:11-14`), so this remains an application-level control — do not treat "flag off" as a substitute for network isolation in production.
2. **Coverage-honesty erosion.** Reusing `"capture"` for a crawl-fetched map overloads the origin (a reader of `coverage.source_map` can't tell a capture-ingested map from a crawl-fetched one), and `source_map` isn't merged across assets (`findings/queries.py:331`), so the score gate reads a single asset's status (§8.3). Low-stakes for recon-range (all chunks have maps), but a cleaner design would add a distinct `"crawl"` origin — deferred (touches `analyze.py:451` + `answer-key.json:29`).
3. **`-jc` overturns a deliberate, tested decision and adds crawl load.** The change reverses `katana.py:1-14` + `katana_test.py:15`; the docstring and test must be rewritten, not just extended. `-jc` also increases discovered URLs → more fetch/analyze work (bounded by dedup + `crawl_max_assets`, §4) and katana flag semantics "drift between releases" (`katana.py:11`), so the config kill-switch is the safety valve. Secondary: the extra `.map` fetch per asset (REQ-CE2) doubles outbound requests to a host unless it also takes a politeness slot (§5.2).

**Contradictions with the stated plan (surfaced, not silently accepted):**
- (a) The codebase **explicitly forbids** `-jc` by design and by a passing test (`katana.py:1-14`, `katana_test.py:15`) — this is not a gap to fill but a decision to reverse. Flagged so the reviewer knows the rationale ("Vespasian parses, not katana") is being consciously overridden by new empirical evidence (9→10 on recon-range).
- (b) The plan says "wire per-asset `source_map_ref`" as if analyze needs work — it does **not**. `_analyze_assets` already passes `source_map_origin="capture"` and reads `asset.source_map_ref` (`analyze.py:218-229`), and `set_source_map_ref` already exists (`assets.py:79-83`). The only new code is the fetch-stage discover/fetch/store/link. This *reduces* the change surface vs the plan's framing.
- (c) The plan scopes the egress relaxation to `is_public_ip`/`is_valid_scope_entry`, but reaching `localhost` requires the flag at **five** checkpoints across three modules (§6.3), including a session-create docstring that documents the *opposite* behavior (`service.py:100-101`). And I deliberately **narrow** "relax private" to exclude link-local/metadata (§6.4) — a safer default than the plan's loose wording; confirm this narrowing is acceptable.

## 10. Out of scope / YAGNI

- No OS/network egress isolation (egress proxy / netns / firewall) — still deferred (`egress.py:11-14`, `crawl.py:9-11`).
- No headless crawl requirement — the measured 15/15 is via the **standard** (non-headless) crawl (`config.py:72` default `crawl_headless=False`); `-jc` + source-map recovery reach parity without JS execution. Headless stays the opt-in it already is.
- No legacy single-URL source-map fetch (only the multi-asset crawl path; §5.2).
- No dedicated `"crawl"` source-map origin (reuse `"capture"`; §5.2 / §9).
- No coverage-merge change for `source_map` aggregation (§8.3).
- No CI wiring — the recon-range gate stays on-demand (needs a served local target + the egress flag).
- No new detector forms — the recon-range blind spots (`answer-key.json:21-27`) remain expected-missing.
