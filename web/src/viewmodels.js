// viewmodels.js — pure builders that turn raw API data into the view-models the
// Sessions grid renders. Kept out of app.jsx to hold the controller under the
// file-size cap; no Preact/JSX here, only data + theme colour tokens.
import { C } from './theme.js';
import { relTime, hostOf } from './transforms.js';

export const RUNNING = new Set(['running', 'queued', 'cancelling']);

// Human-readable scope: the root domains plus whether subdomains are in-scope.
export function scopeLabelOf(rootDomains, includeSubdomains) {
  const roots = (rootDomains || []).filter(Boolean);
  if (!roots.length) return 'No scope set';
  const shown = roots.slice(0, 2).join(', ') + (roots.length > 2 ? ` +${roots.length - 2}` : '');
  return `${shown} · ${includeSubdomains ? 'incl. subdomains' : 'same-origin'}`;
}

// Most-recent recon job for a session (by createdAt desc), or null.
export function latestJobForSession(jobs, sessionId) {
  const matches = (jobs || []).filter((j) => (j.sessionId || j.session_id) === sessionId);
  matches.sort((a, b) => Date.parse(b.createdAt || b.created_at || 0) - Date.parse(a.createdAt || a.created_at || 0));
  return matches[0] || null;
}

// Per-session card view-model: stats, status colours, the latest job id (for Stop),
// and resume eligibility + a backend-shaped resume payload (Continue Crawl).
export function buildSessionsVm(sessions, jobs, selectedId) {
  return (sessions || []).map((s) => {
    const job = latestJobForSession(jobs, s.id);
    const running = !!job && RUNNING.has((job.status || '').toLowerCase());
    const cov = Math.round(
      (job?.coverage?.rates?.analysisPct) ??
      (typeof s.captureCoverage === 'number' ? s.captureCoverage : s.captureCoverage?.analysisPct) ?? 0
    );
    const summary = s.analysisSummary || {};
    const fileCount = s.fileCount ?? summary.files ?? 0;
    const statusLabel = running ? 'RUNNING' : (s.id === selectedId ? 'ACTIVE' : 'DONE');
    const statusc = running ? C.amber : (s.id === selectedId ? C.lime : C.blue);
    const statusbg = running ? 'rgba(255,199,61,0.13)' : (s.id === selectedId ? 'rgba(205,235,69,0.13)' : 'rgba(107,168,255,0.13)');
    // Resume is eligible only when not running, the session has files, and a prior
    // recon job recorded a target URL (extension-only sessions have no job to resume).
    const jobId = job?.jobId || job?.id || null;
    const resumeUrl = (job?.targets && job.targets[0]) || null;
    const opts = job?.options || {};
    const canResume = !running && fileCount > 0 && !!resumeUrl;
    const rootDomains = Array.isArray(s.rootDomains) ? s.rootDomains : [];
    const includeSubdomains = s.includeSubdomains !== false;
    return {
      id: s.id, host: hostOf(s), name: s.name || '',
      rootDomains, includeSubdomains, scopeLabel: scopeLabelOf(rootDomains, includeSubdomains),
      files: fileCount,
      endpoints: summary.endpoints ?? summary.endpointCount ?? 0,
      secrets: summary.secrets ?? summary.secretCount ?? 0,
      lastRun: running ? 'running' : relTime(job?.finishedAt || job?.finished_at || s.createdAt || s.created_at),
      cov,
      statusLabel, statusc, statusbg,
      border: running ? 'rgba(255,199,61,0.25)' : (s.id === selectedId ? 'rgba(205,235,69,0.25)' : C.line),
      running, jobId, canResume,
      // Faithfully re-run the prior crawl: carry through every option the backend
      // honours, not just the headline ones, so resume reuses the original tuning.
      resumePayload: canResume ? {
        sessionId: s.id, url: resumeUrl,
        sameOriginOnly: opts.sameOriginOnly !== false,
        includeSourceMaps: opts.includeSourceMaps !== false,
        performAnalysis: opts.performAnalysis !== false,
        maxDepth: opts.maxDepth ?? 2,
        maxAssets: opts.maxAssets ?? 300,
        // Reuse the original scan type. Pre-feature jobs have no analysisOptions, so they
        // resume on the backend defaults (which equal the Standard preset).
        ...(opts.analysisOptions ? { analysisOptions: opts.analysisOptions } : {}),
        ...(opts.discoveryEngine ? { discoveryEngine: opts.discoveryEngine } : {}),
        ...(opts.waitAfterLoadMs != null ? { waitAfterLoadMs: opts.waitAfterLoadMs } : {}),
        ...(opts.timeoutSeconds != null ? { timeoutSeconds: opts.timeoutSeconds } : {}),
        ...(opts.maxResponseBytes != null ? { maxResponseBytes: opts.maxResponseBytes } : {})
      } : null
    };
  });
}
