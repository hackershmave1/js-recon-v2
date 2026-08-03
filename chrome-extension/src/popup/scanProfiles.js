// scanProfiles.js — "scan type" presets for the extension popup. Mirrors the web
// app's web/src/scanProfiles.js but WITHOUT a discovery engine (the extension is
// capture-only). The options map to the backend extract_all toggles and are sent as
// metadata.analysisOptions on upload. Keep extractor keys in sync with the web copy.

export const ANALYSIS_TOGGLES = [
  { key: 'use_rep_endpoints', label: 'Endpoints' },
  { key: 'use_rep_secrets', label: 'Secrets' },
  { key: 'use_jsluice', label: 'jsluice' },
  { key: 'use_parameter_extraction', label: 'Parameters' },
  { key: 'use_sensitive_file_detection', label: 'Sensitive files' },
  { key: 'include_sourcemap', label: 'Source maps' }
];

export const SCAN_PROFILES = {
  quick: {
    label: 'Quick', desc: 'Endpoints + secrets only',
    options: { use_rep_endpoints: true, use_rep_secrets: true, use_jsluice: false, use_parameter_extraction: false, use_sensitive_file_detection: false, include_sourcemap: false }
  },
  standard: {
    label: 'Standard', desc: 'Endpoints, secrets, params, files, source maps',
    options: { use_rep_endpoints: true, use_rep_secrets: true, use_jsluice: false, use_parameter_extraction: true, use_sensitive_file_detection: true, include_sourcemap: true }
  },
  deep: {
    label: 'Deep', desc: 'Everything incl. jsluice',
    options: { use_rep_endpoints: true, use_rep_secrets: true, use_jsluice: true, use_parameter_extraction: true, use_sensitive_file_detection: true, include_sourcemap: true }
  }
};

export const DEFAULT_PROFILE = 'standard';

export function matchProfile(options) {
  for (const [key, p] of Object.entries(SCAN_PROFILES)) {
    if (ANALYSIS_TOGGLES.every((t) => !!options[t.key] === !!p.options[t.key])) return key;
  }
  return 'custom';
}
