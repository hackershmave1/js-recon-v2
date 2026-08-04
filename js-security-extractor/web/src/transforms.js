// transforms.js — map raw API responses into the view-models the views render.
// Defensive throughout: the dashboard data shapes vary, so every accessor has a
// fallback and the UI degrades to empty states rather than throwing.
import { SEV, TYPE } from './theme.js';

export function relTime(iso) {
  if (!iso) return '—';
  const t = typeof iso === 'number' ? iso : Date.parse(iso);
  if (Number.isNaN(t)) return '—';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function hostOf(session) {
  return session?.name || session?.host || session?.target || (session?.id ? `session ${String(session.id).slice(0, 8)}` : 'unknown');
}

// The backend records carry confidence/type but not an explicit severity; derive a
// pragmatic severity so Priority Findings can rank. Refined in the Findings phase.
export function deriveSeverity(rec, kind) {
  const conf = (rec.confidence || rec.conf || '').toLowerCase();
  if (kind === 'secret') {
    if (conf === 'high') return 'critical';
    if (conf === 'medium') return 'high';
    return 'low';
  }
  if (kind === 'endpoint') {
    const v = `${rec.url || rec.value || ''}`.toLowerCase();
    if (/admin|internal|export|impersonate|debug/.test(v)) return 'high';
    if (conf === 'high') return 'medium';
    return 'low';
  }
  return 'low';
}

function secretLabel(rec) {
  return rec.ruleName || rec.rule || rec.type || 'Secret';
}
function endpointLabel(rec) {
  const method = (rec.method || 'GET').toUpperCase();
  return `${method} ${rec.url || rec.value || ''}`.trim();
}

export function hostnameOf(url) {
  try { return new URL(url).hostname.toLowerCase(); } catch (e) { return ''; }
}
function pathOf(url) {
  try { return (new URL(url).pathname || '').toLowerCase(); } catch (e) { return (url || '').toLowerCase(); }
}
// Last two labels as a best-effort registrable domain (no PSL), mirrors the popup.
function registrable(host) { return host.split('.').slice(-2).join('.'); }

const TRACKER_HOSTS = [
  'google-analytics.com', 'googletagmanager.com', 'doubleclick.net', 'segment.io',
  'segment.com', 'mixpanel.com', 'hotjar.com', 'facebook.net', 'fbcdn.net',
  'amplitude.com', 'sentry.io', 'clarity.ms', 'newrelic.com', 'optimizely.com'
];
const LIB_HINTS = [
  /jquery[.-]/i, /\breact(-dom)?[.-]/i, /\bvue[.-]/i, /angular[.-]/i, /lodash/i,
  /bootstrap/i, /\bd3[.-]/i, /moment/i, /polyfill/i, /\bvendors?[~.\-]/i, /runtime~/i
];

// Classify a source asset into app / lib / cms / tracker. Ported from the popup's
// asset-classifier so the workspace "Focus: app code" toggle mutes the same noise.
export function classifyAsset(url) {
  const host = hostnameOf(url);
  const path = pathOf(url);
  const last = path.split('/').pop() || '';
  if (TRACKER_HOSTS.some((h) => host === h || host.endsWith('.' + h)) ||
      /\b(gtag|analytics|gtm|fbevents|hotjar)\b/i.test(last)) return 'tracker';
  if (path.includes('/wp-content/') || path.includes('/wp-includes/') || /\bwp-/.test(path)) return 'cms';
  if (LIB_HINTS.some((re) => re.test(last) || re.test(path)) || /\.min\.js(\?|$)/i.test(path)) return 'lib';
  return 'app';
}

// in-scope / subdomain / third-party relative to the target host. webpack:// and
// relative paths are reconstructed app sources, so they count as in-scope.
export function scopeOf(assetUrl, targetHost) {
  if (!assetUrl || /^webpack:\/\//i.test(assetUrl) || !/^https?:\/\//i.test(assetUrl)) return 'in';
  const a = hostnameOf(assetUrl);
  const t = (targetHost || '').toLowerCase();
  if (!a || !t) return 'in';
  if (a === t) return 'in';
  if (registrable(a) === registrable(t)) return 'sub';
  return 'third';
}

// Stable, synchronous, low-collision hash (cyrb53) → opaque finding identity.
// Backend stores it verbatim (see api/app/models/finding_status.py); keep the
// canonical input string "<type>|<value>|<file>|<line>" in lockstep with that model.
function cyrb53(str, seed = 0) {
  let h1 = 0xdeadbeef ^ seed, h2 = 0x41c6ce57 ^ seed;
  for (let i = 0; i < str.length; i++) {
    const ch = str.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(16);
}
export function fingerprintOf(f) {
  return cyrb53(`${f.kind}|${f.value}|${f.file}|${f.line}`);
}

const EXTRACTOR_LABELS = {
  rep_kingfisher: 'Kingfisher', jsluice_secrets: 'jsluice', jsluice_urls: 'jsluice',
  rep_endpoint_extractor: 'REP endpoint', sourcemapper: 'sourcemapper',
  parameter_extractor: 'ParameterExtractor', custom_patterns: 'custom', reconstructed: 'sourcemapper'
};
function extractorLabel(rec) {
  const raw = rec.extractor || (rec.extractors && rec.extractors[0]) || '';
  if (raw === 'multiple') return 'multiple extractors';
  return EXTRACTOR_LABELS[raw] || raw || 'unknown';
}

// Human "what this is" / impact text derived from type + severity (the real API
// has no curated copy; this gives analysts orientation without inventing facts).
function describe(kind, label, sev) {
  if (kind === 'secret') {
    return {
      description: `A ${label} surfaced in client-side JavaScript. Anything shipped to the browser is readable by anyone who loads the page.`,
      impact: sev === 'critical' || sev === 'high'
        ? 'Treat as live until proven otherwise: rotate the credential and check provider logs for misuse.'
        : 'Verify whether the value is sensitive or public-by-design before acting.'
    };
  }
  return {
    description: 'An endpoint reference extracted from client code. It reveals a route the application talks to.',
    impact: sev === 'critical' || sev === 'high'
      ? 'High-value target — probe for broken access control / IDOR and missing auth.'
      : 'Recon value: enumerate the surface and check authorization server-side.'
  };
}

function makeFinding(rec, kind, idx, targetHost) {
  const value = kind === 'secret' ? (rec.value || '') : (rec.url || rec.value || '');
  const sourceUrl = rec.source_file_url || rec.file || '';
  const severity = deriveSeverity(rec, kind);
  const label = kind === 'secret' ? secretLabel(rec) : endpointLabel(rec);
  const file = rec.file || sourceUrl || '';
  const f = {
    id: `${kind === 'secret' ? 's' : 'e'}${idx}`, kind, severity, label, value,
    file, line: rec.line || 0, col: rec.column || 0,
    // The bundle DbFile id whose sourcemap produced this finding. Equals the id
    // passed to /api/files/{id}/reconstructed-sources, so the Sources view can
    // open the exact reconstructed file the finding points at.
    fileId: rec.source_file_id || rec.sourceFileId || '',
    conf: (rec.confidence || 'medium').toLowerCase(),
    extractor: extractorLabel(rec), context: rec.context || '',
    sourceUrl, origin: hostnameOf(sourceUrl) || hostnameOf(file) || '',
    scope: scopeOf(sourceUrl || file, targetHost),
    cls: classifyAsset(sourceUrl || file)
  };
  Object.assign(f, describe(kind, label, severity));
  f.fingerprint = fingerprintOf(f);
  return f;
}

// The target host drives scope (in/sub/third). A session's display name is often
// not a hostname, so prefer the most common source-file host across the findings
// themselves; fall back to the supplied hint only when none can be derived.
export function targetHostFromAnalysis(analysis, hint) {
  const counts = {};
  const tally = (rec) => {
    const h = hostnameOf(rec.source_file_url || rec.file || '');
    if (h) counts[h] = (counts[h] || 0) + 1;
  };
  (analysis?.secrets || []).forEach(tally);
  (analysis?.endpoints || []).forEach(tally);
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
  return (top && top[0]) || hostnameOf(hint) || hint || '';
}

export function findingsFromAnalysis(analysis, hostHint) {
  const targetHost = targetHostFromAnalysis(analysis, hostHint);
  const out = [];
  (analysis?.secrets || []).forEach((r, i) => out.push(makeFinding(r, 'secret', i, targetHost)));
  (analysis?.endpoints || []).forEach((r, i) => out.push(makeFinding(r, 'endpoint', i, targetHost)));
  return out;
}

export const SEV_RANK = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };

export function topFindings(findings, n = 5) {
  return findings
    .filter((f) => f.severity === 'critical' || f.severity === 'high')
    .sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity])
    .slice(0, n)
    .map((f) => ({
      id: f.id, label: f.label,
      sev: SEV[f.severity] || SEV.low,
      type: TYPE[f.kind] || TYPE.endpoint,
      fileLine: f.file ? `${f.file}:${f.line}` : f.value
    }));
}

// Surface-map nodes from the asset graph; first node is the root host.
export function surfaceFrom(assetGraph, fallbackHost) {
  const graph = assetGraph?.graph || assetGraph || {};
  const rawNodes = graph.nodes || [];
  const stats = graph.stats || {};
  const colorFor = (node) => {
    const k = (node.type || node.kind || '').toLowerCase();
    if (k.includes('host') || k.includes('origin')) return '#CDEB45';
    if (k.includes('map') || k.includes('source')) return '#5BD6C0';
    if (k.includes('third') || k.includes('external')) return '#FF8A47';
    return '#6BA8FF';
  };
  const nodes = rawNodes.map((n) => ({ label: n.label || n.url || n.id || '', color: colorFor(n) }));
  if (nodes.length && !rawNodes.some((n) => `${n.type || ''}`.toLowerCase().includes('host'))) {
    nodes.unshift({ label: fallbackHost, color: '#CDEB45' });
  }
  const hosts = stats.hosts || stats.hostCount || new Set(rawNodes.map((n) => n.host).filter(Boolean)).size || (nodes.length ? 1 : 0);
  const maps = stats.maps || stats.sourcemaps || stats.mapCount || 0;
  return { nodes, hosts, maps };
}

function pct(part, whole) {
  if (!whole) return 0;
  return Math.round((part / whole) * 100);
}

// Capture-coverage bars from a recon job's coverage block.
export function coverageBars(coverage) {
  const c = coverage || {};
  const disc = c.discovered_js || c.discovered || 0;
  const rows = [
    { label: 'Fetched', part: c.fetched_js || 0, whole: disc, color: '#6BA8FF' },
    { label: 'Ingested', part: c.ingested_js || 0, whole: disc, color: '#7C8CFF' },
    { label: 'Analyzed', part: c.analyzed_js || 0, whole: disc, color: '#CDEB45' },
    { label: 'Maps processed', part: c.map_processed || 0, whole: c.map_detected || 0, color: '#5BD6C0' }
  ];
  return rows.map((r) => {
    const p = pct(r.part, r.whole);
    return { label: r.label, frac: `${r.part}/${r.whole}`, pct: p, color: r.color, pctc: p >= 90 ? '#CDEB45' : p >= 60 ? '#FFC73D' : '#FF8A47' };
  });
}

export function missReasons(coverage) {
  const fr = coverage?.failure_reasons || {};
  return Object.entries(fr)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([label, n]) => ({ n: String(n), label }));
}

// ---- Sources view ----
// A "JS bundle" is a captured file whose sourcemap reconstructed original sources
// (or, failing that, a raw JS file we can still show). The Sources view groups the
// tree by bundle and lazily loads each bundle's reconstructed sources on expand —
// the reconstructed-sources endpoint re-processes the map per call, so eager
// session-wide fan-out is avoided on purpose.
const RECON_DONE = new Set(['completed', 'completed_limited']);

export function isJsBundle(file) {
  const ct = (file.contentType || '').toLowerCase();
  const url = (file.url || '').toLowerCase();
  return ct.includes('javascript') || ct.includes('ecmascript') || /\.m?js(\?|#|$)/.test(url);
}

export function hasReconstructed(file) {
  const sm = file.sourceMap;
  return !!(sm && RECON_DONE.has(sm.processingStatus) && (sm.reconstructedFilesCount || 0) > 0);
}

// Short label for a bundle: the URL's last path segment (or host root).
export function bundleLabel(url) {
  try {
    const u = new URL(url);
    const last = (u.pathname || '/').split('/').filter(Boolean).pop();
    return last || u.hostname;
  } catch (e) {
    return (url || '').split('/').filter(Boolean).pop() || url || 'bundle';
  }
}

// Findings that point at one bundle. When `path` is given, restrict to the
// reconstructed source at that path; the finding.file and reconstructed path are
// produced by the same normalizer server-side, so this is a raw equality match.
export function findingsForDoc(findings, fileId, path) {
  const id = String(fileId);
  return (findings || []).filter((f) => {
    if (String(f.fileId) !== id) return false;   // ids are stringified UUIDs both sides
    return path == null ? true : f.file === path;
  });
}

// Map of line number -> findings on that line, for the open document.
export function findingsByLine(docFindings) {
  const map = {};
  (docFindings || []).forEach((f) => {
    const ln = f.line || 0;
    (map[ln] = map[ln] || []).push(f);
  });
  return map;
}

// Build a path-sorted, depth-tagged list of reconstructed files for one bundle so
// the rail can render an indented tree without recursive folder state.
export function sortedSourceRows(files) {
  const seen = new Set();
  return (files || [])
    .filter((file) => { const p = file.path || ''; if (seen.has(p)) return false; seen.add(p); return true; })
    .sort((a, b) => (a.path || '').localeCompare(b.path || ''))
    .map((file) => {
      const parts = (file.path || '').split('/').filter(Boolean);
      return {
        path: file.path || '',
        name: parts[parts.length - 1] || file.path || '',
        depth: Math.max(0, parts.length - 1),
        size: file.size || 0,
        type: file.type || 'unknown'
      };
    });
}
