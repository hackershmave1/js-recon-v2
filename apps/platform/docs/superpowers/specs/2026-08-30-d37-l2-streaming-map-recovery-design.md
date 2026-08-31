# D37 Layer 2 — streaming big source-map recovery (design)

Status: **BUILT** (2026-08-31) — all 5 slices shipped + committed on `feat/d37-l2-streaming-map-recovery`, each an
isolated commit with an adversarial design + higher-model code review (all CLEAN / fixes-folded), full host fast
lane + integration lane green, and the real-binary streaming chain validated in-container. §4 DESIGN gate =
**SHIP-WITH-CHANGES** (2026-08-30); all 6 must-fixes + 4 should-fixes folded (see "Adversarial review outcome").
Scope confirmed by user: **all 5 slices**. **Slice-5 scope decision (2026-08-31, via subagent design discussion +
§4):** slice 5 ships as "streamed Phase A" — it closes the slice-3-review cross-chunk cap divergence (#3) but
DEFERS the D28 double-recover (a bounded-disk recover-once reuse cache; perf-only, entangled, deterministic so
never wrong output) to its own tracked slice; see `DEBT.md` D37/D28.
Supersedes the "L2" plan block in `DEBT.md` D37. Related: D32 (map recovery), D36 (fetch
heartbeat), D28 (double-recover), D23 (heartbeat family), REQ-D3/D5/C2/A3, REQ-S2.

## Adversarial review outcome (§4 gate 1) — SHIP-WITH-CHANGES

All ~26 anchors verified accurate; the reveal-integrity invariant confirmed SOUND as built (analyze
locates offsets in `unit_text.encode("utf-8")`; reveal reproduces the same via
`recover_file_text`/`beautify_if_minified` and re-encodes — same function, same bytes). R6
(streaming content-address) verified SOUND: SHA-256 is streaming-invariant, dedup keys on the object
key not the S3 ETag, so an incremental hash over a **raw binary** temp copy equals `object_key(bytes)`.
Folded changes:
- **M1 (build-order blocker).** Slice 3 must NOT change `recover_sources`'s return type — it has 3
  callers (`analyze.py:874` s3, `analyze.py:971` Phase-A s5, `sources.py:259` s2) + ~7 monkeypatching
  tests, all mypy-strict. Slice 3 ADDS `iter_recovered_files(...) -> Iterator[(rel_path, raw_bytes)]`
  and migrates ONLY `_analysis_units`; `recover_sources` stays (re-implemented as a thin wrapper over
  the generator so the sort/containment/cap live once) until slice 5 migrates Phase A.
- **M2 (integrity write contract).** The beautified tree is written
  `open(p,"wb").write(unit_text.encode("utf-8"))` — binary, no added newline/BOM; offset re-location
  re-reads those bytes + `locate_snippet(bytes.decode("utf-8"))`. The cumulative-write budget stops at
  WHOLE-FILE granularity (never mid-file: reveal re-derives the full file independently, so a mid-file
  cut desyncs → 409). Test: on-disk file bytes == `recover_file_text(...).encode("utf-8")`.
- **M3 (scan_dir path attribution).** New function, NOT `_index_from_path` (basename-based → collides
  on nested same-basename files; Kingfisher's path form is ambiguous per `kingfisher.py:329-330`). Pin
  1.106.0's reported path form against a NESTED fixture; invert via
  `relpath(realpath(reported), realpath(scan_root))`; handle non-`.js` extensions (test Kingfisher
  scans them, or force a scannable ext).
- **M4 (scan-output cap).** `run_engine` buffers whole stdout, capped at `engine_max_output_bytes`
  (32 MiB, `engines.py:153-156`); `--no-dedup` on a 96 MiB tree can exceed it → `EngineError` → retry
  loop. Scale the scan `max_output_bytes` with tree size (or soft-partial on overflow, honest REQ-C2);
  don't over-claim "whole 96 MiB" for the secret lane.
- **M5 (slice-2 honesty).** A single map-sized `sourcesContent` entry still loads whole into the API
  parent (prlimit bounds the child, not the parent read). Slice 2 is restated as "bounds the parent to
  largest-single-file, worst case ≈ map size"; optionally cap the single-file read with an honest status.
- **M6 (D28 reuse bound).** Don't hold N per-asset trees unbounded. Bound the reuse (small-K cache +
  re-recover tail, OR delete each tree immediately after the loop consumes it); state the bound + cost.
- **S1:** drop `prlimit --fsize` as the tree bound (RLIMIT_FSIZE is per-FILE, not cumulative — category
  error); the Python cumulative-write budget IS the bound. **S2:** slice-2 tests must move the fake seam
  onto the new single-file path (they monkeypatch `recover_sources` today). **S3:** carry
  `dirnames.sort()`/`sorted(filenames)` into the generator explicitly. **S4:** beat before AND after
  each file's scan (a single giant file's scan must not outlast the stall window).
- Nit: invariant 1's beautify call is `sources.py:268-270` (271 is the `return None` fall-through).

## Problem

D37 L0+L1 (shipped, on `main`) bounded the `sourcemapper` child's memory (`prlimit --as`, 3 GiB)
and the container (`mem_limit`), and raised the `.map` INPUT cap to 96 MiB. But recovery is still
**whole-in-RAM at 8 points**, and the recovered OUTPUT is still hard-capped at
`engine_max_output_bytes` = 32 MiB *in RAM* — so a 96 MiB map recovers only its first ~third, and a
user click can OOM the API process on demand. The map input cap can't safely climb further until
recovery streams.

### The 8 whole-in-RAM hotspots (agent-verified anchors)

| # | Where | Anchor | Whole-in-RAM |
|---|-------|--------|--------------|
| 1 | `.map` fetch accumulation | `fetch/fetch.py:307-319` | `bytearray` + `bytes()` copy of whole map |
| 2 | content-address (sha256) | `storage.py:44-49` | whole `content` hashed in one shot |
| 3 | blob put/get | `storage.py:77-88` | `Body=content` / `.read()` — no `upload_fileobj` |
| 4 | analyze pre-pass recover | `analyze.py:917,971,979-980` | whole map + whole `recovered.files` |
| 5 | analyze per-asset recover | `analyze.py:874,903-906,917` | whole map + whole beautified `units` list |
| 6 | sourcemapper adapter | `sourcemapper.py:110,147,162,168-192` | whole `map_bytes` in, whole `list[RecoveredFile]` out |
| 7 | viewer click | `probe/sources.py:234,259` | whole map + whole tree to serve ONE file (API process) |
| 8 | reveal click | `probe/reveal.py:332,336` | whole map + whole tree to serve ONE file (API process) |

## Load-bearing invariants (the design must not break these)

1. **Reveal integrity (REQ-S2, D32-B1).** A recovered secret's offset is located in
   `beautify_if_minified(decode(raw_recovered_bytes))` (`analyze.py:1291-1293,1302-1305`), and
   `probe/sources.recover_file_text` reproduces the SAME function on the SAME bytes at reveal
   (`sources.py:268-270`); `probe/reveal._derive` re-slices and re-hashes, failing **closed (409)** on any
   drift (`reveal.py:283-285`). Whatever bytes L2 scans MUST be the exact bytes reveal reproduces.
   `beautify_if_minified` is deterministic (pinned `_OPTS`, `deobfuscate.py:36-37`), fail-soft to raw
   over a 1 MiB input cap (`_MAX_BEAUTIFY_BYTES`, `:32`) — so "same function, same bytes" holds on
   both sides regardless of the cap.
2. **Content-addressing needs the whole stream (REQ-D2).** `object_key` = `sha256(content)`
   (`storage.py:48`); the key can't be formed without consuming the whole map. L2 buffers to a temp
   **file** (incremental hash), never RAM.
3. **Egress guard on every map hop.** The `.map` GET runs through `fetch_url` → egress validation
   (intact today); streaming must keep every hop guarded, unchanged.
4. **Idempotency / deterministic order (REQ-A3).** `_walk_recovered` sorts dir + file names so the
   set kept under the cap is identical across retries (`sourcemapper.py:174-177`). Streaming must keep
   a total, stable traversal order so re-analysis yields the identical finding-hash set.
5. **Honesty / fail-soft origin fallback (REQ-C2/D5).** An `inline`/`capture` map that fails recovery
   falls back to bundle analysis with a visible `<origin>-error` status (`analyze.py:876-883`); an
   `uploaded` map re-raises. A "skipped" oversized map stays a distinct coverage status. L2 must
   preserve these exact fall-throughs (never silently drop coverage).
6. **The L0 subprocess bound stays.** `run_engine(memory_limit_bytes=...)` `prlimit` wrapper
   (`engines.py:84-104`) still guards every recovery. Streaming changes the adapter's I/O, not its
   isolation.
7. **Lease safety (REQ-A4, D23/D36).** analyze beats once per asset (`analyze.py:354`); a long
   per-file loop must beat between files so a peer can't reclaim the RUNNING job mid-recovery.

## Design decisions (settled before slicing)

- **Recover-to-disk, read one file at a time.** `sourcemapper` already writes the whole tree to an
  on-disk `out_dir` (`sourcemapper.py:144-150`) and `_walk_recovered` reads it back. The only change
  is: yield `(path, raw_bytes)` file-by-file (a generator / callback) instead of returning a whole
  `list[RecoveredFile]`. RAM bound becomes **largest single file**, not the whole tree.
- **Beautify-to-disk, then scan the tree in ONE subprocess.** `kingfisher.scan_many` already works by
  writing units to a temp dir and scanning the **directory** (`kingfisher.py:272-282`) — so we point
  Kingfisher at an on-disk *beautified* tree instead of in-RAM `bytes` tuples. One subprocess (no
  per-file fork DoS), no whole-tree-in-RAM, and the scanned bytes == the bytes reveal reproduces
  (invariant 1). New `kingfisher.scan_dir(path) -> {rel_path: [RawSecret]}` (paths keyed by
  rel-path, not slot index).
- **Content-address from a temp file.** New streaming storage primitives:
  `storage.put_blob_stream(fileobj|path)` (incremental `sha256` while copying to a spooled temp file,
  then `upload_fileobj`) and `storage.download_blob_to_path(key, dest)` (`download_fileobj`). Keep the
  existing `bytes` APIs for small blobs (they're correct and widely used).
- **On-disk tree bound (part c).** The recovered tree ≈ Σ `sourcesContent` ≤ the map size (96 MiB
  cap), and the beautified tree ≈ same magnitude (beautify only runs ≤ 1 MiB/file, else raw). The bound
  is a **cumulative-write budget** in OUR beautify-to-disk loop, stopping at WHOLE-FILE granularity
  (skip/stop past it with a logged, honest partial status — never truncate mid-file, per M2), plus the
  documented input-cap bound on the recovered tree. NOT `prlimit --fsize` (RLIMIT_FSIZE is per-FILE, not
  cumulative — S1). (No tmpfs: it's RAM-backed and would count against `mem_limit`.)
- **Temp location.** New `RECON_ENGINE_WORKDIR` setting (default = OS temp) so streaming temp files +
  the recovery workdir are tunable to a sized volume in prod. `sourcemapper` currently uses
  `tempfile.TemporaryDirectory(prefix="sm-")` at the OS default (`sourcemapper.py:142`).

## Slices (each ships + reviews independently; isolated commit)

### Slice 1 — streaming storage primitives  ·  S  ·  foundation
- **Add** `put_blob_stream` / `download_blob_to_path` to `storage.py` (incremental sha256 +
  `upload_fileobj`/`download_fileobj`). Add `RECON_ENGINE_WORKDIR` to `config.py`.
- Pure addition, **no behavior change** — existing `put_blob`/`get_blob` untouched.
- Tests: `storage_test.py` — key byte-identical to `object_key(bytes)` for the same content; round-trip
  put-stream → download-to-path; large (>chunk) content streams without whole-RAM.
- Invariants: 2 (content-address), 3 (n/a here).

### Slice 2 — bound the viewer / reveal path  ·  S–M  ·  highest value (bounds API parent to largest-single-file)
- `sourcemapper`: add `recover_one_file(map_path, target_path) -> str|None` (recover-to-disk, read
  ONLY the target file; whole tree on disk but only one file in API RAM). Map input read from a temp
  file (via slice-1 `download_blob_to_path`), never `get_blob` whole.
- `probe/sources.recover_file_text` + `_recovered_content` (`sources.py:223-271`) and
  `probe/reveal._recovered_byte_space` (`reveal.py:314-339`): stream map→temp file, call
  `recover_one_file`, beautify the ONE file identically. Whole map leaves API RAM; the whole tree
  leaves too EXCEPT the single target file.
- **Honesty (M5):** this bounds the API parent to the *largest single recovered file*, NOT to a fixed
  ceiling — a map with one map-sized `sourcesContent` entry still spikes ~that size. Worst case ≈ map
  size; typical case drops from whole-tree to one-file. Optionally cap the single-file read with an
  honest denial/partial status.
- Invariants: 1 (reveal integrity — same beautify), 5, 6. The L0 `prlimit` already bounds the child;
  this removes the API *parent's* whole-map + whole-tree footprint.
- Tests: `probe/reveal_recovered_test.py` round-trip still byte-identical — but the fake seam must MOVE
  to `recover_one_file` (S2: those tests monkeypatch `recover_sources` today, `reveal_recovered_test.py:79/100/105`,
  `sources_test.py:37`); a large-map case asserts only one file is materialized; 409-on-drift preserved.

### Slice 3 — stream the analyze recovery  ·  M–L  ·  the big one (full big-map recovery)
- **ADD** `sourcemapper.iter_recovered_files(map_source, ...) -> Iterator[(rel_path, raw_bytes)]`
  yielding in stable order — carry `dirnames.sort()`/`sorted(filenames)` + the realpath-containment +
  the whole-file cap-sentinel from `_walk_recovered` (`sourcemapper.py:168-192`) INTO the generator (S3).
  **Do NOT change `recover_sources`'s signature (M1):** re-implement it as a thin wrapper that
  materializes the generator, so Phase A (`analyze.py:971`, slice 5), `sources.py:259` (slice 2), and
  the ~7 monkeypatching tests keep working until slice 5 migrates them. Only `_analysis_units` moves now.
- `analyze._analysis_units` (`analyze.py:853-907`): instead of the whole beautified `units` list
  (`:903-906`), stream each recovered file → `beautify_if_minified(raw.decode("utf-8","replace"))` →
  write **byte-exact** `open(p,"wb").write(text.encode("utf-8"))` (M2: binary, no added newline/BOM) to
  a beautified on-disk tree, under a cumulative-write budget that stops at WHOLE-FILE granularity
  (skip/stop past it with an honest partial status — never mid-file). Return a tree handle + stable
  path list, not RAM text.
- Secret scan (M3): new `kingfisher.scan_dir(tree_root) -> dict[rel_path, list[RawSecret]]` (NOT
  `_index_from_path`) — invert Kingfisher's reported path via `relpath(realpath(reported),
  realpath(root))`, pinned against a NESTED fixture; handle non-`.js` extensions. In
  `_record_recovered_secrets` (`analyze.py:1285-1334`) locate each sighting by re-reading the ONE
  beautified file's bytes + `locate_snippet(bytes.decode("utf-8"))`. Endpoint `extract()` + `internal_ip`
  run per beautified file, read one at a time.
- Scan-output cap (M4): pass a `max_output_bytes` scaled to the tree size into `scan_dir`'s `run_engine`
  (or treat overflow as an honest coverage-partial), so `--no-dedup` JSONL on a 96 MiB tree doesn't
  `EngineError`-retry-loop (`engines.py:153-156`).
- **Per-file heartbeat** (part d / S4): beat BEFORE and AFTER each file's scan/extract (closes the
  `NOTE(DEBT)` at `analyze.py:896-902`) so one giant file's scan can't outlast the 30 s stall window.
  The 32 MiB in-RAM output cap becomes an on-disk cumulative bound; recovers the WHOLE 96 MiB map
  (endpoints; the secret lane is bounded by M4).
- Fix the stale `deobfuscate.py:13` docstring ("secrets are NEVER beautified" — false for the D32-B1
  recovered path).
- Invariants: 1 (beautified-both-sides, byte-exact), 4 (stable order = same hash set), 5, 7.
- Tests: on-disk beautified file bytes == `recover_file_text(...).encode("utf-8")` (M2); recovered-secret
  reveal round-trips + 409-on-drift; `scan_dir` attributes nested same-basename files correctly (M3); a
  synthesized >32 MiB map recovers fully (was truncated); deterministic order across two runs.

### Slice 4 — stream the .map fetch → disk  ·  M
- `_fetch_and_store_source_map` (`fetch.py:840-892`): stream the map body → temp file (incremental
  hash, slice-1 `put_blob_stream`) instead of `fetch_url` whole-bytes → `put_blob`. Needs a streaming
  `_fetch_hops` variant that writes body → file handle instead of the `bytearray` accumulation
  (`fetch.py:307-319`), keeping the per-chunk cap + egress guard.
- Optional (fold in): a mid-body heartbeat on the map fetch so `fetch_secondary_timeout_seconds` (20 s,
  no beat today — `config.py:86`) can rise for a big map on a slow origin (the D36-deferred piece).
  Keep the lease-safety validator (D36 `_check_fetch_lease_safety`) honest.
- Invariants: 2, 3 (egress guard every hop), 7 (if heartbeat added).
- Tests: `fetch_multi_test.py` — big map streams to a blob without whole-RAM; egress still validated;
  soft-skip semantics unchanged.

### Slice 5 — fix D28 double-recover  ·  S–M  ·  perf only (entangled)
- Today `build_export_index` (Phase A, run-level pre-pass) recovers each mapped asset's map to harvest
  export consts (`analyze.py:971`), then the per-asset loop recovers the SAME map again for extraction
  (`analyze.py:874`) — 2× subprocess spawns per mapped asset (`NOTE(DEBT D28)`, `analyze.py:1009-1015`).
- Cross-chunk resolution NEEDS the full run-level export index before ANY asset's extraction, so we
  can't fold Phase A into the loop. Fix = **recover once, reuse the on-disk tree** (slice-3 machinery)
  keyed by `run_asset_id`; the per-asset loop reuses that tree instead of re-recovering, then deletes it.
- **Bound (M6):** DO NOT retain N trees unbounded (a 500-mapped-asset crawl ≈ N × up-to-96 MiB of temp
  = disk blowout). Pick ONE and state its cost: (a) a small fixed-K reuse cache with re-recover on the
  evicted tail, OR (b) key retention to a total disk budget and delete each tree the instant the loop
  consumes it. NOT "LRU with re-recover-on-miss" presented as an interchangeable hedge — under pressure
  that silently reintroduces the double-recover this slice removes. Re-recovery stays correctness-safe
  (deterministic), so a miss is a perf cost, never wrong output.
- Invariants: 4 (identical recovered set both phases — guaranteed by reusing the same tree OR by
  deterministic re-recovery), 5.
- Tests: a mapped asset spawns `sourcemapper` ONCE on the reuse-hit path (assert spawn count); the
  disk-retention bound holds on a many-asset run; exports + extraction identical to pre-slice output.

## Ordering & dependencies

```
1 (storage) ──┬─▶ 2 (viewer/reveal bound)      ← independent user-facing win, ship first after 1
              ├─▶ 3 (analyze stream) ──▶ 5 (D28 reuse, builds on 3's on-disk tree)
              └─▶ 4 (fetch stream)
```
Build order: **1 → 2 → 3 → 4 → 5.** 2 is the highest-value/most-contained; 5 depends on 3's on-disk
tree machinery; 4 is independent of 3.

## Risks / open questions for the adversarial review to attack

- **R1 (integrity).** Does writing beautified text to disk and scanning it via `scan_dir` reproduce
  EXACTLY the bytes `recover_file_text` beautifies at reveal? (Path normalization, trailing newline,
  encoding round-trip `decode("utf-8","replace")` → write → Kingfisher read.) A single-byte drift =
  systematic 409s.
- **R2 (Kingfisher path attribution).** `scan_dir` must map a sighting's reported path back to the
  recovered rel-path unambiguously when rel-paths contain nested dirs / odd chars (today it's flat
  `<i>.js`). Risk of mis-attribution or collision.
- **R3 (per-file RAM bound honesty).** A single giant `sourcesContent` entry (>1 MiB, served raw) still
  loads one large file into RAM for extract/scan/locate — is "largest single file" an honest bound, or
  can one source be map-sized? Document the true worst case.
- **R4 (on-disk tree bound).** Is the recovered + beautified tree genuinely ≤ input cap, or can
  beautify expand it past the disk budget before the cumulative cap trips? Where exactly does the
  write budget stop, and is the partial result honest (REQ-C2)?
- **R5 (D28 reuse memory).** Holding N per-asset beautified trees on disk between Phase A and the loop
  — does that blow the disk budget on a many-asset crawl? Is re-recover-on-miss actually cheaper than
  the LRU?
- **R6 (streaming content-address correctness).** `put_blob_stream` must produce a key byte-identical
  to `object_key(same_bytes)` or dedup/idempotency breaks. Prove equality.
- **R7 (lease).** Per-file heartbeat cadence vs. a pathological many-tiny-file tree (beat overhead) and
  a few-giant-file tree (a single file's beautify+scan outrunning the stall window).
