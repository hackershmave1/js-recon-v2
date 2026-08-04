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

function test_resolve_null_override_is_inherit() {
  // Mirror api/app/project_config.py: a null leaf = inherit (not replace), recorded nowhere.
  const d = systemDefaults(); d.scope.includeSubdomains = true;
  const { effective, overrideKeys } = resolveEffectiveConfig(d, { scope: { includeSubdomains: null } });
  assert.equal(effective.scope.includeSubdomains, true);
  assert.deepEqual(overrideKeys, []);
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
  test_resolve_null_override_is_inherit,
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
