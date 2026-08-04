// app.jsx — workspace controller: fetches API data, selects the active target,
// builds Overview/Sessions view-models, and routes between views. Phase 1 scope:
// shell + Overview + Sessions; Findings/Sources are stubs pending later phases.
import { useEffect, useMemo, useState } from 'preact/hooks';
import { C } from './theme.js';
import * as api from './api.js';
import {
  relTime, hostOf, findingsFromAnalysis, topFindings, surfaceFrom, coverageBars, missReasons
} from './transforms.js';
import { Sidebar } from './views/Sidebar.jsx';
import { Topbar } from './views/Topbar.jsx';
import { Overview } from './views/Overview.jsx';
import { Sessions } from './views/Sessions.jsx';
import { Projects } from './views/Projects.jsx';
import { Findings } from './views/Findings.jsx';
import { Sources } from './views/Sources.jsx';
import { ActivityDrawer } from './views/ActivityDrawer.jsx';
import { NewReconModal } from './views/NewReconModal.jsx';
import { SearchPalette } from './views/SearchPalette.jsx';
import { ExportModal } from './views/ExportModal.jsx';
import { ScopeModal } from './views/ScopeModal.jsx';
import { METRIC_ICONS as ICONS } from './icons.jsx';
import { RUNNING, latestJobForSession, buildSessionsVm, scopeLabelOf } from './viewmodels.js';

export function App() {
  // Land on Projects by default; an extension ?session= deep-link opens straight into that
  // session's overview instead, so "Open Workspace" lands in the capture, not the picker.
  const [view, setView] = useState(() => {
    try { return new URLSearchParams(window.location.search).has('session') ? 'overview' : 'projects'; }
    catch (e) { return 'projects'; }
  });
  const [projects, setProjects] = useState([]);
  // Active engagement filter for the Sessions list: a project id scopes it; null = Standalone (all).
  const [activeProjectId, setActiveProjectId] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [jobs, setJobs] = useState([]);
  // Seed the active session from a ?session=<id> deep-link. The Chrome extension's
  // "Open Workspace" passes its capture session id this way, so we open INTO that
  // session rather than defaulting to the newest. null → newest, exactly as before.
  const [selectedId, setSelectedId] = useState(() => {
    try {
      // Ignore a missing/garbage deep-link value (e.g. ?session=undefined from an
      // extension whose status hadn't loaded yet) so we fall through to the newest
      // session instead of pinning a bogus id that matches nothing.
      const raw = (new URLSearchParams(window.location.search).get('session') || '').trim();
      return raw && raw !== 'undefined' && raw !== 'null' ? raw : null;
    } catch (e) { return null; }
  });
  const [analysis, setAnalysis] = useState(null);
  const [assetGraph, setAssetGraph] = useState(null);
  const [statusMap, setStatusMap] = useState({});
  const [toast, setToast] = useState(null);
  const [sourceTarget, setSourceTarget] = useState(null);
  const [findingTarget, setFindingTarget] = useState(null);
  // Phase 4 overlays: Activity drawer, New Recon modal, ⌘K search, Export.
  const [activityOpen, setActivityOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [newReconOpen, setNewReconOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [scopeTarget, setScopeTarget] = useState(null);
  const [scopeBusy, setScopeBusy] = useState(false);
  const [reconBusy, setReconBusy] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);

  // initial + periodic load of session/stats/job lists. reloadNonce forces an
  // immediate refresh after starting/stopping a recon job (don't wait for the tick).
  useEffect(() => {
    let alive = true;
    // Sessions + jobs drive the shell. The global /api/stats rollup is deliberately
    // NOT fetched here: it scans every analysis across the whole DB and pins the
    // single API worker for tens of seconds, which would stall the workspace on
    // boot. The workspace is session-centric, so per-session data is the source.
    const load = async () => {
      const [sess, jb, projs] = await Promise.all([api.getSessions(), api.getReconJobs(), api.getProjects()]);
      if (!alive) return;
      const list = Array.isArray(sess) ? sess : (sess?.sessions || []);
      setSessions(list);
      setJobs(Array.isArray(jb) ? jb : (jb?.jobs || []));
      if (Array.isArray(projs)) setProjects(projs);
      setSelectedId((cur) => cur || (list[0] && list[0].id) || null);
    };
    load();
    const id = setInterval(load, 5000);
    return () => { alive = false; clearInterval(id); };
  }, [reloadNonce]);

  // Consume the ?session deep-link once. We've already captured it into selectedId
  // above; strip it from the URL so a later manual refresh doesn't re-pin a stale
  // session if the operator has since navigated to a different one.
  useEffect(() => {
    try {
      if (new URLSearchParams(window.location.search).has('session')) {
        window.history.replaceState(null, '', window.location.pathname);
      }
    } catch (e) { /* ignore */ }
  }, []);

  // ⌘K opens search; Escape closes whichever overlay is topmost. When the Sources
  // code-viewer find bar is open it owns Escape (to close itself), so we defer to it
  // rather than also tearing down background overlays on the same keypress.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setSearchOpen(true); }
      else if (e.key === 'Escape') {
        if (document.querySelector('input[data-find-in-file]')) return;
        setSearchOpen(false); setNewReconOpen(false); setExportOpen(false); setActivityOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // per-target detail load
  useEffect(() => {
    if (!selectedId) return;
    let alive = true;
    (async () => {
      const [an, ag, st] = await Promise.all([
        api.getComprehensiveAnalysis(selectedId),
        api.getAssetGraph(selectedId),
        api.getFindingStatuses(selectedId)
      ]);
      if (!alive) return;
      setAnalysis(an);
      setAssetGraph(ag);
      setStatusMap((st && st.statuses) || {});
    })();
    return () => { alive = false; };
  }, [selectedId]);

  const flash = (msg) => { setToast(msg); setTimeout(() => setToast(null), 2400); };

  const selected = sessions.find((s) => s.id === selectedId) || sessions[0] || null;
  const targetHost = selected ? hostOf(selected) : '';
  const findings = useMemo(() => findingsFromAnalysis(analysis?.analysis || analysis, targetHost), [analysis, targetHost]);

  // Optimistic triage: reflect the new status immediately, then persist. On failure
  // we roll back to the prior value so the UI never shows a status the server
  // rejected. Captured against the session that was active at click time.
  const onTriage = async (finding, status) => {
    if (!selectedId) return;
    const sessionAtClick = selectedId;
    const prev = statusMap[finding.fingerprint] || 'new';
    setStatusMap((m) => ({ ...m, [finding.fingerprint]: status }));
    const saved = await api.setFindingStatus(sessionAtClick, finding.fingerprint, status);
    if (!saved && selectedId === sessionAtClick) {
      setStatusMap((m) => ({ ...m, [finding.fingerprint]: prev }));
      flash('Could not save triage status — reverted');
    }
  };
  const onCopy = (value) => {
    try { navigator.clipboard.writeText(value || ''); flash('Copied to clipboard'); }
    catch (e) { flash('Copy failed'); }
  };
  // "Open in Sources →": carry the finding's bundle + path + line to the Sources
  // view, which expands that bundle's reconstructed tree and focuses the line.
  const onOpenSource = (finding) => {
    if (!finding) return;
    if (!finding.fileId) { flash('No source file recorded for this finding'); return; }
    setSourceTarget({ fileId: finding.fileId, file: finding.file, line: finding.line });
    setView('sources');
  };
  // Start a recon crawl. The modal supplies a backend-shaped payload; on success we
  // jump to the new session, surface progress in the Activity drawer, and force an
  // immediate list refresh so the job appears without waiting for the poll tick.
  const onStartRecon = async (payload) => {
    setReconBusy(true);
    const res = await api.startReconJob(payload);
    setReconBusy(false);
    if (!res || !res.ok) { flash((res && res.error) || 'Could not start recon'); return; }
    setNewReconOpen(false);
    if (res.sessionId) setSelectedId(res.sessionId);
    setActivityOpen(true);
    setReloadNonce((n) => n + 1);
    flash('Recon started — tracking in Activity');
  };
  const onStopJob = async (jobId) => {
    const res = await api.stopReconJob(jobId);
    setReloadNonce((n) => n + 1);
    // null = request failed (network/non-2xx); stopRequested:false = job already terminal.
    if (!res) flash('Could not reach the API to stop the job');
    else flash(res.stopRequested ? 'Stop requested' : 'Job already finished');
  };
  // Stop a session's in-flight job from the Sessions grid (same endpoint as the
  // Activity drawer, surfaced where the session lives so it's discoverable).
  const onStopSession = async (jobId) => {
    if (!jobId) return;
    const res = await api.stopReconJob(jobId);
    setReloadNonce((n) => n + 1);
    if (!res) flash('Could not reach the API to stop the job');
    else flash(res.stopRequested ? 'Stop requested' : 'Job already finished');
  };
  // Resume / Continue Crawl: re-run the latest job's target + options on this session.
  const onResumeSession = async (payload) => {
    if (!payload) return;
    const res = await api.startReconJob(payload);
    if (!res || !res.ok) { flash((res && res.error) || 'Could not resume crawl'); return; }
    setSelectedId(payload.sessionId);
    setActivityOpen(true);
    setReloadNonce((n) => n + 1);
    flash('Crawl resumed — tracking in Activity');
  };
  // Rename: optimistic (reflect immediately, roll back on failure). The displayed
  // label is derived from session.name, so updating it locally re-renders the card.
  const onRenameSession = async (sessionId, name) => {
    const trimmed = (name || '').trim();
    if (!trimmed) return;
    const prev = sessions;
    setSessions((list) => list.map((s) => (s.id === sessionId ? { ...s, name: trimmed } : s)));
    const res = await api.renameSession(sessionId, trimmed);
    if (!res) { setSessions(prev); flash('Could not rename session — reverted'); }
    else flash('Session renamed');
  };
  // Delete: optimistic removal; fully restore (list + selection) if the API rejects it.
  const onDeleteSession = async (sessionId) => {
    const prev = sessions;
    const prevSelected = selectedId;
    setSessions((list) => list.filter((s) => s.id !== sessionId));
    if (selectedId === sessionId) setSelectedId(null);
    const res = await api.deleteSession(sessionId);
    if (!res) { setSessions(prev); setSelectedId(prevSelected); flash('Could not delete session — restored'); }
    else { setReloadNonce((n) => n + 1); flash('Session deleted'); }
  };
  // Scope edit: persist root domains + include-subdomains, then mirror the server's
  // normalized result back into the session list so cards/Overview update immediately.
  const onSaveScope = async (rootDomains, includeSubdomains) => {
    if (!scopeTarget) return;
    setScopeBusy(true);
    const res = await api.setSessionScope(scopeTarget.id, rootDomains, includeSubdomains);
    setScopeBusy(false);
    if (!res) { flash('Could not save scope'); return; }
    setSessions((list) => list.map((s) => (s.id === scopeTarget.id
      ? { ...s, rootDomains: res.rootDomains, includeSubdomains: res.includeSubdomains } : s)));
    setScopeTarget(null);
    flash('Scope updated');
  };
  // ---- projects (engagements) ----
  const onOpenProject = (p) => { setActiveProjectId(p.id); setView('sessions'); };
  const onStandalone = () => { setActiveProjectId(null); setView('sessions'); };
  const onCreateProject = async (name, rootDomains) => {
    const res = await api.createProject(name, rootDomains);
    if (!res || !res.ok) { flash((res && res.error) || 'Could not create project'); return; }
    setProjects((list) => [res.project, ...list]);
    flash(`Project created · ${res.project.name}`);
  };
  const onRenameProject = async (projectId, name) => {
    const prev = projects;
    setProjects((list) => list.map((p) => (p.id === projectId ? { ...p, name } : p)));
    const res = await api.renameProject(projectId, name);
    if (!res) { setProjects(prev); flash('Could not rename project — reverted'); }
    else flash('Project renamed');
  };
  const onRescopeProject = async (projectId, rootDomains) => {
    const prev = projects;
    setProjects((list) => list.map((p) => (p.id === projectId
      ? { ...p, defaults: { ...(p.defaults || {}), scope: { ...((p.defaults || {}).scope || {}), rootDomains } } } : p)));
    const res = await api.setProjectScope(projectId, rootDomains);
    if (!res) { setProjects(prev); flash('Could not update scope — reverted'); }
    else { setProjects((list) => list.map((p) => (p.id === projectId ? res : p))); flash('Project scope updated'); }
  };
  const onDeleteProject = async (projectId) => {
    const prev = projects;
    setProjects((list) => list.filter((p) => p.id !== projectId));
    if (activeProjectId === projectId) setActiveProjectId(null);
    const res = await api.deleteProject(projectId);
    if (!res) { setProjects(prev); flash('Could not delete project — restored'); }
    // Sessions that referenced it are now project-less server-side (ON DELETE SET NULL) —
    // refresh so their cards reflect it.
    else { setReloadNonce((n) => n + 1); flash('Project deleted'); }
  };
  // Search result → open the finding where it lives: Sources if it has a
  // reconstructed file, otherwise the Findings drawer (via findingTarget).
  const onSearchPick = (finding) => {
    setSearchOpen(false);
    if (finding.fileId) { onOpenSource(finding); return; }
    setView('findings');
    setFindingTarget(finding.fingerprint);
  };
  const runningJobs = (jobs || []).filter((j) => RUNNING.has((j.status || '').toLowerCase())).length;
  const criticalCount = findings.filter((f) => f.severity === 'critical' || f.severity === 'high').length;

  // ---- sessions view-model (pure builder in viewmodels.js) ----
  // Scope the Sessions list to the active engagement (null => Standalone shows every session).
  const activeProject = activeProjectId ? projects.find((p) => p.id === activeProjectId) : null;
  const visibleSessions = activeProjectId ? sessions.filter((s) => s.projectId === activeProjectId) : sessions;
  const sessionsVm = buildSessionsVm(visibleSessions, jobs, selectedId);

  // ---- overview view-model ----
  const job = selected ? latestJobForSession(jobs, selected.id) : null;
  const analyzed = job?.coverage?.analyzed_js ?? 0;
  const mapsCount = job?.coverage?.map_detected ?? (assetGraph?.graph?.stats?.maps ?? 0);
  const secCount = findings.filter((f) => f.kind === 'secret').length;
  const epCount = findings.filter((f) => f.kind === 'endpoint').length;
  const critCount = findings.filter((f) => f.severity === 'critical').length;
  const covPct = Math.round(job?.coverage?.rates?.analysisPct ?? 0);

  const overviewVm = {
    sessionId: selected ? String(selected.id).slice(0, 8) : '—',
    lastRun: job ? relTime(job.finishedAt || job.finished_at || job.createdAt) : '—',
    host: selected ? hostOf(selected) : 'No session selected',
    scope: scopeLabelOf(selected?.rootDomains, selected?.includeSubdomains),
    hasScope: !!(selected?.rootDomains || []).length,
    onEditScope: () => { if (selected) setScopeTarget({ ...selected, host: hostOf(selected) }); },
    metrics: [
      { label: 'Files discovered', value: String(selected?.fileCount ?? 0), icon: ICONS.files, iconbg: 'rgba(108,168,255,0.12)', iconc: C.blue,
        sub: <span><span style={{ color: C.lime }}>{analyzed}</span> analyzed · {mapsCount} maps</span>, go: () => setView('sources') },
      { label: 'Endpoints', value: String(epCount), icon: ICONS.endpoints, iconbg: 'rgba(124,140,255,0.12)', iconc: C.indigo,
        sub: 'API + GraphQL routes', go: () => setView('findings') },
      { label: 'Secrets', value: String(secCount), icon: ICONS.secrets, iconbg: 'rgba(255,107,138,0.12)', iconc: C.pink,
        sub: <span><span style={{ color: C.red }}>{critCount} critical</span> · needs triage</span>, go: () => setView('findings') },
      { label: 'Coverage', value: `${covPct}%`, icon: ICONS.coverage, iconbg: 'rgba(205,235,69,0.12)', iconc: C.lime,
        sub: 'analyzed / fetched', go: () => setView('overview') }
    ],
    surface: surfaceFrom(assetGraph, selected ? hostOf(selected) : ''),
    coverage: coverageBars(job?.coverage),
    missReasons: missReasons(job?.coverage),
    topFindings: topFindings(findings),
    onNewRecon: () => setNewReconOpen(true),
    goFindings: () => setView('findings'),
    openFinding: () => setView('findings')
  };

  const target = {
    host: selected ? hostOf(selected) : 'No target',
    sub: selected ? `${selected.fileCount ?? 0} files · ${overviewVm.metrics[3].value} analyzed` : 'select a session'
  };

  return (
    <div style={{ height: '100vh', display: 'flex', background: C.app, color: C.text, overflow: 'hidden' }}>
      <Sidebar
        view={view} onNav={setView} criticalCount={criticalCount}
        target={target} runningJobs={runningJobs}
        onActivity={() => setActivityOpen(true)}
        onTarget={() => setView('sessions')}
      />
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <Topbar
          onSearch={() => setSearchOpen(true)}
          onExport={() => setExportOpen(true)}
          onNewRecon={() => setNewReconOpen(true)}
        />
        <div style={{ flex: 1, overflowY: 'auto', position: 'relative' }}>
          {view === 'projects' && (
            <Projects projects={projects} sessions={sessions}
              onOpenProject={onOpenProject} onStandalone={onStandalone}
              onCreate={onCreateProject} onRename={onRenameProject}
              onRescope={onRescopeProject} onDelete={onDeleteProject} />
          )}
          {view === 'overview' && <Overview vm={overviewVm} />}
          {view === 'sessions' && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '18px 30px 0' }}>
                <button onClick={() => setView('projects')} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '5px 11px', borderRadius: '8px', border: `1px solid ${C.lineStrong}`, background: C.control, color: C.muted, cursor: 'pointer', fontSize: '12px', fontWeight: 600 }}>‹ Projects</button>
                <span style={{ fontSize: '12.5px', color: C.faint }}>{activeProject ? activeProject.name : 'Standalone · all sessions'}</span>
              </div>
              <Sessions sessions={sessionsVm} onNewRecon={() => setNewReconOpen(true)}
                onOpen={(s) => { setSelectedId(s.id); setView('overview'); }}
                onStop={onStopSession} onResume={onResumeSession}
                onRename={onRenameSession} onDelete={onDeleteSession}
                onEditScope={(s) => setScopeTarget(s)} />
            </>
          )}
          {view === 'findings' && (
            <Findings
              findings={findings} statusMap={statusMap}
              onTriage={onTriage} onCopy={onCopy} onOpenSource={onOpenSource}
              onExport={() => setExportOpen(true)}
              openFp={findingTarget} onOpened={() => setFindingTarget(null)}
            />
          )}
          {view === 'sources' && (
            <Sources
              sessionId={selectedId} findings={findings}
              openTarget={sourceTarget} onClearTarget={() => setSourceTarget(null)}
              onPickFinding={() => setView('findings')}
            />
          )}
        </div>
      </main>
      {activityOpen && <ActivityDrawer jobs={jobs} onClose={() => setActivityOpen(false)} onStop={onStopJob} />}
      {searchOpen && <SearchPalette findings={findings} onClose={() => setSearchOpen(false)} onPick={onSearchPick} />}
      {newReconOpen && <NewReconModal onClose={() => setNewReconOpen(false)} onStart={onStartRecon} busy={reconBusy} />}
      {exportOpen && <ExportModal findings={findings} target={targetHost} onClose={() => setExportOpen(false)} onDone={(msg) => { setExportOpen(false); flash(msg); }} />}
      {scopeTarget && <ScopeModal session={scopeTarget} busy={scopeBusy} onClose={() => setScopeTarget(null)} onSave={onSaveScope} />}
      {toast && (
        <div style={{ position: 'fixed', bottom: '18px', left: '50%', transform: 'translateX(-50%)', background: '#1a1f2c', border: `1px solid ${C.lineHover}`, borderRadius: '9px', padding: '10px 16px', fontSize: '12.5px', color: C.textSoft, boxShadow: '0 10px 30px rgba(0,0,0,0.5)', zIndex: 50 }}>
          {toast}
        </div>
      )}
    </div>
  );
}
