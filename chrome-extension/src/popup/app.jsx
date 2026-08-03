// app.jsx — popup controller. Owns view state, polls the background worker, builds
// the view-models for Home/Settings, and turns UI events into background messages.
import { useEffect, useRef, useState } from 'preact/hooks';
import { C } from './theme.js';
import { Toast } from './components/ui.jsx';
import { HomeView } from './components/HomeView.jsx';
import { SettingsView } from './components/SettingsView.jsx';
import * as api from './api.js';

const NOISE = new Set(['lib', 'cms', 'tracker']);

// Fallback settings so the popup still renders if the service worker is slow or
// unavailable (e.g. opened outside an extension context). Mirrors background defaults.
const FALLBACK_SETTINGS = {
  includeSubdomains: true, muteNoise: true, outOfScopeMode: 'tag', maxAssetMb: 8,
  denyDefaultProfile: true, performAnalysisOnUpload: false, captureAuthContext: true,
  workspaceUrl: '', domainScopes: [], useDomainScope: false,
  denyRules: [
    { tag: 'CMS', pattern: '/wp-content/plugins/*' },
    { tag: 'CMS', pattern: '/wp-includes/*' },
    { tag: 'TRACK', pattern: '*.google-analytics.com' },
    { tag: 'TRACK', pattern: '*.doubleclick.net' },
    { tag: 'LIB', pattern: '*/jquery*.min.js' }
  ]
};

function basename(url) {
  try {
    const u = new URL(url);
    const seg = u.pathname.split('/').filter(Boolean);
    return seg.length ? seg[seg.length - 1] : u.hostname;
  } catch (e) {
    return url;
  }
}

function fileMeta(f) {
  const kb = Math.max(1, Math.round((f.size || 0) / 1024));
  const parts = [`${kb} KB`];
  if (f.hasSourceMap) parts.push('map');
  else if (f.sourceMapFetchStatus && !['not_detected', 'disabled'].includes(f.sourceMapFetchStatus)) parts.push(`map ${f.sourceMapFetchStatus}`);
  if (f.isMinified) parts.push('min');
  return parts.join(' · ');
}

function ruleTag(pattern) {
  if (/wp-|cms/i.test(pattern)) return 'CMS';
  if (/analytics|doubleclick|segment|gtag|track/i.test(pattern)) return 'TRACK';
  if (/\.js$/i.test(pattern)) return 'LIB';
  return 'HOST';
}

export function App() {
  const [view, setView] = useState('home');
  const [status, setStatus] = useState({ isCapturing: false, fileCount: 0, mapsCount: 0, secretCount: 0 });
  const [files, setFiles] = useState([]);
  const [settings, setSettings] = useState(null);
  const [activeHost, setActiveHost] = useState('');
  const [toast, setToast] = useState(null);
  const [connState, setConnState] = useState('ok');
  const [latency, setLatency] = useState('');
  const [newRule, setNewRule] = useState('');
  // Decoupled analysis: status of the on-demand backend job + its per-file progress,
  // which drives the captures feed's ingested→analyzing→analyzed lifecycle.
  const [analysis, setAnalysis] = useState({ status: 'idle', counts: null, files: [] });
  const toastTimer = useRef(null);

  // Local settings is the edit source of truth once loaded; polling refreshes only
  // live status/files so it never clobbers an in-progress text edit.
  function adoptSettings(s) { setSettings((prev) => prev || s || {}); }

  async function refresh() {
    const [st, fl] = await Promise.all([api.getStatus(), api.getFiles()]);
    if (st && typeof st.isCapturing === 'boolean') setStatus(st);
    if (st?.settings) adoptSettings(st.settings);
    if (fl?.files) setFiles(fl.files);
  }

  useEffect(() => {
    api.getActiveTabHost().then(setActiveHost);
    refresh();
    // Seed analysis state on open so reopening the popup mid-run resumes the feed.
    api.getAnalysisProgress().then((res) => {
      if (!res?.success || !res.job?.counts) return;
      const c = res.job.counts;
      const inFlight = (c.queued || 0) + (c.analyzing || 0);
      if (inFlight > 0) setAnalysis({ status: 'running', counts: c, files: res.job.files || [] });
      else if ((c.completed || 0) > 0 || (c.failed || 0) > 0) setAnalysis({ status: 'done', counts: c, files: res.job.files || [] });
    });
    const id = setInterval(refresh, 2000);
    // If the worker never answers with settings, fall back so the UI still renders.
    const fb = setTimeout(() => adoptSettings(FALLBACK_SETTINGS), 1000);
    return () => { clearInterval(id); clearTimeout(fb); };
  }, []);

  // While an analysis job is running, poll per-file progress and stop when nothing is
  // left queued/analyzing.
  useEffect(() => {
    if (analysis.status !== 'running') return undefined;
    let alive = true;
    const poll = async () => {
      const res = await api.getAnalysisProgress();
      if (!alive || !res?.success || !res.job) return;
      const counts = res.job.counts || {};
      const inFlight = (counts.queued || 0) + (counts.analyzing || 0);
      // Counts partition total, so nothing queued/analyzing => settled (this also resolves
      // a genuinely empty session, total===0, instead of polling forever).
      const done = inFlight === 0;
      setAnalysis({ status: done ? 'done' : 'running', counts, files: res.job.files || [] });
      if (done) showToast(`Analysis complete · ${counts.completed || 0} analyzed`);
    };
    poll();
    const id = setInterval(poll, 1500);
    return () => { alive = false; clearInterval(id); };
  }, [analysis.status]);

  function showToast(msg) {
    setToast(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 2200);
  }

  function patchSettings(patch) {
    setSettings((prev) => ({ ...(prev || {}), ...patch }));
    api.updateSettings(patch);
  }

  // ---- actions ----
  async function toggleCapture() {
    const next = !status.isCapturing;
    setStatus((s) => ({ ...s, isCapturing: next }));
    await (next ? api.startCapture() : api.stopCapture());
    refresh();
  }

  function toggleSetting(key) { patchSettings({ [key]: !(settings?.[key]) }); }

  // Start a fresh session with an explicit scope. The background rotates the session id,
  // clears prior captures, and tags subsequent uploads so the app-side session shows this
  // scope. We mirror the resolved scope locally so the popup's scope display stays in sync.
  async function startNewSession(rawScope) {
    const rootDomains = String(rawScope || '').split(/[\s,]+/).filter(Boolean);
    const includeSubdomains = settings.includeSubdomains !== false;
    const res = await api.newSession({ rootDomains, includeSubdomains });
    if (res?.success) {
      const resolved = res.scope?.rootDomains || rootDomains;
      setSettings((prev) => ({
        ...(prev || {}),
        domainScopes: resolved,
        useDomainScope: resolved.length > 0,
        includeSubdomains: res.scope?.includeSubdomains !== false
      }));
      showToast('New session started');
    } else {
      showToast('Could not start session');
    }
    refresh();
  }

  async function exportNow() {
    const res = await api.getExportData();
    if (res?.success) {
      api.downloadJson(res.exportData, res.filename || 'js-extraction.json');
      showToast('Export downloaded');
    } else {
      showToast(res?.error ? 'Export failed' : 'Nothing to export');
    }
  }

  // Kick off the decoupled analysis job for the captured session, then let the polling
  // effect track it. Flush happens server-side of the message (background flushes first).
  async function analyzeNow() {
    if (analysis.status === 'starting' || analysis.status === 'running') return;
    setAnalysis((a) => ({ ...a, status: 'starting' }));
    const res = await api.analyzeSession();
    if (res?.success) {
      showToast(res.started ? 'Analysis started' : (res.message || 'Analysis already running'));
      setAnalysis((a) => ({ ...a, status: 'running' }));
    } else {
      setAnalysis((a) => ({ ...a, status: 'idle' }));
      showToast(res?.error === 'timeout' ? 'Analyze timed out' : 'Analyze failed');
    }
  }

  function openWorkspace() {
    const url = settings?.workspaceUrl || 'http://localhost:3000';
    api.openTab(url);
  }

  async function testConnection() {
    setConnState('testing'); setLatency('');
    const res = await api.testConnection();
    if (res?.ok) {
      setConnState('ok'); setLatency(`${res.latencyMs} ms`);
      showToast(`Connection OK · ${res.latencyMs} ms`);
    } else {
      setConnState('fail'); setLatency('timeout');
      showToast('Connection failed');
    }
  }

  function setDefScope(value) {
    const list = value.split(/[\s,]+/).filter(Boolean);
    patchSettings({ domainScopes: list, useDomainScope: list.length > 0 });
  }

  function addRule() {
    const p = newRule.trim();
    if (!p) return;
    const rules = [...(settings?.denyRules || []), { tag: ruleTag(p), pattern: p }];
    patchSettings({ denyRules: rules });
    setNewRule('');
    showToast('Rule added');
  }

  function removeRule(i) {
    patchSettings({ denyRules: (settings?.denyRules || []).filter((_, j) => j !== i) });
  }

  if (!settings) {
    return <div style={{ padding: '40px', textAlign: 'center', color: C.faint, fontSize: '12px' }}>Loading…</div>;
  }

  // ---- view-models ----
  const scopeText = settings.useDomainScope && (settings.domainScopes || []).length
    ? settings.domainScopes.join(', ')
    : (activeHost || 'auto (active tab)');

  // Per-file analysis status (by URL) → the captures feed's lifecycle token.
  const analysisByUrl = new Map((analysis.files || []).map((f) => [f.url, f.status]));
  const LIFECYCLE = { queued: 'ingested', analyzing: 'analyzing', completed: 'analyzed', failed: 'analysis failed', cancelled: 'ingested' };
  const allCaptures = files.map((f, i) => {
    const cls = f.classification || 'app';
    const aStatus = analysisByUrl.get(f.url);
    const lifecycle = aStatus ? (LIFECYCLE[aStatus] || 'ingested') : 'ingested';
    return {
      key: f.url || i,
      name: basename(f.url),
      meta: `${fileMeta(f)} · ${lifecycle}`,
      classification: cls,
      secretCount: f.secretCount || 0,
      isThirdParty: !!f.isThirdParty,
      analyzing: aStatus === 'analyzing',
      dot: { app: C.lime, lib: C.blue, cms: C.purple, tracker: C.dim }[cls] || C.lime,
      _noise: NOISE.has(cls)
    };
  });
  const muteNoise = settings.muteNoise === true;
  const muteThirdParty = settings.outOfScopeMode === 'mute';
  const isHidden = (c) => (muteNoise && c._noise) || (muteThirdParty && c.isThirdParty);
  const mutedCount = allCaptures.filter(isHidden).length;
  const captures = allCaptures.filter((c) => !isHidden(c)).slice().reverse();

  const homeVm = {
    capturing: status.isCapturing,
    connectionLabel: connState === 'fail' ? 'workspace unreachable' : 'connected to workspace',
    host: status.host || activeHost || '—',
    session: (status.sessionId || '').slice(0, 8) || '—',
    scope: scopeText,
    includeSubdomains: settings.includeSubdomains !== false,
    startNewSession,
    startScopeDefault: (settings.domainScopes || []).join(', ') || activeHost || '',
    stats: { js: status.fileCount || 0, maps: status.mapsCount || 0, secrets: status.secretCount || 0 },
    captures, mutedCount,
    analysis, analyzeNow, canAnalyze: (status.fileCount || 0) > 0,
    toggles: [
      { key: 'performAnalysisOnUpload', label: 'Analyze on upload', on: settings.performAnalysisOnUpload === true },
      { key: 'muteNoise', label: 'Mute plugins & trackers', on: muteNoise },
      { key: 'captureAuthContext', label: 'Capture auth context', on: settings.captureAuthContext !== false }
    ],
    openSettings: () => setView('settings'),
    toggleCapture,
    toggleSubdomains: () => patchSettings({ includeSubdomains: !(settings.includeSubdomains !== false) }),
    showAllCaptures: () => patchSettings({ muteNoise: false }),
    toggleSetting,
    exportNow,
    openWorkspace
  };

  const settingsVm = {
    closeSettings: () => setView('home'),
    connState, latency,
    wsUrl: settings.workspaceUrl || '',
    setWsUrl: (v) => patchSettings({ workspaceUrl: v }),
    testConnection,
    defScope: (settings.domainScopes || []).join(', '),
    setDefScope,
    includeSubdomains: settings.includeSubdomains !== false,
    toggleSubdomains: () => patchSettings({ includeSubdomains: !(settings.includeSubdomains !== false) }),
    outOfScopeMode: settings.outOfScopeMode || 'tag',
    setOutOfScopeMode: (m) => patchSettings({ outOfScopeMode: m }),
    maxAssetMb: settings.maxAssetMb || 8,
    setMaxAssetMb: (n) => patchSettings({ maxAssetMb: n }),
    denyDefaultProfile: settings.denyDefaultProfile !== false,
    toggleDefaultProfile: () => patchSettings({ denyDefaultProfile: !(settings.denyDefaultProfile !== false) }),
    denyRules: settings.denyRules || [],
    removeRule, newRule, setNewRule, addRule,
    version: api.extensionVersion()
  };

  return (
    <div class="pp" style={{
      width: '384px', background: C.card, border: `1px solid ${C.lineStrong}`,
      borderRadius: '0', overflow: 'hidden', color: C.text, position: 'relative'
    }}>
      {view === 'home' ? <HomeView vm={homeVm} /> : <SettingsView vm={settingsVm} />}
      <Toast message={toast} />
    </div>
  );
}
