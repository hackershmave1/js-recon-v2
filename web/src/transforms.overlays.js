// transforms.overlays.js — pure view-model/serialization helpers for the Phase 4
// shell overlays (Activity drawer, Search palette, Export). Split out of
// transforms.js to keep each module under the file-size cap. Theme-light: only
// SEV/TYPE come from theme.js; hostnameOf/SEV_RANK are shared from transforms.js.
import { SEV, TYPE } from './theme.js';
import { hostnameOf, SEV_RANK } from './transforms.js';

// ---- Activity drawer ----
// A recon job moves Discover → Fetch → Analyze → Done. The job snapshot carries a
// `coverage` block (discovered_js / fetched_js / analyzed_js + rates) and a status;
// derive a stepper + progress bar view-model from it. Pure + tested so the drawer
// stays a thin renderer.
const RUNNING_JOB = new Set(['running', 'queued', 'cancelling']);
const ACTIVITY_STAGES = ['Discover', 'Fetch', 'Analyze', 'Done'];

// Colors referenced by jobActivityVm without importing the whole theme palette into
// the data layer (only SEV/TYPE come from theme.js).
const C_LIME = '#CDEB45', C_AMBER = '#FFC73D', C_BLUE = '#6BA8FF', C_RED = '#FF4D5E', C_FAINT = '#7E8AA3';

export function jobActivityVm(job) {
  const status = String(job?.status || '').toLowerCase();
  const cov = job?.coverage || {};
  const disc = cov.discovered_js || 0;
  const fet = cov.fetched_js || 0;
  const ana = cov.analyzed_js || 0;
  const rates = cov.rates || {};
  const active = RUNNING_JOB.has(status);
  const done = status === 'completed';
  const terminal = done || status === 'failed' || status === 'cancelled';

  // Index of the stage currently in progress (Discover 0 / Fetch 1 / Analyze 2 /
  // Done 3). Terminal jobs are "past Done" (4) so every stage renders complete.
  let stage;
  if (terminal) stage = 4;
  else if (ana > 0) stage = 2;
  else if (fet > 0) stage = 1;
  else stage = 0;

  const stages = ACTIVITY_STAGES.map((label, i) => ({
    label,
    state: i < stage ? 'done' : (i === stage && active ? 'active' : 'pending')
  }));

  // Active progress: the analysis rate once analyzing, else the fetch rate.
  const analyzing = ana > 0;
  const stageLabel = analyzing ? 'Analyzing JS' : (fet > 0 ? 'Fetching assets' : 'Discovering');
  const partDone = analyzing ? ana : fet;
  const pct = Math.round(analyzing ? (rates.analysisPct ?? 0) : (rates.fetchPct ?? 0));

  const targets = job?.targets || [];
  const target = targets[0] || '—';
  const STATUS_C = { running: C_LIME, queued: C_AMBER, cancelling: C_AMBER, completed: C_BLUE, failed: C_RED, cancelled: C_FAINT };

  return {
    jobId: job?.jobId || job?.id || '',
    title: hostnameOf(target) || target,
    target,
    status,
    statusLabel: status.toUpperCase() || 'UNKNOWN',
    statusc: STATUS_C[status] || C_FAINT,
    active, doneState: terminal,
    stages, stageLabel, done: partDone, total: disc, pct,
    summary: terminal ? jobSummaryLine(job) : '',
    canStop: active
  };
}

function jobSummaryLine(job) {
  const status = String(job?.status || '').toLowerCase();
  const cov = job?.coverage || {};
  const stored = job?.summary?.stored || 0;
  if (status === 'failed') return job?.error ? `Failed: ${job.error}` : 'Failed';
  if (status === 'cancelled') return `Cancelled · ${stored} files stored`;
  return `${stored} files stored · ${cov.analyzed_js || 0} analyzed · ${cov.map_fetched || 0} maps`;
}

// ---- Search palette ----
// Substring match over a finding's label / value / file, ranked by best (earliest)
// hit position then severity. Returns decorated rows carrying the finding ref so the
// caller can deep-link into Findings (drawer) or Sources.
export function searchFindings(findings, query, limit = 8) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return [];
  const scored = [];
  (findings || []).forEach((f) => {
    const hay = `${f.label}\n${f.value}\n${f.file}`.toLowerCase();
    const pos = hay.indexOf(q);
    if (pos < 0) return;
    scored.push({ f, rank: pos * 10 + (SEV_RANK[f.severity] ?? 4) });
  });
  scored.sort((a, b) => a.rank - b.rank);
  return scored.slice(0, limit).map(({ f }) => ({
    fingerprint: f.fingerprint,
    finding: f,
    label: f.label,
    type: TYPE[f.kind] || TYPE.endpoint,
    sev: SEV[f.severity] || SEV.low,
    fileLine: f.file ? `${f.file}:${f.line}` : f.value
  }));
}

// ---- Export ----
// No backend export endpoint exists, so serialize the in-memory findings client-side.
const EXPORT_COLUMNS = ['kind', 'severity', 'label', 'value', 'file', 'line', 'col', 'scope', 'cls', 'conf', 'extractor', 'fingerprint'];

export function findingsToJson(findings, meta = {}) {
  return JSON.stringify({
    exportedAt: new Date().toISOString(),
    target: meta.target || '',
    count: (findings || []).length,
    findings: (findings || []).map((f) => {
      const row = {};
      EXPORT_COLUMNS.forEach((k) => { row[k] = f[k]; });
      return row;
    })
  }, null, 2);
}

function csvCell(value) {
  const s = value == null ? '' : String(value);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function findingsToCsv(findings) {
  const lines = [EXPORT_COLUMNS.join(',')];
  (findings || []).forEach((f) => lines.push(EXPORT_COLUMNS.map((k) => csvCell(f[k])).join(',')));
  return lines.join('\n');
}
