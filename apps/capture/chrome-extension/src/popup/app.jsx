// app.jsx — popup controller. Owns view state, polls the background worker, builds
// the view-models for Home/Settings, and turns UI events into background messages.
import { useEffect, useRef, useState } from 'preact/hooks';
import { C } from './theme.js';
import { Toast } from './components/ui.jsx';
import { HomeView } from './components/HomeView.jsx';
import { SettingsView } from './components/SettingsView.jsx';
import { LoginView } from './components/LoginView.jsx';
import * as api from './api.js';
import { reconcileActiveProject, activeProjectName } from '../../modules/active-engagement.js';
import { deriveDelivery } from '../../modules/delivery-health.js';
import { buildHomeViewModel } from './viewmodels/homeVm.js';
import { buildSettingsViewModel } from './viewmodels/settingsVm.js';
import { useCaptureFlow } from './useCaptureFlow.js';

const IDLE_ANALYSIS = { status: 'idle', counts: null, files: [] };

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
  // When `status` was read, and a 1 s clock: the capture timer ticks between the 2 s status polls.
  const [statusAt, setStatusAt] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const [files, setFiles] = useState([]);
  const [settings, setSettings] = useState(null);
  const [activeHost, setActiveHost] = useState('');
  const [toast, setToast] = useState(null);
  const [connState, setConnState] = useState('ok');
  const [latency, setLatency] = useState('');
  // Force the sign-in view while a (now-expired) token is still held — the re-auth path for a
  // 401/403 upload pause (DEBT D41). Cleared on a successful sign-in. A same-tenant re-login
  // refreshes the token WITHOUT rotating the capture session, so the buffered outbox resumes.
  const [forceLogin, setForceLogin] = useState(false);
  const [newRule, setNewRule] = useState('');
  // Decoupled analysis: status of the on-demand backend job + its per-file progress,
  // which drives the captures feed's ingested→analyzing→analyzed lifecycle.
  const [analysis, setAnalysis] = useState(IDLE_ANALYSIS);
  // D46: findings summary card — null until fetched after analysis completes.
  const [findingsSummary, setFindingsSummary] = useState(null);
  // The engagement list (cached in the worker) and the one picked for a NEW session (null => Solo).
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(null);
  const toastTimer = useRef(null);
  const seededProjectRef = useRef(false);
  // LLM provider config state (threat-model generation)
  const [llmApiKeyDraft, setLlmApiKeyDraft] = useState('');
  const [llmSaving, setLlmSaving] = useState(false);
  const [llmTesting, setLlmTesting] = useState(false);
  const [llmStatusMsg, setLlmStatusMsg] = useState('');
  const [llmStatusOk, setLlmStatusOk] = useState(false);

  // Local settings is the edit source of truth once loaded; polling refreshes only
  // live status/files so it never clobbers an in-progress text edit. `settings` is null until
  // the worker answers with REAL settings — there is no fallback object, so `settings != null`
  // means "signed-in state is known" and the sign-in gate can trust `settings.authToken` (a slow
  // cold worker shows "Loading…", never a wrong gate). Adopt-once so later polls don't clobber edits.
  function adoptSettings(s) { setSettings((prev) => prev || s); }

  // Adopt the session's analysis job (on open, and after an unknown-outcome re-sync) so the feed
  // resumes for a run already in flight or shows a finished one.
  function adoptAnalysisJob(res) {
    if (!res?.ok || !res.job?.counts) return;
    const c = res.job.counts;
    const inFlight = (c.queued || 0) + (c.analyzing || 0);
    if (inFlight > 0) setAnalysis({ status: 'running', counts: c, files: res.job.files || [] });
    else if ((c.completed || 0) > 0 || (c.failed || 0) > 0) setAnalysis({ status: 'done', counts: c, files: res.job.files || [] });
  }

  async function refresh() {
    const [st, fl] = await Promise.all([api.getStatus(), api.getFiles()]);
    if (st && typeof st.isCapturing === 'boolean') { setStatus(st); setStatusAt(Date.now()); }
    if (st?.settings) adoptSettings(st.settings);
    if (fl?.files) setFiles(fl.files);
  }

  useEffect(() => {
    // C7: if the worker never answers, the outcome is unknown — re-read its state, don't fail.
    // An action's outcome is unknown, so re-read everything it could have changed: status/files,
    // the engagement list (a create may have landed) and the analysis job (an analyze may run).
    api.setNoReplyHandler(() => {
      showToast(api.replyError({ unknownOutcome: true }), 'warn');
      refresh();
      api.listProjects().then((r) => { if (r && Array.isArray(r.projects)) setProjects(r.projects); });
      api.getAnalysisProgress().then(adoptAnalysisJob);
    });
    api.getActiveTabHost().then(setActiveHost);
    refresh();
    // Load engagements for the picker (cached in the worker; refreshed on open). Its 401 is the
    // workspace's verdict on the session token, so a rejected token asks for sign-in right away.
    api.listProjects().then((res) => {
      if (res?.authRejected) setForceLogin(true);
      if (res && Array.isArray(res.projects)) setProjects(res.projects);
    });
    const id = setInterval(refresh, 2000);
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => { clearInterval(id); clearInterval(clock); };
  }, []);

  // The analysis feed belongs to ONE capture: reset it when the capture changes (Start, End, a
  // Continue), then adopt that capture's job so reopening the popup mid-run resumes the feed.
  const captureRunId = status.capture?.runId || null;
  useEffect(() => {
    setAnalysis(IDLE_ANALYSIS);
    setFindingsSummary(null);
    if (captureRunId) api.getAnalysisProgress().then(adoptAnalysisJob);
  }, [captureRunId]);

  // Seed the staged engagement pick from the session's REAL binding (worker getStatus) once it's
  // known, so the picker shows the active engagement instead of defaulting to Solo. Adopt-once
  // (a ref guard) so a later user pick / switch isn't clobbered by polling.
  useEffect(() => {
    if (!seededProjectRef.current && status.projectId) {
      setProjectId(status.projectId);
      seededProjectRef.current = true;
    }
  }, [status.projectId]);

  // While an analysis pass runs, poll per-file progress and stop when nothing is left
  // queued/analyzing. After it, while the capture is still open, keep a slow watch: files that
  // arrive later are re-analyzed by the platform on its own (about 10 s after uploads go quiet),
  // and the feed follows that pass too instead of going stale at "Analyzed".
  useEffect(() => {
    const watching = analysis.status === 'done' && !!captureRunId;
    if (analysis.status !== 'running' && !watching) return undefined;
    let alive = true;
    const poll = async () => {
      const res = await api.getAnalysisProgress();
      if (!alive || !res?.ok || !res.job) return;
      const counts = res.job.counts || {};
      const inFlight = (counts.queued || 0) + (counts.analyzing || 0);
      if (watching) {
        if (inFlight > 0) setAnalysis({ status: 'running', updating: true, counts, files: res.job.files || [] });
        return;
      }
      // Counts partition total, so nothing queued/analyzing => settled (this also resolves
      // a genuinely empty session, total===0, instead of polling forever).
      const done = inFlight === 0;
      setAnalysis((a) => ({ status: done ? 'done' : 'running', updating: a.updating, counts, files: res.job.files || [] }));
      if (done) showToast(analysis.updating ? 'Analysis updated with the new files' : `Analysis complete · ${counts.completed || 0} analyzed`);
    };
    poll();
    const id = setInterval(poll, watching ? 5000 : 1500);
    return () => { alive = false; clearInterval(id); };
  }, [analysis.status, captureRunId]);

  // D46: fetch the findings summary once analysis settles to 'done' and a session id is known.
  // Runs whenever analysis.status transitions to 'done'; clears on a new session (status changes
  // back to 'idle'). A failed fetch is silent — the card simply stays hidden.
  useEffect(() => {
    if (analysis.status !== 'done') return;
    const sid = status.capture?.sessionId;
    if (!sid) return;
    api.getSessionFindingsSummary(sid).then((res) => {
      if (res?.ok && res.summary?.status === 'complete') {
        setFindingsSummary(res.summary);
      }
    });
  }, [analysis.status, status.capture?.sessionId]);

  // toast holds { msg, tone }; tone ∈ ok|warn|error drives colour/icon (ui.jsx). Failures linger
  // longer than the 2.2s success flash so an error can actually be read (D42).
  function showToast(msg, tone = 'ok') {
    setToast({ msg, tone });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), tone === 'ok' ? 2200 : 4200);
  }

  function patchSettings(patch) {
    setSettings((prev) => ({ ...(prev || {}), ...patch }));
    api.updateSettings(patch);
  }

  // ---- capture flow (worker-owned capture; scope → Continue/New → Start, Pause/Resume/End) ----
  const flow = useCaptureFlow({
    status, activeHost, projects, projectId, showToast, refresh,
    onSessionExpired: () => setForceLogin(true),
    onCaptureStarted: () => { setAnalysis(IDLE_ANALYSIS); setFindingsSummary(null); },
  });

  // ---- actions ----
  // Central login: authenticate to the workspace, then mirror the identity into local
  // settings so the popup reflects it immediately (adoptSettings won't re-pull from the
  // worker once settings is the edit source of truth). Returns the raw result for the form.
  async function signIn(username, password) {
    const res = await api.login(username, password);
    if (res?.ok) {
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
      setForceLogin(false);   // leave the re-auth view once signed back in (D41)
      // The polled status (authExpired, the uploader's auth pause) would hold the sign-in gate and
      // then Home's "session expired" banner up to the next 2s poll; the worker has already
      // resumed uploads, so clear both now and re-read its real status.
      setStatus((s) => ({ ...s, authExpired: false, uploader: { ...(s.uploader || {}), authPaused: false } }));
      refresh();
      showToast(`Signed in${res.tenant?.name ? ` · ${res.tenant.name}` : ''}`);
    }
    return res || { ok: false };
  }

  async function signOut() {
    // The worker's logout stops capture AND resets to a fresh Standalone session (so nothing
    // keeps stamping the old tenant); here we just clear the local identity + stale picker state.
    await api.logout();
    setSettings((prev) => ({ ...(prev || {}), authToken: '', authUser: '', authTenantName: '' }));
    setProjects([]);
    setProjectId(null);
    showToast('Signed out');
  }

  function toggleSetting(key) { patchSettings({ [key]: !(settings?.[key]) }); }

  async function exportNow() {
    const res = await api.getExportData({ includeContent: settings?.exportIncludeContent === true });
    if (res?.ok) {
      api.downloadJson(res.exportData, res.filename || 'js-extraction.json');
      showToast(settings?.exportIncludeContent ? 'Export downloaded · with code' : 'Export downloaded');
    } else if (!res?.unknownOutcome) {
      showToast(res?.error ? 'Export failed' : 'Nothing to export', res?.error ? 'error' : 'warn');
    }
  }

  // LLM provider config — save encrypts the key server-side, never stores it locally.
  async function saveLlmConfig() {
    if (!llmApiKeyDraft.trim()) { setLlmStatusMsg('Paste an API key first'); setLlmStatusOk(false); return; }
    setLlmSaving(true); setLlmStatusMsg(''); setLlmStatusOk(false);
    const res = await api.saveLlmConfig({
      provider: settings?.llmProvider || 'anthropic',
      model: settings?.llmModel || 'claude-opus-4-7',
      apiKey: llmApiKeyDraft.trim(),
    });
    setLlmSaving(false);
    if (res?.ok) {
      patchSettings({ llmConfigured: true });
      setLlmApiKeyDraft('');
      setLlmStatusMsg('Saved · key encrypted on server');
      setLlmStatusOk(true);
    } else {
      setLlmStatusMsg(api.replyError(res, 'Save failed'));
      setLlmStatusOk(false);
    }
  }

  async function testLlmConfig() {
    setLlmTesting(true); setLlmStatusMsg(''); setLlmStatusOk(false);
    const res = await api.testLlmConfig();
    setLlmTesting(false);
    if (res?.ok) {
      setLlmStatusMsg(`Connected · ${res.provider} / ${res.model}`);
      setLlmStatusOk(true);
    } else {
      setLlmStatusMsg(api.replyError(res, 'Test failed'));
      setLlmStatusOk(false);
    }
  }

  // Reachable "clear captures" (D46): the worker's clearFiles handler existed but was wired to no
  // UI. Drops the captured set for the current session and resets the analysis feed.
  async function clearCaptures() {
    await api.clearFiles();
    setAnalysis(IDLE_ANALYSIS);
    showToast('Captures cleared');
    refresh();
  }

  // Analyze what has arrived so far. It never ends the capture: files that land later join the
  // same capture and the platform re-analyzes them on its own. The polling effect tracks the pass.
  async function analyzeNow() {
    if (analysis.status === 'starting' || analysis.status === 'running') return;
    setAnalysis((a) => ({ ...a, status: 'starting' }));
    const res = await api.analyzeSession();
    if (res?.ok) {
      showToast(res.upToDate ? 'Already up to date' : res.started ? 'Analysis started' : (res.message || 'Analysis already running'));
      setAnalysis((a) => ({ ...a, status: 'running', updating: false }));
      return;
    }
    setAnalysis((a) => ({ ...a, status: 'idle' }));
    if (res?.unknownOutcome) return;  // the no-reply handler re-reads the job and resumes the feed
    // During a capture only an expired sign-in refuses Analyze (its uploads will never drain
    // without re-auth); late files are picked up by the platform's own re-analysis.
    if (res?.reason === 'session_expired') {
      showToast('Session expired — sign in to finish uploading', 'error');
    } else if (res?.reason === 'pending_uploads') {
      showToast(`${res.pending || 'Some'} captures still uploading — try again shortly`, 'warn');
    } else {
      showToast(res?.error === 'timeout' ? 'Analyze timed out' : 'Analyze failed', 'error');
    }
  }

  function openWorkspace() {
    const base = settings?.workspaceUrl || 'http://localhost:8000';
    // Deep-link the current capture session so the workspace opens INTO it instead of
    // defaulting to whatever run is newest. This id is the EXTENSION's session id (the
    // backend session's external_id, NOT its primary key); the workspace resolves
    // ?capture=<external_id> to that session's latest run. Bare URL when none is known yet.
    const sid = status.capture?.sessionId || status.lastEnded?.sessionId || status?.sessionId;
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
    } else if (res?.unknownOutcome) {
      setConnState('fail'); setLatency('');  // the no-reply handler already said why
    } else {
      setConnState('fail'); setLatency('timeout');
      showToast('Connection failed', 'error');
    }
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
    version: api.extensionVersion(),
    expired: !!status.authExpired || forceLogin,
    // A capture running when the token expired must stay stoppable from here: the sign-in screen
    // has no other controls, and the workspace may be unreachable for a sign-in.
    // (Pause, not End: End needs the workspace to finish, and a paused capture resumes later.)
    capture: status.isCapturing
      ? { held: status.uploader?.pendingQueueLength || 0, stop: async () => { await api.capturePause(); refresh(); } }
      : null
  };
  // Expired (known locally, polled) or rejected (the workspace's 401) -> sign in before capturing.
  if (!settings.authToken || forceLogin || status.authExpired) {
    return (
      <div class="pp" style={{
        width: '384px', background: C.card, border: `1px solid ${C.lineStrong}`,
        borderRadius: '0', overflow: 'hidden', color: C.text, position: 'relative'
      }}>
        <LoginView vm={loginVm} />
        <Toast toast={toast} />
      </div>
    );
  }

  // ---- view-models ----
  // Per-file analysis status (by URL) → the captures feed's lifecycle token.
  // The platform keeps a re-captured url's other versions as `url#sha256=…`; the popup lists
  // only the newest copy per url, so key by the bare url (a later version's status wins).
  const analysisByUrl = new Map((analysis.files || []).map((f) => [f.url.split('#sha256=')[0], f.status]));
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

  // Real delivery health from the worker's uploader + processing stats — the D42 fix for a popup
  // that hid "captured nothing" / "never uploaded". A manual connection test still overrides
  // (testing/fail); otherwise the health comes from actual upload/skip/failure outcomes.
  const delivery = deriveDelivery(status);
  const deliveryVm = { ...delivery, lastFile: delivery.lastUrl ? basename(delivery.lastUrl) : '' };
  const health = connState === 'testing' ? 'testing' : connState === 'fail' ? 'fail' : delivery.health;
  const connectionLabel =
    health === 'testing' ? 'testing…'
      : health === 'fail'
        ? (delivery.paired === false && !delivery.lastReason ? 'not paired · check sign-in' : 'delivery failing')
        : health === 'warn' ? `${delivery.skipped} skipped`
          : delivery.paired === true ? 'connected · delivering' : 'connected to workspace';

  // Pre-bind actions that need closure access before passing to viewmodel factories.
  const selectProject = (id) => { setProjectId(id || null); };
  const createProject = async (name, rootDomains) => {
    const cleanName = String(name || '').trim();
    if (!cleanName) { showToast('Project name required', 'warn'); return { ok: false }; }
    const res = await api.createProject({
      name: cleanName,
      defaults: { scope: { rootDomains: String(rootDomains || '').split(/[\s,]+/).filter(Boolean) } }
    });
    if (res?.ok && res.project) {
      setProjects((prev) => [res.project, ...prev]);
      setProjectId(res.project.id);
      showToast(`Project created · ${res.project.name}`);
    } else {
      showToast(res?.unknownOutcome ? api.replyError(res) : (res?.error ? `Create failed: ${res.error}` : 'Could not create project'), res?.unknownOutcome ? 'warn' : 'error');
    }
    return res;
  };

  // The timer ticks locally between status polls; it stops while paused or ending.
  const capture = status.capture || null;
  const elapsedMs = capture
    ? (capture.elapsedMs || 0) + (flow.phase === 'live' ? Math.max(0, now - statusAt) : 0)
    : 0;

  const homeVm = buildHomeViewModel({
    status, elapsedMs, delivery: deliveryVm, health, connectionLabel,
    flow,
    captures, mutedCount,
    analysis, canAnalyze: (status.fileCount || 0) > 0,
    findingsSummary,
    projects, projectId,
    activeProjectName: activeProjectName(activeProjectId, projects),
    settings,
    reauth: () => setForceLogin(true),
    openSettings: () => setView('settings'),
    showAllCaptures: () => patchSettings({ muteNoise: false }),
    toggleSetting, exportNow, openWorkspace, analyzeNow,
    selectProject, createProject,
  });

  const settingsVm = buildSettingsViewModel({
    closeSettings: () => setView('home'),
    connState, latency, testConnection,
    settings, status,
    signOut, patchSettings,
    newRule, setNewRule, addRule, removeRule,
    clearCaptures,
    saveLlmConfig, testLlmConfig,
    llmApiKeyDraft, setLlmApiKeyDraft,
    llmSaving, llmTesting, llmStatusMsg, llmStatusOk,
    version: api.extensionVersion(),
  });

  return (
    <div class="pp" style={{
      width: '384px', background: C.card, border: `1px solid ${C.lineStrong}`,
      borderRadius: '0', overflow: 'hidden', color: C.text, position: 'relative'
    }}>
      {view === 'home' ? <HomeView vm={homeVm} /> : <SettingsView vm={settingsVm} />}
      <Toast toast={toast} />
    </div>
  );
}
