# Tech debt register

Known, deliberate debt - written down so it's visible to the next contributor (and
the next session) instead of living only in an AI's memory. Add here when you defer
something on purpose; link the code with a `# NOTE(DEBT):` comment where it helps.
Effort: S (hours) · M (a day-ish) · L (multi-day).

> **History (2026-09-04):** the resolved and won't-fix records for the pre-2026-09-03 register
> (D1-D39 - the D31-D37 dogfood fixes, the earlier D1-D30 items, and the D17/D18/D19/D23 residual-risk
> acceptances) were removed from this file to keep the live register lean. They are preserved in git
> history - `git log -p -- DEBT.md` recovers the full "why isn't X done" trail. This register now tracks
> only the currently-open items.

> **Pruned (2026-11-17, #214):** resolved and obsolete entries (D40-D43, D45, D47-D53, D55-D63, D67, D68,
> D71, D74, D85) were removed. They live in git history: `git log -p -- DEBT.md`.

## Priority (agreed 2026-11-17, #214)

**Now** - wrong or missing results, or the capture path misleads you:
- **D69 + D70** (#267): session-rotation races can bleed captures into the wrong engagement or session; tiny fixes, do together.
- **D80** (#268): webpack module identity breaks recall on the most common bundler; the scoped fix is known.
- **D77** (#269): every extension capture shows "no tech stack", and the extension is now the main capture path.
- **D64** (#270): the threat model is generic without call-site code; the design is already decided.
- **D44a check** (#271): re-check whether the ADR-0010 quarantine already closes the cross-origin-import scope bypass; Now only if it still leaks.

**Next** - real but narrower:
- **D78**: stale `localhost` crawl scope still reaches katana; one filter call, and fail-closed is a repo invariant.
- **D82 + D83**: later capture passes miss earlier files and re-read everything; routine under the multi-pass model, fix together.
- **D84**: an identity bump during an open capture duplicates findings; cheap, only bites around a deploy.
- **D81**: the extension CI lane can pass a hung suite; cheap CI-trust fix.
- **D66**: the 5-minute orphan window lets a direct API call re-trigger a long threat-model run and pay twice.
- **D65**: a saved hyphenated model id may point at a dead model with no Settings flag.
- **D75**: huge bundles lose jump-to-location marks in Pretty print; raw mode is a workaround.

**Later** - rare, accepted, or maintainability:
- **D76**: base-URL rows show "no exact location"; cosmetic, the finding is correct.
- **D79**: rare URL shapes stay as two rows; accepted, fix only if seen in data.
- **D44b**: `captureEverything` has no auto-expire; low frequency since the confirm shipped.
- **D46**: Burp/Caido/HAR export is a feature, not a defect.
- **D54c**: a second operator needs the `seed-admin` CLI; solo use is the stated audience.
- **D72**: a rare delete race orphans two blobs; same accepted class as D47, wait for the storage GC.
- **D73**: files over the 300-line cap; split `threat_model/service.py` as part of D64.

## Open debt

### 2026-11-03 extension end-to-end walkthrough — OPEN (D78–D81)

Found while driving the unpacked extension against the live stack (the findings-summary 404 found in the
same walkthrough was fixed with it).

#### D81 · extension CI lane passes a test suite that hangs [S]  ·  ci

`.github/workflows/ci.yml`'s extension lane runs each `tests/test_*.mjs` and checks only the exit code.
A suite whose promise never settles lets Node drain the event loop and exit 0 with no output — a false
green (C7 review: `test_popup_api.mjs` silently stopped running this way; fixed there with an
"exited before finishing" guard). **Fix:** have the lane require each suite's final sentinel line (or
run them under `node --test`, which reports a never-settled test as a failure).
#### D80 · webpack module identity: names are poisoned bundle-wide, not per module [M]  ·  recall

`_base_env._declared_names` poisons a name bound more than once ANYWHERE in the file, and webpack
emits many modules per chunk that each reuse the same short names (`t`, `s`, `o`). So on real
multi-module chunks: a local const base used by a wrapper is unresolvable (example_js_files/02: the
inferred wrapper `c` is recognized but its base `s=o+i` is poisoned because module 9004 reuses `o`,
so its calls land as unresolved rows attributed to `c`; corpus 0/7), and a `.create({baseURL})`
FACTORY reached through a module alias (`t.A.delete(...)`) is deliberately not claimed — `t.A` is
"export A of whatever module `t` is", and a sibling module may reuse `t` for a store (adversarial
review 2026-11-04 proved confirmed FPs). recon-range webpack-nomap therefore misses
`DELETE /api/v1/session` (a strict-xfail known-red test pins it). **Fix:** scope poisoning and
instance identity per webpack module body (`_modulegraph._webpack_module_defs` already finds the
disjoint bodies), keying factories by (module id, export) via `collect_webpack_requires`.

#### D79 · identity v3 placeholder edge cases left verbatim (accepted) [S]  ·  recall

Identity v3 (`normalize._templatize_path`, 2026-11-04) collapses a whole `${...}` segment after a
static segment to `{id}`. Known shapes it leaves alone, each an honest (extra-row) outcome, never an
over-merge: (1) a literal host plus a LEADING placeholder (`https://api.x.com/${userId}`) stays verbatim,
so its readable/minified copies are still two rows; (2) `${...}` masking counts braces without parsing JS
strings, so `${fmt("{")}` swallows the rest of the URL into one placeholder; (3) a base-URL prefix rule
written against a v2 path with a non-leading `${var}` (`/api/${ver}`) no longer matches the v3 `{id}`
path (hash-keyed selection rules ARE migrated by 0033). **Fix when seen in data:** treat a leading
placeholder as a param when the host is literal; string-aware brace scan; rewrite `path_prefix` in a
migration via `normalize.rewrite_v2_value`.

#### D78 · katana keeps a stored `localhost` crawl scope after the local-targets switch is turned off [S]  ·  security

`discover/katana.build_argv` passes the session's raw stored `scope_hosts` as `-crawl-scope` without
re-checking them against the current `RECON_ALLOW_LOCAL_EGRESS` (every other egress path does). A mixed
scope stored while the switch was on (`["acme.io","localhost"]`) keeps `-crawl-scope localhost` after it is
turned off. Bounded by the seed guard, `-field-scope rdn` and the `_revalidate` post-filter; katana's own
requests are the documented residual. **Fix:** filter `scope_hosts` through `egress.is_valid_scope_entry`
with the current flag in `build_argv`.

### 2026-09-08 swarm review — OPEN (D64)

Three-agent architecture swarm (token-budget / agent-tool-call / codebase-audit) commissioned to design
threat model code-context enrichment. Unanimous verdict documented below.

#### D64 · Threat model sees endpoint URLs but not the code — LLM produces generic threats [M]  ·  correctness — Tier 1

The threat model LLM receives endpoint URLs, secret counts, tech stack, and GraphQL operation names
(`threat_model/service.py:_assemble_context`). It has no visibility into *how* endpoints are called —
no parameter names, no auth patterns, no request body shapes, no business logic signals. This forces
generic threats ("IDOR on parameterised routes") rather than specific ones ("candidate/:id is fetched
without owner check, auth header is present on write but absent on read, confirmed by call-site at
`src/api/candidates.js:247`").

**What already exists (no new infrastructure needed):**
- `FindingOccurrence` table (`db/models.py`) has `source_path`, `line`, `col`, `offset_start`,
  `offset_end`, `evidence` (optional snippet), and `run_asset_id` → `RunAsset.input_ref` (S3 key).
  `_assemble_context` currently ignores `FindingOccurrence` entirely — reads `Finding` only.
- Raw JS stored in S3 under `{tenant_id}/{run_id}/input/{sha256}`; source-mapped readable JS under
  `reconstructed/`. `storage.get_blob(key)` already used by `probe/sources.py`.
- `Finding.attributes` already stores `auth` (`[{name, scheme}]`), `kind`, `method`, `wrapper` for
  endpoint findings — some signal is already there, just not surfaced to the threat model.

**Rejected approaches (swarm consensus):**
- **Web search / OpenRouter tool**: MinIO is internal — OpenRouter's infrastructure cannot reach it.
- **Multi-turn tool-call loop**: adds 20-60s latency + failure surfaces at every turn boundary vs.
  8-25s for a single enriched prompt. The relevant code locations are already known (byte offsets in
  `FindingOccurrence`) so tool retrieval offers nothing over deterministic pre-fetch.
- **RAG / vector store**: over-engineered. The "query" is a byte offset — no fuzzy retrieval needed.
- **OpenRouter Agent SDK**: JS-only, no Python equivalent, not needed.

**Fix (Phase 1 — ~1 day, no pipeline changes):**
Extend `_assemble_context` in `threat_model/service.py`:
1. Query `FindingOccurrence JOIN RunAsset` for endpoint findings in the run — get `source_path`,
   `line`, `offset_start`, `evidence`, `RunAsset.input_ref` (S3 key).
2. Priority-rank occurrences: (a) co-located secret findings (same run, nearby byte offset); (b)
   write-semantic URL segments (`create`, `update`, `delete`, `admin`, `upload`, `internal`); (c)
   IDOR-shape params (`:id`, `:userId`, `:token` in URL value); (d) auth literals in evidence.
   Take top 25.
3. For each: use `evidence` if populated; else `get_blob(RunAsset.input_ref)`, prefer `reconstructed/`
   blob (source-mapped), fallback to `input/` raw JS. Extract ±15 lines around `line`; if minified
   (single-line file), extract `offset_start ± 500` chars.
4. Inject as a `## Code Context` section — fenced JS blocks labelled `source_path:line`.
5. Cap: 30 lines/snippet × 25 snippets ≈ 10K tokens; total prompt stays under 15K.

**Fix (Phase 2 — future, Vespasian pipeline change):**
Populate `FindingOccurrence.evidence` at extraction time in Vespasian (walk AST ancestors to
enclosing `function_declaration` / `arrow_function` / `method_definition` node, cap at 60 lines).
This eliminates the S3 read at generation time entirely — `_assemble_context` just reads from DB.

### 2026-10-07 LLM model presets final review — OPEN (D65–D66)

Found by the final whole-branch review of the OpenRouter model catalog + Cheapest/Balanced/Strongest presets.

#### D65 · Saved hyphenated OpenRouter ids not migrated + no catalog badge on the team default model [S]  ·  correctness

Team and session rows saved with `anthropic/claude-sonnet-4-6` (the old OpenRouter default, hyphenated) are
not rewritten, and OpenRouter catalog ids use dots (`anthropic/claude-sonnet-4.6`), so the saved id may be
rejected at run time. `GET /settings/llm` reports catalog availability only for presets
(`llm/settings_router.py:_preset_views`), not for `config.model`, so Settings can't flag it.

**Fix:** a data migration mapping known hyphenated OpenRouter ids to their catalog ids (team + session
rows), and an `available` flag for the default model in `GET /settings/llm`.

#### D66 · 5-minute orphan window vs long threat-model runs [S]  ·  reliability

`threat_model/service.py:trigger_generation` resets a `running` row whose `updated_at` is older than 300 s
back to pending. A Strongest/Opus run with `max_tokens=12000` can legitimately exceed that, so a direct API
re-trigger can start a second paid call whose results race the first. The UI hides Generate while running,
so only direct API callers hit it.

**Fix:** heartbeat `updated_at` during the LLM call, or raise the window.

Since the LLM call records (D72 section), the same race is visible: the old task's `_store_results` can
overwrite the re-triggered run's threats while the Threat Model tab shows the new run's call details. Also
guard those writes with `WHERE llm_call_id = <this run's call>`.

### 2026-11-02 capture history fix + CaptureSession refactor — OPEN (D69–D70)

Left on purpose when the past-sessions findings count was fixed (session-keyed cache + tenant-tagged rows).

#### D69 · "New session" leaves the old binding persisted while it rotates [S]  ·  correctness

`CaptureSession.startNew` rotates (and persists) the new session id before it overwrites
`pendingSessionConfig`. A worker respawn in that window restores the new id with the previous session's
engagement binding, and the first upload binds the new session to the old engagement (immutable on the
server). Pre-existing; found by the CaptureSession adversarial design review.

**Fix:** remove `pendingSessionConfig` before `sessionStore.rotate()`, so the window is "unbound" (the
next `startNew` write re-binds) instead of "wrongly bound".

#### D70 · A batch already taken off the processing queue can be stamped with the new session id [S]  ·  correctness

`processQueue` splices up to 10 requests into a local batch (`background.js` `processQueue`) and stamps
each file with `this.session.sessionId` as it processes it. A rotation mid-batch empties the queue but not
that local batch, so its remaining files land in the new session. The "drop stragglers before rotating"
comment overstated this.

**Fix:** capture the session id when the batch is taken, and drop (or stamp with that id) files whose
session rotated mid-batch.

### 2026-09-03 review swarm — OPEN (D44, D46, D54: partial)

Uncovered by a 7-agent review focused on the chrome-extension capture journey (4 agents) plus the
platform (3 agents). **Cross-cutting theme (extension):** the capture core (MV3 durability, tenant
isolation, auth-context) is well-built, but the operator cannot *trust* it — its two worst outcomes,
"I captured nothing" and "my captures never uploaded," are the two the UI hides or misrepresents. The
Tier-1 items below are mostly small, verified-in-code fixes; several were flagged independently by
multiple agents (noted `◆N`). Evidence is `file:line` at time of review.

#### D44 · Scope-safety gaps: dependency-child bypass, `captureEverything` footgun, no CDN-apex discovery [S]  ·  supply-chain/security — Tier 1
> 🟡 **PARTIAL 2026-09-04** (PR #126). Shipped (c) out-of-scope script-host discovery — the worker
> records out-of-scope `type:script` hosts (bounded, session-scoped, workspace/denylist-filtered), and Home
> shows them with a one-click "+ add"; and (b) an in-app confirm before enabling `captureEverything`.
> ✅ **(a) RESOLVED 2026-11-17** (#271). Proven open first in a real run (built extension, Chromium
> chromium-1234, :8000 stack): an in-scope `app.d44a.test` module with a static `import`, a dynamic
> `import()` and a never-executed `import()` of `cdn.other-d44a.test` files stored all three as analyzed
> run assets — the never-executed one the browser never requested — and the extension fetched them WITH
> that host's cookies (`credentials: 'include'`) and probed their `.map`. Fix: a dependency child is
> followed only when the ordered rule (`admitDependency`, `modules/script-admission.js`) would CAPTURE it;
> anything the browser actually loads still goes through `handleRequest` and the ordered rule (an unknown
> host on a known page is quarantined; with no page, `tabId` -1, it is dropped). Re-run: only the 2
> in-scope files stored; the 3 browser-loaded out-of-scope scripts (static, dynamic, and the worker's
> `importScripts`, which Chrome tagged with the tab) held for `cdn.other-d44a.test`; no cookies sent to it; the
> never-executed one never fetched. The concern that deferred this (cross-origin app chunks) is now met by
> ADR-0010: such a chunk is quarantined and analyzed on Add. Tests: `tests/test_script_admission.mjs`,
> `tests/test_script_admission_wiring.mjs`.
> **STILL OPEN:** (b) `captureEverything` auto-expire / per-tab arming (the confirm lands the headline
> safety win).
Three scope-enforcement / rules-of-engagement gaps: (a) **dependency-child chunks bypass the scope
gate** — resolved child URLs are captured/uploaded with `isInScope` deliberately skipped (only denylist
+ exclude-mode-only third-party filtering apply), and the default `outOfScopeMode` is `tag`, so an
absolute cross-origin `import` fetches and uploads out-of-scope third-party JS
(`background.js:519-527,805`, `modules/dependency-extractor.js:83-85`); (b) **`captureEverything` is a
one-tap cross-tenant footgun** — it makes `isInScope` return true for every tab/window with no per-tab
binding (`background.js:711-721`, toggle `src/popup/app.jsx:414`), so left on it uploads unrelated
sites/tenants into the current engagement; (c) **CDN-apex bundles are dropped with no discovery aid** —
app JS served from a separate apex (`d123.cloudfront.net`) doesn't match the target root and is silently
dropped (`background.js:711-742`, dropped requests just `return` at `:292-296`) with no "out-of-scope
script host seen — add?" hint. **Fix:** apply `isInScope` to children (or gate behind an explicit
"follow cross-scope deps" opt-in); gate `captureEverything` behind confirm + auto-expire and/or a
per-tab "armed" binding; surface out-of-scope hosts that served `type:script` with one-click add.

#### D46 · Extension value-loop + activation affordances [M]  ·  maintainability — Tier 2  ◆2
> 🟡 **PARTIAL 2026-09-04** (PR #126). Shipped (b) a live toolbar badge, (d) reachable "clear
> captures" (Settings) + an export-with-code toggle, and (e) a first-run coach on the sign-in screen.
> ✅ **(a) findings summary card RESOLVED 2026-09-07** — `GET /api/sessions/{id}/findings/summary`
> added to the platform (queries.get_session_findings_summary, sessions_router); extension popup
> fetches it once analysis completes and shows a compact "N findings: X endpoints · Y secrets · Z IPs"
> card with a "View →" workspace link. Bug-fixed 2026-09-07: endpoint now resolves the extension's
> own UUID via `external_id` fallback so the card actually renders (previously the platform UUID
> mismatch always returned `no_run`).
> ✅ **(c) persisted capture history RESOLVED 2026-09-07** — `chrome.storage.local` FIFO-10 history
> (`CAPTURE_HISTORY_KEY`/`HISTORY_MAX`), saved in `_saveSessionToHistory()` before every session
> rotation; `getHistory` handler + `getHistory` API; "PAST SESSIONS" card in HomeView.jsx; structural
> test `tests/test_capture_history.mjs`.
> **STILL OPEN:** Burp/Caido/HAR interchange export.
The extension is a one-way uploader: value never returns to where the operator works, and first-run
activation is unguided. Bundle of feature gaps: (a) **no results in the popup** — after Analyze only
progress counts return; endpoints/secrets/OpenAPI require leaving to the web workspace
(`modules/workspace-client.js`/`src/popup/api.js:36-37` expose no findings call; label
`src/popup/components/HomeView.jsx:213-219`); (b) **no toolbar badge** — `chrome.action.setBadgeText` is
never called, so capture state/progress is invisible while the popup is closed; (c) **no capture
history** — `newSession` clears all state (`background.js:1089-1097`) and `handleMessage`
(`background.js:900-917`) has no list/re-open/re-analyze for past sessions; (d) **export omits code &
"clear captures" is unreachable** — `getExportData` uses `includeContent:false` (`background.js:1256`,
`modules/export-builder.js:1-30`) and the `clearFiles` handler (`background.js:905,1181`) is wired to no
UI; (e) **no Burp/Caido/HAR interchange** and **no first-run onboarding** (`src/popup/components/LoginView.jsx`
has no help). **Fix (incremental):** post-analyze summary card via a `sessions/{id}/findings/summary`
fetch; a live badge; a persisted per-session summary list; an "include code" export toggle + reachable
"clear"; HAR export; a 3-step first-run coach.

#### D54 · Continuous-use & collaboration features [L]  ·  maintainability — Tier 2
> 🟡 **PARTIAL 2026-09-04** (PR #127). Shipped (b) a run-finished browser Notification and (d) global
> session search.
> ✅ **(a) run-to-run diff RESOLVED 2026-09-07** — `GET /runs/{id}/diff?base={base_id}` returns
> new/persisted/gone buckets; `base_incomplete` flag when base is PARTIAL/FAILED/CANCELLED (correctness
> trap: absent ≠ fixed). `DiffPage.tsx` + `DiffRoute` + "Compare to prev run" button in OverviewPanel
> (auto-picks immediately preceding done/partial run). 3 fast-lane router tests.
> **STILL OPEN:** (c) in-product invite endpoint.
The product is built to re-run a target over time and be used by a team, but the payoff features are
absent: (a) **no run-to-run diff** — `REQ-D5` specifies it; only a same-hash *sightings count* exists
(`apps/platform/src/recon/findings/queries.py:84-268`), no diff route, so "what's new/gone since last
scan" (the point of re-running) doesn't exist; (b) **no run-finished notification** — progress is
SSE-only, nothing fires on terminal state if the tab is unfocused (`apps/platform/web/src/features/progress/RunProgress.tsx`;
runs can be ~19 min per [[D38]]); (c) **no in-product user invite** — the only way to add an operator is
the `seed-admin` CLI (`docs/ARCHITECTURE.md:173-177`), and `role` is never enforced outside tests, so a
2-person team can't self-serve a second seat at all; (d) **global search is a decorative placeholder** —
the TopBar box is an inert `<div title="Search — coming soon">` (`apps/platform/web/src/shell/TopBar.tsx:5-6,18-22`).
**Fix (independent slices):** an MVP two-run diff (partial-aware per REQ-D5); a browser `Notification`
on state→terminal; an admin-only invite endpoint reusing `seed-admin`; a client-side session search to
start.

### 2026-11-03 LLM call records — OPEN (D72)

#### D72 · A session deleted mid-recording can orphan one LLM call's prompt/response blobs [S]  ·  storage

`recon.llm.calls._record` inserts the `llm_call` row first and writes the prompt/response blobs only after
it commits, so a session deleted before the insert leaves no blobs. A delete that lands between the insert
and the blob writes still can: the row cascades away, the session purge has already swept the run's
prefix, and the two blobs (~100 KB) stay as unreferenced storage. Same class as D47's accepted orphans.

**Fix:** the deferred blob GC D47 mentions, or a post-write existence check that deletes the blobs if the
row is gone.

#### D73 · Backend files over the ~300-line cap keep growing [M]  ·  maintainability

`CLAUDE.md` points here for files over the cap, but none were listed. Grown by the LLM-call work:
`db/models.py` (868), `threat_model/service.py` (639), `sessions/service.py` (530). Each change kept its own
additions minimal, but the files need splitting by responsibility (e.g. threat-model context assembly vs
generation vs serialization; models per domain).

**Fix:** split one file per PR, starting with `threat_model/service.py`.

### 2026-11-07 Capture lifecycle — OPEN (D82, D83, D84)

Known limits of analyzing one capture in passes (design:
`apps/platform/docs/superpowers/specs/2026-11-07-capture-lifecycle-design.md`).

#### D82 · An earlier pass's file doesn't re-resolve against a later file's exports [M]  ·  recall

Each pass rebuilds the cross-module index over every file, so NEW files resolve against old ones.
But a file analyzed in an earlier pass is skipped (`findings/analyze.py`, terminal
`analyze_status`), so it never re-resolves against exports from a file that arrived later, and a
stale unresolved endpoint can stay. This is narrow: static ESM imports load before their importers.

**Fix:** store the cross-module index per run and re-resolve only the unresolved sinks of earlier
files when a pass adds exports.

#### D83 · Each pass re-reads every file for the cross-module index [M]  ·  performance

A pass analyzes only new files, but its pre-pass reads every file of the capture (and re-recovers
every source map, D28), and correlate re-scans all request bodies. On a large capture, each
re-analysis costs nearly a full one. The single worker serves these between other runs.

**Fix:** a stored per-run index merged per pass (with D82), and correlate over new observations only.

Related edges accepted for now:
- A pass's progress counts every file in the run (`analyze.py` total), so a pass over 3 new files
  reads "3 of 500".
- The 10 s quiet period has no maximum wait. Continuous browsing keeps postponing the automatic
  update; "Analyze now" forces one.
- End or Analyze during a running pass always schedules one more pass, even when the running
  pass already covered everything: one redundant pass, traded for never losing late observations.

#### D84 · A finding-identity bump during an open capture duplicates its findings [S]  ·  correctness

A capture can now stay open across a deploy that bumps `FINDING_HASH_VERSION`
(`findings/normalize.py`), so findings recorded before and after it carry different hashes.
The 24 h idle auto-end bounds the window.

**Fix:** end open captures (final pass) as part of an identity-version deploy, or re-key on the bump.

### 2026-11-03 Exact source locations — OPEN (D75, D76)

#### D75 · A bundle over the 1 MiB beautify cap has no exact location in Pretty-print mode [M]  ·  UX

Such a bundle is analyzed and served RAW (`deobfuscate.beautify` returns None), so its view locations
index the raw text. A jump opens it raw and highlights the exact token (the viewer renders a window
of the huge row around it), but the client-side Pretty print
(a different formatter, in the Web Worker) drops every mark and highlight, because nothing maps raw
positions onto its output.

**Fix:** run the same whitespace-skipping alignment `recon.findings.located` uses (raw -> formatted,
O(n), fail-closed) inside `beautify.worker.ts` and return the mapped ranges with the formatted text.

#### D76 · Declared base-URL consts have no source location [S]  ·  UX

The declared-const endpoints `extract._emit_declared_consts` emits (`kind="declared"`) carry a 0..0
span, so they are stored with no view location and show "no exact location": the `BaseEnv` consts it
reads keep only the resolved value, not the declarator node.

**Fix:** carry the declarator's byte span on the declared `RawEndpoint` (as `mark_start_byte` /
`mark_end_byte`), so `analyze._endpoint_view` locates it like any other endpoint.

### 2026-11-04 QA run (recon-range) — OPEN (D77)

#### D77 · Extension captures never get technology detection [M]  ·  recall

`findings/techdetect_pass.run_fingerprint_pass` needs a `fingerprint-signal` blob (allowlisted response
headers, script URLs, meta, cookies per host). Crawls (`fetch/fetch.py`) and server-side captures
(`capture/stage.py`) write one; the extension ingest (`/api/save-files`) does not, so every extension
run shows no tech stack whatever the site uses (QA: recon-range bundles axios -> nothing). Uploads and
legacy single-URL runs lack the signal too. The UI now says "not collected" (or "awaiting analysis"
while the run is active) instead of a misleading 0 (`detection_ran`).

**Fix:** the extension already uploads each file's response `headers` and URL; build the same
fingerprint-signal from the run's captured files at analyze time (allowlist the same headers), so the
existing pass runs unchanged.

