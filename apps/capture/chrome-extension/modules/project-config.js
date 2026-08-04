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
  const isObj = (v) => v && typeof v === 'object' && !Array.isArray(v);
  for (const [key, value] of Object.entries(patch || {})) {
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
      // null/absent = inherit (mirror api/app/project_config.py: `section_override.get(key) is not None`).
      if (sectionOverride[key] != null) {
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
      maxAssetMb: (typeof s.maxAssetMb === 'number' && s.maxAssetMb > 0) ? Math.min(10, s.maxAssetMb) : 8,
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
