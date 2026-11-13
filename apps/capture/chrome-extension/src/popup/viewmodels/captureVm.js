// captureVm.js — the capture screens as pure functions of the worker's state (getStatus.capture /
// lastEnded) and the Continue lookup. No hooks, no chrome.*: a Node test covers every word here.
// The worker owns the capture; these only decide which screen shows and what it says.
import { apexForHost, takesSubdomains } from '../../../modules/capture-scope.js';
import { normalizeRootDomains } from '../../../modules/normalize-scope.js';

const SOLO = 'Solo · standalone';

// idle (no capture: the scope-first card) · live · paused · ending (End pressed, uploads draining).
export function capturePhase(status) {
  const c = status && status.capture;
  if (!c) return 'idle';
  if (c.ending) return 'ending';
  return c.paused ? 'paused' : 'live';
}

// "Use app.acme.io as scope": the tab's registrable domain (+ subdomains), exact for IPs/localhost.
// null when the tab is not a website (new tab, chrome://, a PDF).
export function suggestScope(tabHost) {
  const host = String(tabHost || '').trim().toLowerCase();
  if (!host) return null;
  const scope = apexForHost(host);
  return scope ? { host, scope, subdomains: takesSubdomains(scope) } : null;
}

// A typed scope goes through the same normalizer as the worker's Start (a URL or `*.acme.io` must
// not become an entry that matches no host). Kept as typed: "app.acme.io" stays that host.
export function typedScope(text) {
  return normalizeRootDomains([text])[0] || '';
}

export function scopeLabel(scope) {
  const list = Array.isArray(scope) ? scope : [scope];
  return list.filter(Boolean).map((e) => (takesSubdomains(e) ? `${e} + subdomains` : e)).join(', ');
}

export function formatElapsed(ms) {
  const total = Math.max(0, Math.floor((ms || 0) / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

export function engagementName(engagementId, projects) {
  if (!engagementId) return SOLO;
  const found = (projects || []).find((p) => p.id === engagementId);
  return found ? found.name : 'Another engagement';
}

// One "Continue" option per session whose scope holds this entry (the server's lookup). The largest
// capture is shown rather than the latest, so a big earlier run stays in view.
export function continueOptions(sessions, projects, scope) {
  return (sessions || []).filter((m) => m && m.external_id).map((match) => {
    const parts = [plural(match.captures || 0, 'capture')];
    if (match.last_capture_at) {
      parts.push(`last ${new Date(match.last_capture_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })}`);
    }
    const big = match.largest;
    return {
      key: match.external_id,
      match,
      title: `Continue “${scope}”`,
      engagement: engagementName(match.engagement_id, projects),
      line: parts.join(' · '),
      best: big ? `largest: ${plural(big.files || 0, 'file')} · ${plural(big.endpoints || 0, 'endpoint')}` : '',
    };
  });
}

export function defaultChoice(options) {
  return options && options.length ? options[0].key : 'new';
}

// "Continuing acme.io · capture 5 · 0:42 · 152 files" — what you are capturing into, for how long.
export function liveSubtitle(capture, files) {
  const c = capture || {};
  const parts = [];
  if (c.mode === 'continue') parts.push(`Continuing ${(c.scope || [])[0] || 'session'}${c.ordinal ? ` · capture ${c.ordinal}` : ''}`);
  else if (c.mode === 'new') parts.push('New session');
  if (c.paused && c.pausedByRestart) parts.push('paused by restart');
  parts.push(formatElapsed(c.elapsedMs), plural(files || 0, 'file'));
  return parts.join(' · ');
}

export function endedBanner(lastEnded) {
  if (!lastEnded) return null;
  const title = `Capture ended · ${(lastEnded.scope || []).join(', ')}`;
  const body = lastEnded.reason === 'ended_elsewhere'
    ? 'It was ended by the workspace (idle for 24 h, or ended there). The scope is cleared for your next capture.'
    : 'The final analysis finishes in the workspace. The scope is cleared for your next capture.';
  return { title, body };
}

export function startErrorText(res) {
  const error = res && res.error;
  if (error === 'ending') return 'The previous capture is still finishing — try again in a moment';
  if (error === 'session_expired') return 'Session expired — sign in to start';
  if (error === 'capture_in_progress') return 'A capture is already running';
  if (error === 'no_scope') return 'Set a scope to start';
  return 'Could not start the capture';
}

export function analyzeLabel(analysis, scripts) {
  const a = analysis || { status: 'idle' };
  const c = a.counts || {};
  if (a.status === 'starting') return 'Starting analysis…';
  if (a.status === 'running') {
    return `${a.updating ? 'Updating with new files…' : 'Analyzing…'} ${c.completed || 0}/${c.total || 0}`;
  }
  if (a.status === 'done') return `Analyzed ✓ · ${c.completed || 0} done`;
  return `Analyze ${plural(scripts || 0, 'script')}`;
}
