// useCaptureFlow.js — the popup's side of the capture lifecycle. The WORKER owns the capture
// (getStatus.capture); this hook only holds what exists before a capture does — the scope being
// chosen, the Continue lookup, the Continue/New pick — and turns button presses into worker actions.
import { useEffect, useState } from 'preact/hooks';
import * as api from './api.js';
import {
  capturePhase, continueOptions, defaultChoice, scopeLabel, startEngagement, startErrorText,
  suggestScope, typedScope,
} from './viewmodels/captureVm.js';

// The chosen scope survives a closed popup (End can take a while, and "End + start new" pre-fills
// the next scope). Per-viewer convenience only: the capture's real scope lives in the worker.
const DRAFT_KEY = 'reconScopeDraft';
function readDraft() {
  try { return localStorage.getItem(DRAFT_KEY) || ''; } catch (e) { return ''; }
}
function writeDraft(value) {
  try {
    if (value) localStorage.setItem(DRAFT_KEY, value);
    else localStorage.removeItem(DRAFT_KEY);
  } catch (e) { /* storage unavailable: the draft just won't survive a close */ }
}

export function useCaptureFlow({
  status, ready, activeHost, projects, projectsLoaded, projectId,
  showToast, refresh, onSessionExpired, onCaptureStarted,
}) {
  const phase = capturePhase(status);
  const [scope, setScopeState] = useState(readDraft);
  const [lookup, setLookup] = useState({ state: 'idle', sessions: [] });
  const [choice, setChoice] = useState('new');
  const [busy, setBusy] = useState('');  // the action in flight ('start' | 'end' | ...), for disabling

  const setScope = (value) => { writeDraft(value); setScopeState(value); };

  // Ask the workspace which sessions already hold this scope, once a scope is chosen and no capture
  // is running (`ready`: the worker's real status has arrived, so "idle" is not just the default).
  // A failed lookup still lets you start a New session.
  useEffect(() => {
    if (!ready || !scope || phase !== 'idle') { setLookup({ state: 'idle', sessions: [] }); return undefined; }
    let alive = true;
    setLookup({ state: 'loading', sessions: [] });
    api.captureLookup(scope).then((res) => {
      if (!alive) return;
      if (res?.ok) {
        const sessions = Array.isArray(res.sessions) ? res.sessions : [];
        setLookup({ state: 'ready', sessions });
        setChoice(defaultChoice(continueOptions(sessions, [], scope)));
      } else {
        if (res?.status === 401) onSessionExpired();
        setLookup({ state: 'error', sessions: [] });
        setChoice('new');
      }
    });
    return () => { alive = false; };
  }, [scope, phase, ready]);

  const options = continueOptions(lookup.sessions, projects, scope);
  const picked = options.find((o) => o.key === choice) || null;

  async function run(name, action) {
    setBusy(name);
    try { return await action(); } finally { setBusy(''); refresh(); }
  }

  function fail(res, fallback) {
    if (res?.unknownOutcome) return;  // the no-reply handler re-reads the worker's state
    if (res?.error === 'session_expired') onSessionExpired();
    showToast(fallback, 'error');
  }

  async function start() {
    if (!scope || busy) return;
    const engagement = startEngagement(projectId, projects, projectsLoaded);
    if (!picked && engagement.wait) { showToast('Loading engagements…'); return; }
    const payload = picked
      ? { scope, mode: 'continue', match: { external_id: picked.match.external_id, engagement_id: picked.match.engagement_id || null, captures: picked.match.captures || 0 } }
      : { scope, mode: 'new', projectId: engagement.projectId };
    const res = await run('start', () => api.captureStart(payload));
    if (res?.ok) {
      setScope('');
      onCaptureStarted();
      showToast(`Capturing ${scopeLabel(scope)}`);
    } else {
      fail(res, startErrorText(res));
    }
  }

  async function addSite(site) {
    const res = await run('site', () => api.captureAddScope(site));
    if (res?.ok) showToast(`${site} added to this session's scope`);
    else fail(res, `Could not add ${site} to the scope`);
  }

  async function end(nextScope) {
    const res = await run('end', () => api.captureEnd());
    // An unknown outcome may well have ended it: keep the pre-fill (it only shows once idle).
    if (nextScope && (res?.ok || res?.unknownOutcome)) setScope(nextScope);
    if (!res?.ok) fail(res, 'Could not end the capture');
  }

  const suggestion = suggestScope(activeHost);
  return {
    phase, busy,
    scopeDraft: scope, suggestion,
    useTabScope: () => { if (suggestion) setScope(suggestion.scope); },
    // Returns whether the text was a usable scope (the card keeps a rejected entry for editing).
    setTypedScope: (text) => {
      const entry = typedScope(text);
      if (entry) setScope(entry); else showToast('Type a domain, e.g. acme.io', 'warn');
      return !!entry;
    },
    clearScope: () => setScope(''),
    lookupState: lookup.state, options, choice, setChoice, picked,
    start,
    pause: async () => {
      const res = await run('pause', () => api.capturePause());
      if (!res?.ok) fail(res, 'Could not pause the capture');
    },
    resume: async () => {
      const res = await run('resume', () => api.captureResume());
      if (!res?.ok) fail(res, 'Could not resume the capture');
    },
    end: () => end(''),
    addSite,
    endAndStartNew: (site) => end(site),
    dismissSite: (site) => run('site', () => api.captureDismissSite(site)),
    dismissEnded: () => run('ended', () => api.captureDismissEnded()),
  };
}
