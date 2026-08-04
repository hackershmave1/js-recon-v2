// scanProfiles.js — "scan type" presets shared by the New Recon modal. A profile
// bundles the analysis extractor toggles (passed to the backend as analysisOptions →
// ComprehensiveExtractor.extract_all) plus, for recon, a discovery engine. The
// extension mirrors the same extractor toggles (it has no discovery engine).
// Keep in sync with chrome-extension/src/popup/scanProfiles.js.

// Individual extractor toggles surfaced in Advanced mode. Keys match the backend
// options accepted by extract_all (_resolve_extractor_options).
export const ANALYSIS_TOGGLES = [
  { key: 'use_rep_endpoints', label: 'Endpoints', hint: 'REP endpoint extractor (pure Python)' },
  { key: 'use_rep_secrets', label: 'Secrets', hint: 'REP/Kingfisher secret extractor' },
  { key: 'use_jsluice', label: 'jsluice', hint: 'External jsluice CLI: endpoints + secrets (optional binary)' },
  { key: 'use_parameter_extraction', label: 'Parameters', hint: 'Query/body parameter extraction' },
  { key: 'use_sensitive_file_detection', label: 'Sensitive files', hint: 'Referenced sensitive-file detection' },
  { key: 'include_sourcemap', label: 'Source maps', hint: 'Reconstruct original sources from source maps' }
];

export const ENGINES = [
  { key: 'headless', label: 'Headless', hint: 'Render the page in a headless browser (default)' },
  { key: 'hybrid', label: 'Hybrid', hint: 'Headless + katana crawl (broader discovery)' },
  { key: 'katana', label: 'Katana', hint: 'Katana crawler (requires katana binary)' },
  { key: 'vespasian', label: 'Vespasian', hint: 'Vespasian engine (requires vespasian binary)' }
];

export const SCAN_PROFILES = {
  quick: {
    label: 'Quick', desc: 'Endpoints + secrets only — fastest signal', engine: 'headless',
    options: { use_rep_endpoints: true, use_rep_secrets: true, use_jsluice: false, use_parameter_extraction: false, use_sensitive_file_detection: false, include_sourcemap: false }
  },
  standard: {
    label: 'Standard', desc: 'Endpoints, secrets, params, sensitive files, source maps', engine: 'headless',
    options: { use_rep_endpoints: true, use_rep_secrets: true, use_jsluice: false, use_parameter_extraction: true, use_sensitive_file_detection: true, include_sourcemap: true }
  },
  deep: {
    label: 'Deep', desc: 'Everything incl. jsluice + hybrid crawl', engine: 'hybrid',
    options: { use_rep_endpoints: true, use_rep_secrets: true, use_jsluice: true, use_parameter_extraction: true, use_sensitive_file_detection: true, include_sourcemap: true }
  }
};

export const DEFAULT_PROFILE = 'standard';

// Does an option set match a named preset exactly? (else the UI shows "Custom").
export function matchProfile(options, engine) {
  for (const [key, p] of Object.entries(SCAN_PROFILES)) {
    const sameOpts = ANALYSIS_TOGGLES.every((t) => !!options[t.key] === !!p.options[t.key]);
    if (sameOpts && (engine === undefined || engine === p.engine)) return key;
  }
  return 'custom';
}
