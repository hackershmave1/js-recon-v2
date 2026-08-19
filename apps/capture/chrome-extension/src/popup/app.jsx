// app.jsx — popup controller. Owns view state, polls the background worker, builds
// the view-models for Home/Settings, and turns UI events into background messages.
import { useEffect, useRef, useState } from 'preact/hooks';
import { C } from './theme.js';
import { Toast } from './components/ui.jsx';
import { HomeView } from './components/HomeView.jsx';
import { SettingsView } from './components/SettingsView.jsx';
import { LoginView } from './components/LoginView.jsx';
import * as api from './api.js';
import { resolveEffectiveConfig, splitEffective, configFromSettings } from '../../modules/project-config.js';
import { reconcileActiveProject, activeProjectName } from '../../modules/active-engagement.js';

const NOISE = new Set(['lib', 'cms', 'tracker']);

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
  // Project-scoped capture: cached engagement list, the chosen project (null => Standalone),
  // and the sparse per-session override doc the New-Session editor builds.
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(null);
  const [overrides, setOverrides] = useState({});
  const toastTimer = useRef(null);
  const seededProjectRef = useRef(false);

  // Local settings is the edit source of truth once loaded; polling refreshes only
  // live status/files so it never clobbers an in-progress text edit. `settings` is null until
  // the worker answers with REAL settings — there is no fallback object, so `settings != null`
  // means "signed-in state is known" and the sign-in gate can trust `settings.authToken` (a slow
  // cold worker shows "Loading…", never a wrong gate). Adopt-once so later polls don't clobber edits.
  function adoptSettings(s) { setSettings((prev) => prev || s); }

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
    // Load engagements for the picker (cached in the worker; refreshed on open).
    api.listProjects().then((res) => { if (res && Array.isArray(res.projects)) setProjects(res.projects); });
    const id = setInterval(refresh, 2000);
    return () => { clearInterval(id); };
  }, []);

  // Seed the staged engagement pick from the session's REAL binding (worker getStatus) once it's
  // known, so the picker shows the active engagement instead of defaulting to Solo. Adopt-once
  // (a ref guard) so a later user pick / switch isn't clobbered by polling.
  useEffect(() => {
    if (!seededProjectRef.current && status.projectId) {
      setProjectId(status.projectId);
      seededProjectRef.current = true;
    }
  }, [status.projectId]);

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
    // Enforce login before capturing: an unauthenticated upload is rejected by a
    // fail-closed backend (or leaks into the shared tenant), so send the operator to
    // sign in first instead of piling doomed captures into the outbox.
    if (next && !settings?.authToken) {
      showToast('Sign in to capture');
      setView('settings');
      return;
    }
    setStatus((s) => ({ ...s, isCapturing: next }));
    await (next ? api.startCapture() : api.stopCapture());
    refresh();
  }

  // Central login: authenticate to the workspace, then mirror the identity into local
  // settings so the popup reflects it immediately (adoptSettings won't re-pull from the
  // worker once settings is the edit source of truth). Returns the raw result for the form.
  async function signIn(username, password) {
    const res = await api.login(username, password);
    if (res?.success) {
      setSettings((prev) => ({
        ...(prev || {}),
        authToken: 'session', // truthy marker; the real token stays in the worker
        authUser: res.user || username,
        authTenantName: (res.tenant && res.tenant.name) || ''
      }));
      // Engagements are tenant-scoped: re-fetch under the new identity (a prior tenant's list
      // would be wrong). Land on Home so the funnel is sign-in -> pick engagement -> capture.
      setProjectId(null);
      api.listProjects().then((r) => { if (r && Array.isArray(r.projects)) setProjects(r.projects); });
      setView('home');
      showToast(`Signed in${res.tenant?.name ? ` · ${res.tenant.name}` : ''}`);
    }
    return res || { success: false };
  }

  async function signOut() {
    // The worker's logout stops capture AND resets to a fresh Standalone session (so nothing
    // keeps stamping the old tenant); here we just clear the local identity + stale picker state.
    await api.logout();
    setSettings((prev) => ({ ...(prev || {}), authToken: '', authUser: '', authTenantName: '' }));
    setProjects([]);
    setProjectId(null);
    setOverrides({});
    showToast('Signed out');
  }

  function toggleSetting(key) { patchSettings({ [key]: !(settings?.[key]) }); }

  // Start a fresh session with an explicit scope. The background rotates the session id,
  // clears prior captures, and tags subsequent uploads so the app-side session shows this
  // scope. We mirror the resolved scope locally so the popup's scope display stays in sync.
  async function startNewSession(rawScope) {
    // The popup is the resolving client: resolve the effective config from the project
    // defaults (or the current global settings, for Standalone) + the sparse overrides, and
    // send that snapshot. The background applies it to the capture gate and stamps it onto
    // uploads (spec §7/§8.5); it does not re-resolve.
    const selected = projectId ? projects.find((p) => p.id === projectId) : null;
    // Guard: a staged project id that isn't in the loaded list yet (engagements still loading, or
    // a stale / other-tenant id) would silently resolve as Standalone and bind the session to the
    // WRONG engagement. Don't commit until the list has loaded and the id resolves.
    if (projectId && !selected) { showToast('Loading engagements…'); return; }
    let defaults = selected ? selected.defaults : configFromSettings(settings || {});
    let ovr = overrides;
    if (!selected) {
      // Standalone: bake the ad-hoc scope into the resolved DEFAULTS (not as an override) so
      // override_keys stays [] (spec §4) while capture still runs under the typed scope.
      const rootDomains = String(rawScope || '').split(/[\s,]+/).filter(Boolean);
      defaults = { ...defaults, scope: { ...(defaults.scope || {}), rootDomains, includeSubdomains: settings?.includeSubdomains !== false } };
      ovr = {};
    }

    const { effective, overrideKeys } = resolveEffectiveConfig(defaults, ovr);
    const { scope, captureConfig } = splitEffective(effective);

    const res = await api.newSession({ projectId: projectId || null, scope, captureConfig, overrideKeys });
    if (res?.success) {
      // Mirror the applied scope locally so the read-only SCOPE bar updates.
      setSettings((prev) => ({
        ...(prev || {}),
        domainScopes: scope.rootDomains,
        useDomainScope: scope.rootDomains.length > 0,
        includeSubdomains: scope.includeSubdomains
      }));
      setOverrides({});
      showToast(selected ? `New session · ${selected.name}` : 'New standalone session');
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
    const base = settings?.workspaceUrl || 'http://localhost:8000';
    // Deep-link the current capture session so the workspace opens INTO it instead of
    // defaulting to whatever run is newest. This id is the EXTENSION's session id (the
    // backend session's external_id, NOT its primary key); the workspace resolves
    // ?capture=<external_id> to that session's latest run. Bare URL when none is known yet.
    const sid = status?.sessionId;
    const url = sid
      ? `${base}${base.includes('?') ? '&' : '?'}capture=${encodeURIComponent(sid)}`
      : base;
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

  // ---- sign-in gate ----
  // Signed-out users get the dedicated login screen, never the capture UI. Gated on the REAL,
  // worker-provided settings.authToken (there is no fallback settings object), so a slow cold
  // worker shows "Loading…" above rather than misfiring the gate for a signed-in user.
  const loginVm = {
    wsUrl: settings.workspaceUrl || '',
    setWsUrl: (v) => patchSettings({ workspaceUrl: v }),
    connState, latency, testConnection,
    signIn,
    version: api.extensionVersion()
  };
  if (!settings.authToken) {
    return (
      <div class="pp" style={{
        width: '384px', background: C.card, border: `1px solid ${C.lineStrong}`,
        borderRadius: '0', overflow: 'hidden', color: C.text, position: 'relative'
      }}>
        <LoginView vm={loginVm} />
        <Toast message={toast} />
      </div>
    );
  }

  // ---- view-models ----
  // Honest, tri-state scope label. It reflects what the CAPTURE GATE (isInScope) will
  // actually do — never the active tab, which is what made the scope look like it
  // "followed" whatever tab you were on.
  const wideOpen = settings.captureEverything === true;
  const hasScope = settings.useDomainScope && (settings.domainScopes || []).length > 0;
  const scopeMode = wideOpen ? 'open' : hasScope ? 'scoped' : 'none';
  const scopeText = wideOpen
    ? 'WIDE OPEN · all tabs'
    : hasScope
      ? settings.domainScopes.join(', ')
      : 'no scope · capturing nothing';

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

  // The engagement the current session is ACTUALLY bound to (worker getStatus), reconciled
  // against the fetched list so a deleted / different-tenant id renders as Solo, not a phantom.
  const activeProjectId = reconcileActiveProject(status.projectId, projects);

  const homeVm = {
    capturing: status.isCapturing,
    connectionLabel: connState === 'fail' ? 'workspace unreachable' : 'connected to workspace',
    host: status.host || activeHost || '—',
    session: (status.sessionId || '').slice(0, 8) || '—',
    scope: scopeText,
    scopeMode,
    includeSubdomains: settings.includeSubdomains !== false,
    startNewSession,
    startScopeDefault: (settings.domainScopes || []).join(', ') || activeHost || '',
    // Project-scoped capture: engagement picker + override editor state.
    projects,
    projectId,
    // Active engagement = the current session's real binding (surfaced always, on Home).
    activeProjectId,
    activeProjectName: activeProjectName(activeProjectId, projects),
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
      const cleanName = String(name || '').trim();
      if (!cleanName) { showToast('Project name required'); return { success: false }; }
      const res = await api.createProject({
        name: cleanName,
        defaults: { scope: { rootDomains: String(rootDomains || '').split(/[\s,]+/).filter(Boolean) } }
      });
      if (res?.success && res.project) {
        setProjects((prev) => [res.project, ...prev]);
        setProjectId(res.project.id);
        setOverrides({});
        showToast(`Project created · ${res.project.name}`);
      } else {
        showToast(res?.error ? `Create failed: ${res.error}` : 'Could not create project');
      }
      return res;
    },
    stats: { js: status.fileCount || 0, maps: status.mapsCount || 0, secrets: status.secretCount || 0 },
    captures, mutedCount,
    analysis, analyzeNow, canAnalyze: (status.fileCount || 0) > 0,
    toggles: [
      { key: 'captureEverything', label: 'Capture every tab (ignore scope)', on: settings.captureEverything === true },
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
    // Signed-in account summary (sign-in itself is the gate, LoginView). `paired` is the last
    // save-files ack (via the uploader stats) so the Account block can confirm the login token
    // actually routed instead of failing silently; undefined until the first upload.
    authUser: settings.authUser || '',
    authTenantName: settings.authTenantName || '',
    signOut,
    paired: status?.uploader?.paired,
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
