# Project-scoped capture sessions — Extension client (Plan B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Chrome MV3 extension to the project-scoped backend (Plan A) so an operator picks an engagement (project), reviews its inherited defaults, optionally overrides per session, and captures under the resolved config — with the resolved snapshot (`projectId`, scope, `captureConfig`, `overrideKeys`) stamped onto every upload so the backend binds it on session create.

**Architecture:** A pure client mirror of the backend's `project_config.py` (`modules/project-config.js`) is the single source of truth on the client for the config shape, the inherit/override merge, and — new here — the mapping between the grouped config schema and the extension's **flat** `chrome.storage.local` settings bag (`domainScopes`, `outOfScopeMode`, `denyRules`, `performAnalysisOnUpload`, …). **The popup resolves the effective config once** (it needs it for the inherited/override preview anyway) and sends the resolved snapshot; **background is a thin applicator** that maps the snapshot onto the flat capture-gate keys, sets the uploader's scope/config/analyze flag, rotates the session id, and responds. This honors spec §7's "store the config the capture actually ran under (the client's, possibly stale-cached)" — the popup's cache-resolved snapshot *is* that record. A tiny injectable read-through cache (`modules/projects-cache.js`) keeps the engagement picker working through a brief workspace outage. Projects reach the backend only via the background service worker (the popup's `api.js` is a pure message layer): new `WorkspaceClient.listProjects()`/`createProject()` methods + two `handleMessage` actions + two `api.js` wrappers.

**Tech Stack:** Preact 10 (popup, esbuild-bundled to `dist/popup.js`), MV3 ES-module service worker (`background.js` + raw ESM `modules/*.js`), `chrome.storage.local`, `fetch`. Tests: the repo's hand-rolled `tests/*.mjs` convention (Node built-ins `node:assert/strict` + `node:vm`, run via `node tests/<file>.mjs`) — **no framework is added**.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-03-project-scoped-capture-sessions-design.md`. Branch: `feat/project-scoped-sessions` (Plan A already committed on it; the backend projects API + `save-files` binding exist — commits `338967d`, `a3e3ae4`, `894dee1`).
- **Config schema — the single source of truth for the merge** (mirror of `api/app/project_config.py`, camelCase JSON leaves): `scope.{rootDomains,includeSubdomains}`, `capture.{outOfScopeMode,maxAssetMb}`, `denylist.{rules,useDefaultProfile}`, `analysis.{analyzeOnUpload,captureSourceMaps}`.
- **System defaults (must byte-match the backend's):** `scope.rootDomains=[]`, `scope.includeSubdomains=true`, `capture.outOfScopeMode="tag"`, `capture.maxAssetMb=10`, `denylist.rules=[]`, `denylist.useDefaultProfile=true`, `analysis.analyzeOnUpload=false`, `analysis.captureSourceMaps=true`.
- **Schema ↔ flat-storage mapping (the only bridge, both directions):** `scope.rootDomains↔domainScopes`, `scope.includeSubdomains↔includeSubdomains`, `capture.outOfScopeMode↔outOfScopeMode`, `capture.maxAssetMb↔maxAssetMb`, `denylist.rules↔denyRules`, `denylist.useDefaultProfile↔denyDefaultProfile`, `analysis.analyzeOnUpload↔performAnalysisOnUpload`, `analysis.captureSourceMaps↔captureSourceMaps`. Plus derived `useDomainScope = domainScopes.length > 0`.
- **`inherit/override` rule (per leaf):** `null/absent = inherit`, `set = replace` (lists replace, never union). `resolveEffectiveConfig` returns `{effective, overrideKeys}` (`overrideKeys` sorted dotted paths).
- **Snapshot-on-create:** the popup resolves against its (possibly stale-cached) project defaults and sends the resolved snapshot; the backend stores as-is. The extension does not re-resolve server-side.
- **Standalone parity:** `projectId=null`; defaults come from the current global settings (`configFromSettings`); the resolved `captureConfig` is still snapshotted onto uploads for a uniform record (spec §8, §14.3). This preserves today's ad-hoc-scope behavior.
- **Backward compatibility:** an old popup build that omits the new metadata still starts a session — background treats missing `captureConfig` as "leave the non-scope gate as-is / scope-only," matching today.
- **Pure modules only for the shared logic:** `modules/project-config.js` and `modules/projects-cache.js` reference no `chrome`/`fetch`/DOM globals (callers inject storage + fetcher). This is what makes them unit-testable and safe to bundle into both the service worker and the popup.
- **Test lanes:**
  - *Pure logic* (`project-config.js`, `projects-cache.js`, `workspace-client` new methods, `batch-uploader` metadata): red-green `tests/*.mjs`, run `node tests/<file>.mjs` (exit 0 = pass, non-zero = fail). Follow the existing three styles verbatim: direct-import (pure), `vm`+`export`-strip+fetch-stub (class w/ web APIs), injected fake storage.
  - *Popup UI + `newSession` glue* (no DOM/service-worker test harness exists, and none is added): verified by a **live extension walkthrough** — `node build.mjs`, load unpacked, drive it, and confirm the `chrome.storage.local` gate keys + the outgoing `save-files` metadata reflect the resolved config. This is the CLAUDE.md §2 completion gate for UI work.
- **Build:** the popup is bundled by `node build.mjs` (esbuild → `dist/popup.js`, committed). `background.js` + `modules/*.js` load raw — no build. Any new popup element needing `:hover`/`:focus` needs a `class="pp-…"` + a rule in `src/popup/styles.css` (inline styles can't express pseudo-classes); colors/fonts come from `theme.js` (`C`, `F`).

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `chrome-extension/modules/project-config.js` | Create | Pure config mirror: schema, system defaults, `deepMerge`, `validateConfig`, `resolveEffectiveConfig`, `splitEffective`, and the flat-settings bridge `configFromSettings`/`settingsFromConfig` |
| `chrome-extension/tests/test_project_config.mjs` | Create | Unit tests (direct import) |
| `chrome-extension/modules/projects-cache.js` | Create | Injectable read-through cache for the projects list (offline fallback) |
| `chrome-extension/tests/test_projects_cache.mjs` | Create | Unit tests (injected fake storage + fetcher) |
| `chrome-extension/modules/workspace-client.js` | Modify | Add `listProjects()` / `createProject(project)` (inline fetch, mirror `testConnection`) |
| `chrome-extension/background.js` | Modify | `handleMessage`: `listProjects`/`createProject` actions (list via cache); import the pure modules |
| `chrome-extension/src/popup/api.js` | Modify | `listProjects()` / `createProject(project)` message wrappers |
| `chrome-extension/tests/test_projects_client.mjs` | Create | `WorkspaceClient` projects methods (vm + fetch stub) |
| `chrome-extension/modules/batch-uploader.js` | Modify | `setConfig()` + `configMetadata()`; spread into `save-files` payload metadata |
| `chrome-extension/tests/test_config_metadata.mjs` | Create | Upload payload carries `projectId`/`captureConfig`/`overrideKeys` |
| `chrome-extension/background.js` | Modify | `newSession` becomes project-aware: apply resolved snapshot → flat gate keys + uploader scope/config/analyze flag |
| `chrome-extension/src/popup/app.jsx` | Modify | Load projects on open; project/override state; `startNewSession` resolves + sends the snapshot |
| `chrome-extension/src/popup/components/HomeView.jsx` | Modify | Engagement picker + inherited display + override editor + quick "New project" |
| `chrome-extension/src/popup/components/SettingsView.jsx` | Modify | "Default scope" relabelled as the **Standalone** fallback |

---

## Task 1: Pure config mirror (`modules/project-config.js`)

**Files:**
- Create: `chrome-extension/modules/project-config.js`
- Test: `chrome-extension/tests/test_project_config.mjs`

**Interfaces:**
- Produces:
  - `SYSTEM_DEFAULTS`, `CONFIG_SCHEMA`
  - `systemDefaults() -> object`
  - `deepMerge(base, patch) -> object`
  - `validateConfig(doc, { partial = false }) -> doc` (throws `Error`)
  - `resolveEffectiveConfig(defaults, overrides) -> { effective, overrideKeys }`
  - `splitEffective(effective) -> { scope, captureConfig }`
  - `configFromSettings(settings) -> effectiveDoc` (flat bag → grouped)
  - `settingsFromConfig(effective) -> flatPatch` (grouped → flat storage keys, incl. derived `useDomainScope`)

- [ ] **Step 1: Write the failing test**

Create `chrome-extension/tests/test_project_config.mjs`:

```js
// Unit tests for the pure client config mirror (modules/project-config.js). Pure module
// (no chrome/fetch/DOM), so it is imported directly — no vm/export-stripping needed.
import assert from 'node:assert/strict';
import {
  SYSTEM_DEFAULTS, systemDefaults, deepMerge, validateConfig,
  resolveEffectiveConfig, splitEffective, configFromSettings, settingsFromConfig,
} from '../modules/project-config.js';

function test_system_defaults_match_backend() {
  // Parity with api/app/project_config.py SYSTEM_DEFAULTS — drift breaks the snapshot contract.
  assert.deepEqual(SYSTEM_DEFAULTS, {
    scope: { rootDomains: [], includeSubdomains: true },
    capture: { outOfScopeMode: 'tag', maxAssetMb: 10 },
    denylist: { rules: [], useDefaultProfile: true },
    analysis: { analyzeOnUpload: false, captureSourceMaps: true },
  });
}

function test_resolve_inherits_all_when_no_overrides() {
  const d = systemDefaults(); d.scope.rootDomains = ['*.acme.com'];
  const { effective, overrideKeys } = resolveEffectiveConfig(d, null);
  assert.deepEqual(effective.scope.rootDomains, ['*.acme.com']);
  assert.deepEqual(overrideKeys, []);
}

function test_resolve_override_replaces_per_field_and_records_key() {
  const d = systemDefaults(); d.scope.rootDomains = ['*.acme.com'];
  const { effective, overrideKeys } =
    resolveEffectiveConfig(d, { scope: { rootDomains: ['app.acme.com'] } });
  assert.deepEqual(effective.scope.rootDomains, ['app.acme.com']);           // replaced
  assert.equal(effective.scope.includeSubdomains, d.scope.includeSubdomains); // inherited
  assert.deepEqual(overrideKeys, ['scope.rootDomains']);
}

function test_resolve_list_override_is_replace_not_union() {
  const d = systemDefaults(); d.denylist.rules = [{ tag: 'a', pattern: '*.a.com' }];
  const { effective, overrideKeys } = resolveEffectiveConfig(d, { denylist: { rules: [] } });
  assert.deepEqual(effective.denylist.rules, []);                             // replaced, not union
  assert.deepEqual(overrideKeys, ['denylist.rules']);
}

function test_validate_rejects_bad_out_of_scope_mode() {
  const d = systemDefaults(); d.capture.outOfScopeMode = 'nope';
  assert.throws(() => validateConfig(d), /outOfScopeMode/);
}

function test_validate_rejects_max_asset_mb_over_10() {
  const d = systemDefaults(); d.capture.maxAssetMb = 25;
  assert.throws(() => validateConfig(d), /maxAssetMb/);
}

function test_deep_merge_leaf_wins_and_preserves_siblings() {
  const base = systemDefaults();
  const merged = deepMerge(base, { analysis: { analyzeOnUpload: true } });
  assert.equal(merged.analysis.analyzeOnUpload, true);
  assert.equal(merged.analysis.captureSourceMaps, base.analysis.captureSourceMaps);
}

function test_split_effective_separates_scope_from_rest() {
  const { scope, captureConfig } = splitEffective(systemDefaults());
  assert.deepEqual(Object.keys(scope).sort(), ['includeSubdomains', 'rootDomains']);
  assert.deepEqual(Object.keys(captureConfig).sort(), ['analysis', 'capture', 'denylist']);
}

function test_validate_partial_only_checks_present_sections() {
  validateConfig({ analysis: { analyzeOnUpload: true, captureSourceMaps: false } }, { partial: true });
}

function test_settings_config_round_trip() {
  const settings = {
    domainScopes: ['app.acme.com'], includeSubdomains: false,
    outOfScopeMode: 'exclude', maxAssetMb: 5,
    denyRules: [{ pattern: '*.ga.com' }], denyDefaultProfile: false,
    performAnalysisOnUpload: true, captureSourceMaps: false,
  };
  const cfg = configFromSettings(settings);
  assert.equal(cfg.capture.outOfScopeMode, 'exclude');
  assert.equal(cfg.analysis.analyzeOnUpload, true);
  const patch = settingsFromConfig(cfg);
  assert.deepEqual(patch.domainScopes, ['app.acme.com']);
  assert.equal(patch.useDomainScope, true);            // derived: non-empty scope
  assert.equal(patch.performAnalysisOnUpload, true);
  assert.equal(patch.denyDefaultProfile, false);
  assert.equal(patch.maxAssetMb, 5);
}

function test_settings_from_config_empty_scope_disables_gate() {
  const patch = settingsFromConfig(configFromSettings({ domainScopes: [] }));
  assert.equal(patch.useDomainScope, false);
}

const tests = [
  test_system_defaults_match_backend,
  test_resolve_inherits_all_when_no_overrides,
  test_resolve_override_replaces_per_field_and_records_key,
  test_resolve_list_override_is_replace_not_union,
  test_validate_rejects_bad_out_of_scope_mode,
  test_validate_rejects_max_asset_mb_over_10,
  test_deep_merge_leaf_wins_and_preserves_siblings,
  test_split_effective_separates_scope_from_rest,
  test_validate_partial_only_checks_present_sections,
  test_settings_config_round_trip,
  test_settings_from_config_empty_scope_disables_gate,
];

let failed = 0;
for (const t of tests) {
  try { t(); console.log('  ok  ' + t.name); }
  catch (e) { failed++; console.error('  FAIL ' + t.name + ' — ' + e.message); }
}
if (failed) { console.error(`test_project_config: ${failed} failed`); process.exit(1); }
console.log('test_project_config: ok');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd chrome-extension && node tests/test_project_config.mjs`
Expected: FAIL — `Cannot find module '../modules/project-config.js'` (module does not exist yet).

- [ ] **Step 3: Write the implementation**

Create `chrome-extension/modules/project-config.js`:

```js
// Client mirror of api/app/project_config.py. Pure, stdlib-only — no chrome/fetch/DOM — so it
// is imported by the ES-module service worker (background.js), bundled into the popup by
// esbuild, and unit-tested directly (node tests/test_project_config.mjs).
//
// A project owns a `defaults` document with four groups (scope/capture/denylist/analysis). A
// session resolves its effective config once — resolveEffectiveConfig(defaults, overrides):
// null/absent = inherit, set = replace (per leaf; lists replace, never union). This file is
// the single source of truth on the client for the shape, the merge, AND the mapping to/from
// the extension's flat chrome.storage settings bag (the capture gate).

const OUT_OF_SCOPE_MODES = new Set(['tag', 'mute', 'exclude']);
const clone = (value) => JSON.parse(JSON.stringify(value === undefined ? null : value));

export const SYSTEM_DEFAULTS = {
  scope: { rootDomains: [], includeSubdomains: true },
  capture: { outOfScopeMode: 'tag', maxAssetMb: 10 },
  denylist: { rules: [], useDefaultProfile: true },
  analysis: { analyzeOnUpload: false, captureSourceMaps: true },
};

// Every leaf a project owns and a session may override, grouped by section.
export const CONFIG_SCHEMA = {
  scope: ['rootDomains', 'includeSubdomains'],
  capture: ['outOfScopeMode', 'maxAssetMb'],
  denylist: ['rules', 'useDefaultProfile'],
  analysis: ['analyzeOnUpload', 'captureSourceMaps'],
};

// The only bridge between the grouped schema and the extension's FLAT storage keys.
// [section, leaf, storageKey]
const SETTINGS_MAP = [
  ['scope', 'rootDomains', 'domainScopes'],
  ['scope', 'includeSubdomains', 'includeSubdomains'],
  ['capture', 'outOfScopeMode', 'outOfScopeMode'],
  ['capture', 'maxAssetMb', 'maxAssetMb'],
  ['denylist', 'rules', 'denyRules'],
  ['denylist', 'useDefaultProfile', 'denyDefaultProfile'],
  ['analysis', 'analyzeOnUpload', 'performAnalysisOnUpload'],
  ['analysis', 'captureSourceMaps', 'captureSourceMaps'],
];

export function systemDefaults() {
  return clone(SYSTEM_DEFAULTS);
}

export function deepMerge(base, patch) {
  const out = clone(base);
  for (const [key, value] of Object.entries(patch || {})) {
    const isObj = (v) => v && typeof v === 'object' && !Array.isArray(v);
    if (isObj(value) && isObj(out[key])) out[key] = deepMerge(out[key], value);
    else out[key] = clone(value);
  }
  return out;
}

export function validateConfig(doc, { partial = false } = {}) {
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
    throw new Error('config must be an object');
  }
  for (const section of Object.keys(CONFIG_SCHEMA)) {
    if (!(section in doc)) {
      if (partial) continue;
      throw new Error(`missing config section: ${section}`);
    }
    const s = doc[section];
    if (!s || typeof s !== 'object' || Array.isArray(s)) {
      throw new Error(`config section ${section} must be an object`);
    }
  }
  if ('scope' in doc) {
    const scope = doc.scope;
    if ('rootDomains' in scope && !Array.isArray(scope.rootDomains)) {
      throw new Error('scope.rootDomains must be a list');
    }
    if ('includeSubdomains' in scope && typeof scope.includeSubdomains !== 'boolean') {
      throw new Error('scope.includeSubdomains must be a boolean');
    }
  }
  if ('capture' in doc) {
    const capture = doc.capture;
    if ('outOfScopeMode' in capture && !OUT_OF_SCOPE_MODES.has(capture.outOfScopeMode)) {
      throw new Error('capture.outOfScopeMode must be one of tag|mute|exclude');
    }
    if ('maxAssetMb' in capture) {
      const mb = capture.maxAssetMb;
      if (typeof mb !== 'number' || Number.isNaN(mb) || mb <= 0 || mb > 10) {
        throw new Error('capture.maxAssetMb must be a number in (0, 10]');
      }
    }
  }
  if ('denylist' in doc) {
    const denylist = doc.denylist;
    if ('rules' in denylist) {
      if (!Array.isArray(denylist.rules)) throw new Error('denylist.rules must be a list');
      for (const rule of denylist.rules) {
        if (!rule || typeof rule !== 'object' || Array.isArray(rule) || !('pattern' in rule)) {
          throw new Error("each denylist rule must be an object with a 'pattern'");
        }
      }
    }
    if ('useDefaultProfile' in denylist && typeof denylist.useDefaultProfile !== 'boolean') {
      throw new Error('denylist.useDefaultProfile must be a boolean');
    }
  }
  if ('analysis' in doc) {
    const analysis = doc.analysis;
    for (const key of ['analyzeOnUpload', 'captureSourceMaps']) {
      if (key in analysis && typeof analysis[key] !== 'boolean') {
        throw new Error(`analysis.${key} must be a boolean`);
      }
    }
  }
  return doc;
}

export function resolveEffectiveConfig(defaults, overrides) {
  const ov = overrides || {};
  const effective = clone(defaults);
  const overrideKeys = [];
  for (const [section, keys] of Object.entries(CONFIG_SCHEMA)) {
    const sectionOverride = ov[section];
    if (!sectionOverride || typeof sectionOverride !== 'object' || Array.isArray(sectionOverride)) continue;
    for (const key of keys) {
      if (key in sectionOverride) {
        if (!effective[section] || typeof effective[section] !== 'object') effective[section] = {};
        effective[section][key] = clone(sectionOverride[key]);
        overrideKeys.push(`${section}.${key}`);
      }
    }
  }
  overrideKeys.sort();
  return { effective, overrideKeys };
}

export function splitEffective(effective) {
  const scopeSection = (effective && effective.scope) || {};
  const scope = {
    rootDomains: Array.isArray(scopeSection.rootDomains) ? [...scopeSection.rootDomains] : [],
    includeSubdomains: scopeSection.includeSubdomains !== false,
  };
  const captureConfig = {};
  for (const section of ['capture', 'denylist', 'analysis']) {
    captureConfig[section] = clone((effective && effective[section]) || {});
  }
  return { scope, captureConfig };
}

// Flat live settings -> grouped effective config. Used for Standalone: the "defaults" a
// project-less session resolves against are the extension's current global settings. Fallbacks
// mirror background.js loadSettings (booleans default true via !== false; mode defaults 'tag').
export function configFromSettings(settings) {
  const s = settings || {};
  return {
    scope: {
      rootDomains: Array.isArray(s.domainScopes) ? [...s.domainScopes] : [],
      includeSubdomains: s.includeSubdomains !== false,
    },
    capture: {
      outOfScopeMode: OUT_OF_SCOPE_MODES.has(s.outOfScopeMode) ? s.outOfScopeMode : 'tag',
      maxAssetMb: (typeof s.maxAssetMb === 'number' && s.maxAssetMb > 0) ? Math.min(10, s.maxAssetMb) : 10,
    },
    denylist: {
      rules: Array.isArray(s.denyRules) ? clone(s.denyRules) : [],
      useDefaultProfile: s.denyDefaultProfile !== false,
    },
    analysis: {
      analyzeOnUpload: s.performAnalysisOnUpload === true,
      captureSourceMaps: s.captureSourceMaps !== false,
    },
  };
}

// Grouped effective config -> flat chrome.storage patch (what newSession writes to apply the
// resolved config to the live capture gate). Only sections present are mapped, so a partial
// effective (e.g. scope-only from an old build) leaves the other gate keys untouched.
// useDomainScope is derived (scope active iff any root domain), matching newSession's logic.
export function settingsFromConfig(effective) {
  const patch = {};
  for (const [section, leaf, storageKey] of SETTINGS_MAP) {
    const sec = effective && effective[section];
    if (sec && typeof sec === 'object' && leaf in sec) patch[storageKey] = clone(sec[leaf]);
  }
  if ('domainScopes' in patch) {
    patch.useDomainScope = Array.isArray(patch.domainScopes) && patch.domainScopes.length > 0;
  }
  return patch;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd chrome-extension && node tests/test_project_config.mjs`
Expected: PASS — prints `  ok  …` for all 11 and `test_project_config: ok` (exit 0).

- [ ] **Step 5: Commit**

```bash
git add chrome-extension/modules/project-config.js chrome-extension/tests/test_project_config.mjs
git commit -m "feat(ext-projects): pure client config mirror + flat-settings bridge"
```

---

## Task 2: Projects list cache (`modules/projects-cache.js`)

**Files:**
- Create: `chrome-extension/modules/projects-cache.js`
- Test: `chrome-extension/tests/test_projects_cache.mjs`

**Interfaces:**
- Consumes: an injected `storage` (chrome.storage.local shape: `async get(keys)` accepting a string or string[]; `async set(obj)`) and an injected `fetcher` (`async () => { success, projects }`).
- Produces:
  - `readCache(storage) -> { projects: array|null, cachedAt: number }`
  - `writeCache(storage, projects, now?) -> projects`
  - `listProjectsWithCache(fetcher, storage, now?) -> { projects: array, source: 'live'|'cache'|'empty' }`

- [ ] **Step 1: Write the failing test**

Create `chrome-extension/tests/test_projects_cache.mjs`:

```js
// Unit tests for the injectable projects cache (modules/projects-cache.js). Pure module;
// storage + fetcher are injected, so it imports directly with an in-memory storage fake.
import assert from 'node:assert/strict';
import { readCache, writeCache, listProjectsWithCache } from '../modules/projects-cache.js';

// In-memory chrome.storage.local mock: get accepts a string or string[] and returns only the
// requested keys (matching the real API); set merges.
function makeStorage(initial = {}) {
  const data = { ...initial };
  return {
    _data: data,
    async get(keys) {
      if (keys == null) return { ...data };
      const list = Array.isArray(keys) ? keys : [keys];
      const out = {};
      for (const k of list) if (k in data) out[k] = data[k];
      return out;
    },
    async set(obj) { Object.assign(data, obj); },
  };
}

async function run() {
  // 1) Live success refreshes the cache and reports source 'live'.
  {
    const storage = makeStorage();
    const projects = [{ id: 'p1', name: 'acme' }];
    const res = await listProjectsWithCache(async () => ({ success: true, projects }), storage, 1000);
    assert.deepEqual(res, { projects, source: 'live' });
    assert.deepEqual(storage._data.projectsCache, projects, 'cache written on live success');
    assert.equal(storage._data.projectsCacheAt, 1000, 'timestamp written');
  }

  // 2) Fetch throws -> fall back to the cached list, source 'cache'.
  {
    const cached = [{ id: 'p9', name: 'old' }];
    const storage = makeStorage({ projectsCache: cached, projectsCacheAt: 5 });
    const res = await listProjectsWithCache(async () => { throw new Error('unreachable'); }, storage);
    assert.deepEqual(res.projects, cached);
    assert.equal(res.source, 'cache');
  }

  // 3) A reachable-but-unsuccessful response also falls back to cache.
  {
    const cached = [{ id: 'p2', name: 'c' }];
    const storage = makeStorage({ projectsCache: cached, projectsCacheAt: 5 });
    const res = await listProjectsWithCache(async () => ({ success: false, error: 'HTTP 500' }), storage);
    assert.deepEqual(res.projects, cached);
    assert.equal(res.source, 'cache');
  }

  // 4) Failure with no cache -> empty list, source 'empty'.
  {
    const storage = makeStorage();
    const res = await listProjectsWithCache(async () => { throw new Error('x'); }, storage);
    assert.deepEqual(res.projects, []);
    assert.equal(res.source, 'empty');
  }

  // 5) read/write round-trip.
  {
    const storage = makeStorage();
    await writeCache(storage, [{ id: 'z' }], 42);
    assert.deepEqual(await readCache(storage), { projects: [{ id: 'z' }], cachedAt: 42 });
  }

  console.log('test_projects_cache: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd chrome-extension && node tests/test_projects_cache.mjs`
Expected: FAIL — `Cannot find module '../modules/projects-cache.js'`.

- [ ] **Step 3: Write the implementation**

Create `chrome-extension/modules/projects-cache.js`:

```js
// A tiny read-through cache for the projects list so the popup's engagement picker keeps
// working through a brief workspace outage (spec §8.1). Pure + injectable (storage + fetcher);
// no chrome/fetch references — the background worker injects chrome.storage.local and a
// WorkspaceClient.listProjects call. Unit-tested with an in-memory storage fake.

const CACHE_KEY = 'projectsCache';
const CACHE_AT_KEY = 'projectsCacheAt';

export async function readCache(storage) {
  const got = (await storage.get([CACHE_KEY, CACHE_AT_KEY])) || {};
  const projects = Array.isArray(got[CACHE_KEY]) ? got[CACHE_KEY] : null;
  const cachedAt = typeof got[CACHE_AT_KEY] === 'number' ? got[CACHE_AT_KEY] : 0;
  return { projects, cachedAt };
}

export async function writeCache(storage, projects, now = Date.now()) {
  await storage.set({ [CACHE_KEY]: projects, [CACHE_AT_KEY]: now });
  return projects;
}

// Try the live fetch; on success refresh the cache and return source 'live'. On failure OR a
// reachable-but-unsuccessful response, fall back to the last cached list ('cache'), or [] if
// there is none ('empty'). Never throws — capture must not break when the workspace blips.
export async function listProjectsWithCache(fetcher, storage, now = Date.now()) {
  try {
    const result = await fetcher();
    if (result && result.success && Array.isArray(result.projects)) {
      await writeCache(storage, result.projects, now);
      return { projects: result.projects, source: 'live' };
    }
  } catch (e) {
    // fall through to cache
  }
  const { projects } = await readCache(storage);
  return { projects: projects || [], source: projects ? 'cache' : 'empty' };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd chrome-extension && node tests/test_projects_cache.mjs`
Expected: PASS — `test_projects_cache: ok` (exit 0).

- [ ] **Step 5: Commit**

```bash
git add chrome-extension/modules/projects-cache.js chrome-extension/tests/test_projects_cache.mjs
git commit -m "feat(ext-projects): injectable read-through cache for the projects list"
```

---

## Task 3: Projects channel — `WorkspaceClient` methods + background actions + `api.js` wrappers

**Files:**
- Modify: `chrome-extension/modules/workspace-client.js` (add two methods)
- Modify: `chrome-extension/background.js` (imports; a `listProjects` method using the cache; two `handleMessage` entries)
- Modify: `chrome-extension/src/popup/api.js` (two wrappers)
- Test: `chrome-extension/tests/test_projects_client.mjs`

**Interfaces:**
- Consumes: `listProjectsWithCache` (Task 2); backend `GET/POST /api/projects` (Plan A).
- Produces:
  - `WorkspaceClient.listProjects() -> { success, projects }` | `{ success:false, error }`
  - `WorkspaceClient.createProject(project) -> { success, project }` | `{ success:false, error }`
  - background actions `listProjects` (cache-wrapped) and `createProject`
  - popup `api.listProjects()` / `api.createProject(project)`

- [ ] **Step 1: Write the failing test**

Create `chrome-extension/tests/test_projects_client.mjs`:

```js
// WorkspaceClient.listProjects / createProject — GET/POST against /api/projects. Loaded via
// the repo's vm pattern (strip the single export) with a fetch stub, like test_t007.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const modPath = path.resolve(__dirname, '../modules/workspace-client.js');
const source = fs.readFileSync(modPath, 'utf8').replace('export class WorkspaceClient', 'class WorkspaceClient');

const calls = [];
function makeClient(fetchImpl) {
  const sandbox = { console, URL, setTimeout, clearTimeout, AbortController, fetch: fetchImpl };
  vm.createContext(sandbox);
  vm.runInContext(`${source}\nthis.WorkspaceClient = WorkspaceClient;`, sandbox, { filename: modPath });
  return new sandbox.WorkspaceClient({ getSettings: () => ({ workspaceUrl: 'http://ws.test' }) });
}

async function run() {
  // listProjects maps a 200 + JSON array to { success:true, projects:[...] }.
  {
    const client = makeClient(async (url, init) => {
      calls.push({ url, method: (init && init.method) || 'GET' });
      return { ok: true, status: 200, json: async () => ([{ id: 'p1', name: 'acme' }]) };
    });
    const res = await client.listProjects();
    assert.equal(res.success, true);
    assert.deepEqual(res.projects, [{ id: 'p1', name: 'acme' }]);
    assert.equal(calls[0].url, 'http://ws.test/api/projects');
    assert.equal(calls[0].method, 'GET');
  }

  // Non-2xx -> { success:false }.
  {
    const client = makeClient(async () => ({ ok: false, status: 503, json: async () => ({}) }));
    const res = await client.listProjects();
    assert.equal(res.success, false);
  }

  // createProject POSTs the JSON body and maps 200 to { success:true, project }.
  {
    let captured;
    const client = makeClient(async (url, init) => {
      captured = { url, method: init.method, body: JSON.parse(init.body) };
      return { ok: true, status: 200, json: async () => ({ id: 'p2', name: captured.body.name }) };
    });
    const res = await client.createProject({ name: 'bounty', defaults: { scope: { rootDomains: ['a.com'] } } });
    assert.equal(res.success, true);
    assert.equal(res.project.id, 'p2');
    assert.equal(captured.method, 'POST');
    assert.equal(captured.url, 'http://ws.test/api/projects');
    assert.equal(captured.body.name, 'bounty');
  }

  console.log('test_projects_client: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd chrome-extension && node tests/test_projects_client.mjs`
Expected: FAIL — `client.listProjects is not a function`.

- [ ] **Step 3a: Add the two `WorkspaceClient` methods**

In `chrome-extension/modules/workspace-client.js`, add these two methods to the class (e.g. immediately after `getAnalysisProgress`, before the closing `}`). They mirror the existing inline `fetch` + `AbortController` + timeout template:

```js
  async listProjects() {
    const target = this.resolveApiBase() + '/api/projects';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const resp = await fetch(target, { method: 'GET', signal: controller.signal });
      clearTimeout(timer);
      if (!resp.ok) return { success: false, status: resp.status, error: `HTTP ${resp.status}` };
      const projects = await resp.json();
      return { success: true, projects: Array.isArray(projects) ? projects : [] };
    } catch (error) {
      clearTimeout(timer);
      return { success: false, error: error?.name === 'AbortError' ? 'timeout' : (error?.message || 'unreachable') };
    }
  }

  async createProject(project) {
    const target = this.resolveApiBase() + '/api/projects';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const resp = await fetch(target, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(project || {}),
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { const body = await resp.json(); if (body && body.detail) detail = body.detail; } catch (e) { /* keep default */ }
        return { success: false, status: resp.status, error: detail };
      }
      return { success: true, project: await resp.json() };
    } catch (error) {
      clearTimeout(timer);
      return { success: false, error: error?.name === 'AbortError' ? 'timeout' : (error?.message || 'unreachable') };
    }
  }
```

- [ ] **Step 3b: Wire the background actions (cache-wrapped list)**

In `chrome-extension/background.js`, add the import near the other module imports (top of file):

```js
import { listProjectsWithCache } from './modules/projects-cache.js';
```

Add a method on the `JSExtractor` class (near the other message handlers, e.g. beside `getStatus`):

```js
  async listProjects(sendResponse) {
    // Live list refreshes the cache; a workspace blip falls back to the cached list so the
    // popup's engagement picker still renders. Never throws.
    const { projects, source } = await listProjectsWithCache(
      () => this.workspaceClient.listProjects(),
      chrome.storage.local
    );
    sendResponse({ success: true, projects, source });
  }
```

Add the two entries to the `handlers` map in `handleMessage` (after `getAnalysisProgress`):

```js
      listProjects: () => this.listProjects(sendResponse),
      createProject: (req) => this.workspaceClient.createProject(req.project).then(sendResponse),
```

- [ ] **Step 3c: Add the popup `api.js` wrappers**

In `chrome-extension/src/popup/api.js`, add next to `newSession`/`updateSettings`:

```js
export const listProjects = () => send('listProjects');
export const createProject = (project) => send('createProject', { project });
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd chrome-extension && node tests/test_projects_client.mjs`
Expected: PASS — `test_projects_client: ok` (exit 0).

- [ ] **Step 5: Commit**

```bash
git add chrome-extension/modules/workspace-client.js chrome-extension/background.js chrome-extension/src/popup/api.js chrome-extension/tests/test_projects_client.mjs
git commit -m "feat(ext-projects): list/create projects channel (client + worker actions + cache)"
```

---

## Task 4: Upload metadata — `setConfig()` + `configMetadata()` on `BatchUploader`

**Files:**
- Modify: `chrome-extension/modules/batch-uploader.js` (constructor field, two methods, payload spread)
- Test: `chrome-extension/tests/test_config_metadata.mjs`

**Interfaces:**
- Consumes: nothing new (plain values from `newSession`, Task 5).
- Produces: `BatchUploader.setConfig({ projectId, captureConfig, overrideKeys } | null)`; each `save-files` upload's `metadata` carries `projectId`/`captureConfig`/`overrideKeys` when set (mirrors `setScope`/`scopeMetadata`).

- [ ] **Step 1: Write the failing test**

Create `chrome-extension/tests/test_config_metadata.mjs`:

```js
// BatchUploader.setConfig / configMetadata — the project binding + non-scope config snapshot
// is stamped onto each save-files upload. Fetch-capture pattern (like test_t007).
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const uploaderPath = path.resolve(__dirname, '../modules/batch-uploader.js');
const transformed = fs.readFileSync(uploaderPath, 'utf8').replace('export class BatchUploader', 'class BatchUploader');

const captured = [];
const sandbox = {
  console, URL, setTimeout, clearTimeout, AbortController,
  chrome: { notifications: { create: () => {} } },
  fetch: async (_url, init) => { captured.push(JSON.parse(init.body)); return { ok: true, json: async () => ({ success: true }) }; },
};
vm.createContext(sandbox);
vm.runInContext(`${transformed}\nthis.BatchUploader = BatchUploader;`, sandbox, { filename: uploaderPath });
const BatchUploader = sandbox.BatchUploader;

const files = [{ url: 'https://a.com/x.js', contentHash: 'h', sessionId: 's', contentLength: 3, content: 'a=1' }];

async function run() {
  const uploader = new BatchUploader();
  uploader.setEndpoint('http://localhost:3000');

  // With a config set, the payload metadata carries projectId/captureConfig/overrideKeys.
  uploader.setConfig({
    projectId: 'proj-1',
    captureConfig: { capture: { outOfScopeMode: 'exclude', maxAssetMb: 5 }, denylist: { rules: [], useDefaultProfile: true }, analysis: { analyzeOnUpload: false, captureSourceMaps: true } },
    overrideKeys: ['capture.outOfScopeMode'],
  });
  await uploader.upload(files);
  const m1 = captured[0].metadata;
  assert.equal(m1.projectId, 'proj-1');
  assert.equal(m1.captureConfig.capture.outOfScopeMode, 'exclude');
  assert.deepEqual(m1.overrideKeys, ['capture.outOfScopeMode']);

  // Standalone: null projectId is omitted, but the captureConfig snapshot is still carried.
  uploader.setConfig({ projectId: null, captureConfig: { analysis: { analyzeOnUpload: true } }, overrideKeys: [] });
  await uploader.upload(files);
  const m2 = captured[1].metadata;
  assert.equal('projectId' in m2, false, 'null projectId omitted');
  assert.equal(m2.captureConfig.analysis.analyzeOnUpload, true);
  assert.deepEqual(m2.overrideKeys, []);

  // No config -> none of the three keys appear (back-compat with today's payload).
  uploader.setConfig(null);
  await uploader.upload(files);
  const m3 = captured[2].metadata;
  assert.equal('projectId' in m3, false);
  assert.equal('captureConfig' in m3, false);
  assert.equal('overrideKeys' in m3, false);

  console.log('test_config_metadata: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd chrome-extension && node tests/test_config_metadata.mjs`
Expected: FAIL — `uploader.setConfig is not a function`.

- [ ] **Step 3a: Add the constructor field**

In `chrome-extension/modules/batch-uploader.js`, in the constructor immediately after `this.scope = null;` (line 15), add:

```js
    // Resolved project binding + non-scope config snapshot chosen when a new session starts;
    // stamped onto save-files metadata so the backend binds it on session create (mirrors
    // this.scope / scopeMetadata). null keeps today's payload (no project keys).
    this.config = null;
```

- [ ] **Step 3b: Add `setConfig` + `configMetadata`**

Immediately after `scopeMetadata()` (ends line 280), add:

```js
  setConfig(config) {
    // config: { projectId, captureConfig, overrideKeys } | null
    this.config = (config && typeof config === 'object') ? config : null;
  }

  configMetadata() {
    const out = {};
    if (!this.config) return out;
    if (this.config.projectId) out.projectId = this.config.projectId;   // null/'' omitted -> standalone
    if (this.config.captureConfig && typeof this.config.captureConfig === 'object') {
      out.captureConfig = this.config.captureConfig;
    }
    if (Array.isArray(this.config.overrideKeys)) out.overrideKeys = this.config.overrideKeys;
    return out;
  }
```

- [ ] **Step 3c: Spread it into the payload metadata**

In `upload()`, extend the metadata object (line 215) so the config metadata is spread right after the scope metadata:

```js
        // Explicit session scope (only honoured by save_files on session create).
        ...this.scopeMetadata(),
        // Project binding + resolved non-scope config snapshot (also create-only on the backend).
        ...this.configMetadata()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd chrome-extension && node tests/test_config_metadata.mjs`
Then the payload regression: `cd chrome-extension && node tests/test_t007_batch_uploader_payload.mjs`
Expected: both PASS (`test_config_metadata: ok`; `test_t007_batch_uploader_payload: ok`).

- [ ] **Step 5: Commit**

```bash
git add chrome-extension/modules/batch-uploader.js chrome-extension/tests/test_config_metadata.mjs
git commit -m "feat(ext-projects): stamp projectId/captureConfig/overrideKeys onto save-files uploads"
```

---

## Task 5: `newSession` becomes project-aware (background)

**Files:**
- Modify: `chrome-extension/background.js` (import; the scope-apply block of `newSession`, lines 872-887)

**Interfaces:**
- Consumes: `settingsFromConfig` (Task 1); `BatchUploader.setConfig` (Task 4). New message shape `newSession({ projectId, scope, captureConfig, overrideKeys })` from the popup (Task 6).
- Produces: on a new session the flat capture-gate keys (`domainScopes`/`useDomainScope`/`includeSubdomains`/`outOfScopeMode`/`maxAssetMb`/`denyRules`/`denyDefaultProfile`/`performAnalysisOnUpload`/`captureSourceMaps`) + the uploader's scope/config/analyze flag reflect the resolved snapshot. Response: `{ success, sessionId, scope, projectId, overrideKeys }`.

**Verification note:** the resolution + flat-mapping logic is fully unit-tested in Task 1 (`settingsFromConfig`) and the upload stamping in Task 4. What remains here is thin async glue against `chrome.storage.local` and the uploader — and `background.js` cannot be `vm`-loaded (top-level ESM `import`s), so no in-process test harness exists for it. This task is therefore verified by the **live walkthrough in Task 6's Step 4** (it starts a real project session and asserts the storage keys + outgoing metadata). Keep the glue minimal and lean on the tested helpers.

- [ ] **Step 1: Add the import**

In `chrome-extension/background.js`, next to the Task 3 import, add:

```js
import { settingsFromConfig } from './modules/project-config.js';
```

- [ ] **Step 2: Replace the scope-only apply block**

In `newSession` (`background.js`), the reset/rotate block (lines 846-870, from the `this.processingQueue = [];` reset through `this.processingStats = { … };`) is unchanged. Replace the scope-application tail — the current block below (lines 872-887):

```js
    // Apply the chosen scope: root domains both gate capture (reusing domainScopes)
    // and seed the app-side session scope via the save-files metadata.
    const scope = (request && request.scope) || {};
    const rootDomains = normalizeRootDomains(scope.rootDomains);
    const includeSubdomains = scope.includeSubdomains !== false;
    // A blank scope must RESET capture gating (no else → the new session would silently
    // inherit the previous session's domainScopes and capture out of its intended scope).
    this.settings.domainScopes = rootDomains;
    this.settings.useDomainScope = rootDomains.length > 0;
    this.settings.includeSubdomains = includeSubdomains;
    await chrome.storage.local.set({
      domainScopes: this.settings.domainScopes,
      useDomainScope: this.settings.useDomainScope,
      includeSubdomains
    });
    this.batchUploader.setScope({ rootDomains, includeSubdomains });

    sendResponse({ success: true, sessionId: this.sessionId, scope: { rootDomains, includeSubdomains } });
```

with the project-aware version:

```js
    // Apply the client-resolved effective config. The popup resolved (project.defaults +
    // per-session overrides) and sent the snapshot; here we map it onto the flat capture-gate
    // keys and the uploader. A blank/absent captureConfig leaves the non-scope gate as-is
    // (back-compat with pre-project popups); a blank scope RESETS gating (no else) so a new
    // session can't silently inherit the previous session's domainScopes.
    const req = request || {};
    const scope = req.scope || {};
    const rootDomains = normalizeRootDomains(scope.rootDomains);
    const includeSubdomains = scope.includeSubdomains !== false;
    const captureConfig = (req.captureConfig && typeof req.captureConfig === 'object') ? req.captureConfig : {};
    const overrideKeys = Array.isArray(req.overrideKeys) ? req.overrideKeys : [];
    const projectId = req.projectId || null;

    // Reconstruct the resolved effective config (scope + non-scope groups) and map to storage.
    const effective = { scope: { rootDomains, includeSubdomains }, ...captureConfig };
    const patch = settingsFromConfig(effective);
    Object.assign(this.settings, patch);
    await chrome.storage.local.set(patch);

    // Uploader: scope + project/config snapshot + analyze flag (mirrors updateSettings's sync).
    this.batchUploader.setScope({ rootDomains, includeSubdomains });
    this.batchUploader.setConfig({ projectId, captureConfig, overrideKeys });
    this.batchUploader.setPerformAnalysisOnUpload(this.settings.performAnalysisOnUpload === true);

    sendResponse({
      success: true,
      sessionId: this.sessionId,
      scope: { rootDomains, includeSubdomains },
      projectId,
      overrideKeys
    });
```

- [ ] **Step 3: Sanity-check the module still loads**

`newSession` isn't unit-testable in isolation, but confirm the service worker still parses and the existing MV3-listener test passes (imports resolve, no syntax error):

Run: `cd chrome-extension && node tests/test_mv3_listeners.mjs`
Expected: PASS (unchanged). If it fails on the new `import`, fix the import path before continuing.

- [ ] **Step 4: Commit**

```bash
git add chrome-extension/background.js
git commit -m "feat(ext-projects): apply the resolved project config on newSession (scope + capture gate + uploader)"
```

> End-to-end verification of this task happens in Task 6, Step 4 (live walkthrough).

---

## Task 6: Popup — engagement picker, inherited/override editor, project-aware start

**Files:**
- Modify: `chrome-extension/src/popup/app.jsx` (state; load projects on open; `startNewSession` resolves + sends the snapshot; `homeVm` fields)
- Modify: `chrome-extension/src/popup/components/HomeView.jsx` (picker + inherited display + override editor + quick "New project")
- Modify: `chrome-extension/src/popup/components/SettingsView.jsx` (relabel "Default scope" as the Standalone fallback)

**Interfaces:**
- Consumes: `api.listProjects`/`api.createProject` (Task 3); `resolveEffectiveConfig`, `splitEffective`, `configFromSettings` (Task 1). Sends `api.newSession({ projectId, scope, captureConfig, overrideKeys })` (Task 5).
- Produces: the popup New-Session journey of spec §8 (pick engagement · review inherited · override · start), plus Standalone parity with today.

**Verification:** no DOM test harness exists (and none is added). Verified by the **live extension walkthrough** in Step 4 — the CLAUDE.md §2 completion gate for UI work.

- [ ] **Step 1: Load projects on popup open + hold project/override state (`app.jsx`)**

Add to the imports at the top of `app.jsx`:

```jsx
import { resolveEffectiveConfig, splitEffective, configFromSettings } from '../../modules/project-config.js';
```

Add state near the other `useState` hooks (after `analysis`, ~line 65):

```jsx
  const [projects, setProjects] = useState([]);          // cached engagement list
  const [projectId, setProjectId] = useState(null);      // null => Standalone
  const [overrides, setOverrides] = useState({});        // sparse per-session override doc
```

In the mount effect (the block that seeds `analysis` via `api.getAnalysisProgress()`, ~lines 83-89 — the existing precedent for fetching backend-owned data on open), add a projects fetch:

```jsx
    api.listProjects().then((res) => { if (res && Array.isArray(res.projects)) setProjects(res.projects); });
```

- [ ] **Step 2: Resolve + send the snapshot in `startNewSession` (`app.jsx`)**

Replace the current `startNewSession` (lines 141-158) with the project-aware version. It resolves the effective config from the selected project's defaults (or the current global settings, for Standalone) plus the sparse overrides, then sends the resolved snapshot:

```jsx
  async function startNewSession(rawScope) {
    // Standalone (no project) resolves against the current global settings; a project resolves
    // against its (cached) defaults. Either way the popup is the resolving client and sends the
    // snapshot the capture actually runs under (spec §7).
    const selected = projectId ? projects.find((p) => p.id === projectId) : null;
    const defaults = selected ? selected.defaults : configFromSettings(settings || {});

    // Standalone still types scope ad-hoc in the box; fold it into the overrides as a scope
    // override so one code path handles both. A project uses whatever the override editor set.
    const ovr = { ...overrides };
    if (!selected) {
      const rootDomains = String(rawScope || '').split(/[\s,]+/).filter(Boolean);
      ovr.scope = { rootDomains, includeSubdomains: settings?.includeSubdomains !== false };
    }

    const { effective, overrideKeys } = resolveEffectiveConfig(defaults, ovr);
    const { scope, captureConfig } = splitEffective(effective);

    const res = await api.newSession({ projectId: projectId || null, scope, captureConfig, overrideKeys });
    if (res?.success) {
      // Mirror the applied scope back into local settings so the read-only SCOPE bar updates.
      setSettings((prev) => ({
        ...(prev || {}),
        domainScopes: scope.rootDomains,
        useDomainScope: scope.rootDomains.length > 0,
        includeSubdomains: scope.includeSubdomains,
      }));
      setOverrides({});
      showToast(selected ? `New session · ${selected.name}` : 'New standalone session');
    } else {
      showToast('Could not start session');
    }
    refresh();
  }
```

- [ ] **Step 3: Expose project state + a project creator on `homeVm`, then build the UI (`app.jsx` + `HomeView.jsx`)**

In `app.jsx`, extend the `homeVm` object (lines 254-278) with the project fields the picker needs:

```jsx
    projects,
    projectId,
    selectProject: (id) => { setProjectId(id || null); setOverrides({}); },
    overrides,
    setOverride: (section, key, value) =>
      setOverrides((prev) => ({ ...prev, [section]: { ...(prev[section] || {}), [key]: value } })),
    clearOverride: (section, key) =>
      setOverrides((prev) => {
        const next = { ...prev, [section]: { ...(prev[section] || {}) } };
        delete next[section][key];
        if (Object.keys(next[section]).length === 0) delete next[section];
        return next;
      }),
    createProject: async (name, rootDomains) => {
      const res = await api.createProject({
        name,
        defaults: { scope: { rootDomains: String(rootDomains || '').split(/[\s,]+/).filter(Boolean) } },
      });
      if (res?.success && res.project) {
        setProjects((prev) => [res.project, ...prev]);
        setProjectId(res.project.id);
        showToast(`Project created · ${res.project.name}`);
      } else {
        showToast(res?.error ? `Create failed: ${res.error}` : 'Could not create project');
      }
      return res;
    },
```

In `HomeView.jsx`, replace the `NewSession` component (lines 59-99) with a project-aware version. It: (a) renders an engagement `<select>` (Standalone · each project · ＋ New project), (b) shows the inherited effective config (computed via `resolveEffectiveConfig(project.defaults, overrides)`), tagging each field `inherited`/`overridden`, (c) lets the operator override the common fields (scope root domains, include-subdomains, out-of-scope mode) prefilled from the inherited value, and (d) for Standalone keeps today's free-text scope box. Insert this component (uses `C`/`F` tokens already imported at the top of the file, and `Switch` from `./ui.jsx`):

```jsx
function NewSession({ vm }) {
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState('');            // Standalone free-text scope
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newScope, setNewScope] = useState('');

  const input = {
    flex: 1, minWidth: 0, background: C.inset, border: `1px solid ${C.lineStrong}`,
    borderRadius: '8px', color: C.text, fontFamily: F.mono, fontSize: '11.5px', padding: '7px 9px', outline: 'none',
  };
  const selectStyle = { ...input, fontFamily: F.sans, cursor: 'pointer' };

  const project = vm.projectId ? (vm.projects || []).find((p) => p.id === vm.projectId) : null;
  // Effective preview for a project (Standalone previews from live settings inside the box below).
  const preview = project ? resolveEffectiveConfig(project.defaults, vm.overrides) : null;
  const overridden = new Set(preview ? preview.overrideKeys : []);

  const start = () => { vm.startNewSession(scope); setOpen(false); };

  if (!open) {
    return (
      <button onClick={() => { setScope(vm.startScopeDefault || ''); setOpen(true); }} style={{
        marginTop: '8px', width: '100%', padding: '8px', borderRadius: '9px',
        border: `1px solid ${C.lineHover}`, background: C.control, color: C.textSoft,
        cursor: 'pointer', fontSize: '12px', fontWeight: 600,
      }}>+ New session</button>
    );
  }

  return (
    <div style={{ marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {/* Engagement picker */}
      <select style={selectStyle}
        value={creating ? '__new__' : (vm.projectId || '')}
        onChange={(e) => {
          const v = e.target.value;
          if (v === '__new__') { setCreating(true); }
          else { setCreating(false); vm.selectProject(v || null); }
        }}>
        <option value="">Standalone (no project)</option>
        {(vm.projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        <option value="__new__">＋ New project…</option>
      </select>

      {/* Quick create */}
      {creating && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <input value={newName} placeholder="project name" onInput={(e) => setNewName(e.target.value)} style={input} />
          <input value={newScope} placeholder="root domains (e.g. *.target.com)" onInput={(e) => setNewScope(e.target.value)} style={input} />
          <div style={{ display: 'flex', gap: '8px' }}>
            <button onClick={async () => { const r = await vm.createProject(newName, newScope); if (r?.success) setCreating(false); }}
              style={{ padding: '7px 14px', borderRadius: '8px', border: 'none', background: C.lime, color: C.onLime, cursor: 'pointer', fontSize: '11.5px', fontWeight: 700 }}>Create</button>
            <button onClick={() => setCreating(false)}
              style={{ padding: '7px 12px', borderRadius: '8px', border: `1px solid ${C.lineHover}`, background: C.control, color: C.muted, cursor: 'pointer', fontSize: '11.5px', fontWeight: 600 }}>Cancel</button>
          </div>
        </div>
      )}

      {/* Project selected: inherited/override editor */}
      {!creating && project && preview && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <div style={{ fontSize: '10.5px', color: C.muted }}>
            Scope {overridden.has('scope.rootDomains') ? 'overridden' : 'inherited'}
          </div>
          <input
            value={(vm.overrides.scope?.rootDomains ?? preview.effective.scope.rootDomains).join(' ')}
            placeholder="root domains"
            onInput={(e) => {
              const list = e.target.value.split(/[\s,]+/).filter(Boolean);
              const inherited = (project.defaults.scope?.rootDomains) || [];
              if (JSON.stringify(list) === JSON.stringify(inherited)) vm.clearOverride('scope', 'rootDomains');
              else vm.setOverride('scope', 'rootDomains', list);
            }}
            style={input} />
          <button onClick={() => vm.setOverride('scope', 'includeSubdomains', !preview.effective.scope.includeSubdomains)}
            style={{ display: 'flex', alignItems: 'center', gap: '6px', border: 'none', background: 'none', cursor: 'pointer' }}>
            <span style={{ fontSize: '10.5px', color: C.muted }}>+ subdomains {overridden.has('scope.includeSubdomains') ? '(overridden)' : ''}</span>
            <Switch on={preview.effective.scope.includeSubdomains} variant="sm" />
          </button>
        </div>
      )}

      {/* Standalone: today's free-text scope box */}
      {!creating && !project && (
        <>
          <input value={scope} placeholder="root domains (e.g. app.target.com)" autofocus
            onInput={(e) => setScope(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') start(); if (e.key === 'Escape') setOpen(false); }}
            style={input} />
          <button onClick={vm.toggleSubdomains}
            style={{ display: 'flex', alignItems: 'center', gap: '6px', border: 'none', background: 'none', cursor: 'pointer' }}>
            <span style={{ fontSize: '10.5px', color: C.muted }}>+ subdomains</span>
            <Switch on={vm.includeSubdomains} variant="sm" />
          </button>
        </>
      )}

      {/* Actions */}
      {!creating && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ flex: 1 }} />
          <button onClick={() => setOpen(false)} style={{
            padding: '7px 12px', borderRadius: '8px', border: `1px solid ${C.lineHover}`,
            background: C.control, color: C.muted, cursor: 'pointer', fontSize: '11.5px', fontWeight: 600,
          }}>Cancel</button>
          <button onClick={start} style={{
            padding: '7px 14px', borderRadius: '8px', border: 'none',
            background: C.lime, color: C.onLime, cursor: 'pointer', fontSize: '11.5px', fontWeight: 700,
          }}>Start</button>
        </div>
      )}
    </div>
  );
}
```

Ensure `HomeView.jsx` imports `resolveEffectiveConfig` (add to the top-of-file imports):

```jsx
import { resolveEffectiveConfig } from '../../../modules/project-config.js';
```

Relabel the Standalone default in `SettingsView.jsx`: change the "Default scope" `<Label>` (line 94 area) to make clear it is the fallback used only for Standalone sessions, e.g.:

```jsx
          <Label mb={8}>Standalone default scope <span style={{ color: C.muted, fontWeight: 400 }}>(used when no project is selected)</span></Label>
```

- [ ] **Step 4: Build + live walkthrough (verifies Tasks 5 and 6)**

Build the popup, load the extension, and drive the full journey against a running workspace backend (this branch, so `/api/projects` + the `save-files` project binding exist).

1. Build: `cd chrome-extension && node build.mjs` → expect `Built dist/popup.js` with no esbuild errors.
2. Start the workspace backend (this branch) so the projects API + binding are live, and note its URL (default `http://localhost:3000`).
3. Load unpacked (`chrome://extensions` → Developer mode → Load unpacked → `chrome-extension/`), open the popup, set the Workspace URL (Settings) to the backend if not default.
4. **Standalone parity:** open **+ New session**, leave engagement = *Standalone*, type a root domain, Start. Confirm a toast, and (DevTools → Application → Storage → the extension's `chrome.storage.local`, or `chrome.storage.local.get(console.log)` in the worker console) that `domainScopes`/`useDomainScope`/`includeSubdomains` match, and that a capture on that host uploads with `metadata.captureConfig` present and no `projectId` (check the backend, or the Network tab on `save-files`).
5. **Project flow:** pick **＋ New project…**, create one (name + scope). Reopen **+ New session**, select it, confirm the inherited scope shows; override the root domains (field flips to `overridden`), Start. Confirm the flat gate keys reflect the resolved config and the `save-files` metadata carries `projectId` + `captureConfig` + `overrideKeys` (e.g. `["scope.rootDomains"]`). Confirm the backend bound them: `GET /api/sessions` shows the session's `projectId`/`overrideKeys`/`captureConfig`.
6. **Offline fallback:** stop the backend, reopen the popup — the engagement picker still lists the cached projects (from `projectsCache`).
7. Take a screenshot of the New-Session panel with a project selected + an override applied, to attach to the review.

- [ ] **Step 5: Commit**

```bash
git add chrome-extension/src/popup/app.jsx chrome-extension/src/popup/components/HomeView.jsx chrome-extension/src/popup/components/SettingsView.jsx chrome-extension/dist/popup.js chrome-extension/dist/popup.css
git commit -m "feat(ext-projects): popup engagement picker + inherit/override editor + project-aware start"
```

---

## Self-Review

**1. Spec coverage:**
- §5 config schema/defaults → Task 1 (mirror + parity test). §7 "client resolves, backend stores as-is" → the popup resolves (Task 6 `startNewSession`) and stamps the snapshot (Task 4), background applies without re-resolving (Task 5). §8.1 projects cache + refresh-on-open + offline fallback → Task 2 + Task 3 (`listProjects` cache-wrapped) + Task 6 Step 1. §8.2 quick New-project → Task 6 `createProject`. §8.3 review inherited → Task 6 preview. §8.4 override prefilled from inherited → Task 6 override editor (prefills from `preview.effective`). §8.5 resolve + apply gating + rotate + stamp → Tasks 1/4/5/6. §8 module list (`workspace-client` list/create, `background` newSession + resolve mirror, `batch-uploader` config metadata, popup picker/editor, SettingsView fallback) → Tasks 3/5/4/6. §12 old builds still work (optional metadata; scope-only apply) → Task 5 back-compat branch. §13 extension tests (newSession resolves+applies+stamps; projectsCache read/write+offline; override prefill; standalone parity) → Tasks 1/2/4 (automated) + Task 6 Step 4 (walkthrough). §14.3 Standalone keeps sending captureConfig → Task 6 `startNewSession` folds scope into overrides and always resolves+sends.
- Correctly deferred (not Plan B): workspace SPA Projects view + Session-settings modal + crawler picker (§9, §10) → Plan C; "apply to existing" / reassign (§14) → fast-follows.

**2. Placeholder scan:** none — every code step has complete, runnable content and exact run commands with expected output. UI steps (Tasks 5-6) name the exact files/line-anchors and give full component code + a concrete build/verify walkthrough (no "add appropriate handling").

**3. Type consistency:** `resolveEffectiveConfig` returns `{ effective, overrideKeys }` everywhere it's used (Tasks 1, 6). `splitEffective` returns `{ scope, captureConfig }` (Tasks 1, 6). `settingsFromConfig` returns a flat storage patch keyed by the exact `chrome.storage` keys `newSession` writes (Tasks 1, 5). The `newSession` message shape `{ projectId, scope, captureConfig, overrideKeys }` is produced identically in Task 6 (`api.newSession(...)`) and consumed in Task 5 (`req.projectId/scope/captureConfig/overrideKeys`). `WorkspaceClient.listProjects()` returns `{ success, projects }` — consumed by `listProjectsWithCache`'s `fetcher` contract (Task 2) and the background `listProjects` handler (Task 3). `BatchUploader.setConfig({ projectId, captureConfig, overrideKeys })` (Task 4) is called with exactly those keys in `newSession` (Task 5). `configMetadata()` emits `projectId`/`captureConfig`/`overrideKeys` — the same names the backend `save-files` binder reads (Plan A). System defaults in `SYSTEM_DEFAULTS` (Task 1) byte-match the backend's `project_config.py` (asserted by `test_system_defaults_match_backend`).

---

## Post-review adjustments (§4 gate-1 adversarial design review)

The plan passed the design gate with verdict **BUILD WITH CHANGES**. These fixes were applied during the build (record updated per CLAUDE.md §12):

- **C1 (CRITICAL — corrects the Type-consistency note above):** the plan omitted the `api.js` `newSession` wrapper edit, so `api.newSession({projectId, scope, captureConfig, overrideKeys})` would have double-nested the payload under `scope`, leaving background with `req.scope.rootDomains === undefined` → empty scope → **wide-open capture**. Fixed: `export const newSession = (payload) => send('newSession', payload);` (`src/popup/api.js`). The message shape is identical across Task 5/6 only *because* the wrapper now passes the payload through unchanged.
- **I1 (IMPORTANT):** standalone must record `overrideKeys: []` (spec §4), but folding the ad-hoc scope into `overrides` recorded scope leaves. Fixed in `app.jsx startNewSession`: standalone bakes the typed scope into the resolved **defaults** (not overrides) and resolves with `{}` → `overrideKeys` stays `[]`.
- **I2 (IMPORTANT):** the binding was in-memory only, so an MV3 worker respawn before the first upload created the session standalone. Fixed: `newSession` persists `pendingSessionConfig = {projectId, captureConfig, overrideKeys}`; `initialize()` re-applies it to the uploader after a respawn (mirroring scope re-application). Overwritten by each new session, so it always reflects the current binding.
- **M1 (MINOR):** `resolveEffectiveConfig` now treats a `null` leaf as *inherit* (`sectionOverride[key] != null`), matching the real backend `api/app/project_config.py:103` (the `55b01cd` fix), not the plan's stale `key in` quote. Covered by `test_resolve_null_override_is_inherit`.
- **M4 (MINOR):** `listProjectsWithCache` wraps the fallback `readCache` in its own try/catch so it truly never throws.
- **M2 / M3 (doc):** the popup resolves (the extension **client**), reconciling spec §8.5's "→ background: resolves" with the settled §7 decision (spec §8.5 annotated). Task 5's parse-check uses `node --check background.js` (the MV3-listener test only regex-scans the source).

**Verification after fixes:** `node build.mjs` clean; **all 15 `tests/*.mjs` pass** (4 new + 11 regressions). Popup runtime + `newSession` glue remain live-walkthrough-verified (no DOM/service-worker harness). A §4 gate-2 code review of the final diff followed.

## Post-review adjustments (§4 gate-2 final code review of the diff)

Verdict **SHIP WITH FIXES**; all gate-1 fixes verified correct. Applied:

- **IMPORTANT-1 (real silent wrong-scope bug):** the editor's `scopeText` is local but `overrides` lives in `App` state and was reset only on `selectProject`/successful Start — not on Cancel or a settings round-trip. So: select project → edit scope to `b.com` → Cancel → reopen showed the *inherited* `a.com` in the field while `overrides` still held `b.com`, and Start (which uses `overrides` for a project) captured `b.com`. **Fixed:** `HomeView.openEditor` now discards pending overrides (`vm.selectProject(vm.projectId || null)`) on every open, so the displayed scope always equals what Start applies.
- **MINOR-1 (defensive):** guarded the override editor against a project whose `defaults` lacks a `scope` section (`previewScope`) — unreachable via the API (`create/update_project` always merge full system defaults) but it would have white-screened the panel.
- **MINOR-3:** aligned `configFromSettings` `maxAssetMb` fallback 10→8 to match `background.js loadSettings` (latent; `loadSettings` always sets it).

**FLAGGED, not changed — IMPORTANT-2 (product decision):** because the flat `chrome.storage` settings bag *is* the live capture gate, `newSession` persists the resolved non-scope config (`denyRules`/`outOfScopeMode`/`maxAssetMb`/`performAnalysisOnUpload`/`captureSourceMaps`), so after a project session those become the global default and a later **Standalone** session inherits them (scope already behaved this way pre-Plan-B). Inherent to the "flat bag = gate" model; isolating a pristine "standalone defaults" bag from the live gate is a scope-expanding product decision — deferred to the user, not silently redesigned.

**Deferred minors (fast-follows, none blocking):** M2 (a Standalone start while the popup is on `FALLBACK_SETTINGS` — worker silent >1s — could persist fallback defaults); M4 (theoretical MV3 respawn window between `sessionStore.rotate()` and the `pendingSessionConfig` write); M5 (sticky `projectId` after Start; Standalone prefill lost when toggling to a project and back — cosmetic). Re-verified after fixes: build clean, 15/15 green.
