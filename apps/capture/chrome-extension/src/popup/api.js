// api.js — thin wrappers around the background service worker message API and a
// couple of chrome.* helpers the popup needs. Keeps chrome.* calls out of the views.

// The popup's ONE safety net (C7). The worker owns the real deadlines (every network call it
// makes is bounded, and it answers before slow work like the stop-capture flush), so this only
// fires if the worker never answers at all (killed mid-request, a bug). The outcome is then
// UNKNOWN — the action may well have happened — so the app re-syncs from the worker's state
// instead of showing a failure. captureStart rotates/adopts the session and asks the platform for
// a run behind the worker's serialized Start/End chain, so it gets longer.
const NO_REPLY_MS = { captureStart: 300000 };
const DEFAULT_NO_REPLY_MS = 60000;
// Reads the popup already repeats on its 2 s poll: an unanswered one needs no re-sync of its own
// (re-syncing would send more reads, which would time out too — a feedback loop).
const POLLED_READS = new Set(['getStatus', 'getFiles', 'getAnalysisProgress', 'listProjects', 'getSessionFindingsSummary']);
// The worker took the message and died before answering (MV3 teardown, a crash): Chrome closes
// the channel. Unlike "Receiving end does not exist" (never delivered), the action may have run.
const CHANNEL_CLOSED = /message (port|channel) closed/i;
let onNoReply = () => {};

// The app registers what "re-sync" means (re-read status/files) once, on mount.
export function setNoReplyHandler(handler) { onNoReply = typeof handler === 'function' ? handler : () => {}; }

// Text for a failed reply: an unknown outcome must not read like a definite failure.
export function replyError(res, fallback) {
  if (res?.unknownOutcome) return 'No answer from the extension yet — status refreshed';
  return res?.error || fallback;
}

// Every reply is `{ ok, error?, ...detail }` (the worker's message-reply envelope).
export function send(action, extra = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (res) => { if (!settled) { settled = true; clearTimeout(timer); resolve(res); } };
    const unknown = () => {
      if (settled) return;
      finish({ ok: false, error: 'no_reply', unknownOutcome: true });
      if (!POLLED_READS.has(action)) onNoReply(action);
    };
    const timer = setTimeout(unknown, NO_REPLY_MS[action] || DEFAULT_NO_REPLY_MS);
    try {
      chrome.runtime.sendMessage({ action, ...extra }, (resp) => {
        const lastError = chrome.runtime.lastError;  // read it, or Chrome logs "unchecked"
        if (lastError && CHANNEL_CLOSED.test(lastError.message || '')) unknown();
        else if (lastError) finish({ ok: false, error: lastError.message || 'no_receiver' });
        else finish(resp || { ok: false, error: 'empty_reply' });
      });
    } catch (e) {
      finish({ ok: false, error: e?.message || 'send_failed' });
    }
  });
}

export const getStatus = () => send('getStatus');
export const getFiles = () => send('getFiles');
// Capture lifecycle (the worker owns the capture; getStatus carries `capture` / `lastEnded`).
// Sessions whose scope holds `scope`, for "Continue": { ok, sessions: [...] }.
export const captureLookup = (scope) => send('captureLookup', { scope });
// Start a capture: { scope, mode: 'new', projectId } or { scope, mode: 'continue', match }.
export const captureStart = (payload) => send('captureStart', payload);
export const capturePause = () => send('capturePause');
export const captureResume = () => send('captureResume');
// Answers at once with { ok, ending: true }; uploads drain and the final analysis runs after.
export const captureEnd = () => send('captureEnd');
// The "New site seen" banner: add the site to the session's scope, or stop asking about it.
export const captureAddScope = (host) => send('captureAddScope', { host });
export const captureDismissSite = (site) => send('captureDismissSite', { site });
// "Done" on the "Capture ended" banner.
export const captureDismissEnded = () => send('captureDismissEnded');
export const getExportData = (opts = {}) => send('getExportData', opts);
export const clearFiles = () => send('clearFiles');
export const testConnection = () => send('testConnection');
export const updateSettings = (settings) => send('updateSettings', { settings });
// Central login: authenticate to the workspace so captures route to the operator's tenant.
export const login = (username, password) => send('login', { username, password });
export const logout = () => send('logout');
// Project-scoped capture: list engagements (cached in the worker) and quick-create one.
export const listProjects = () => send('listProjects');
export const createProject = (project) => send('createProject', { project });
// Decoupled analysis: trigger the backend's async job for the current session, and poll
// its per-file progress for the captures feed.
export const analyzeSession = () => send('analyzeSession');
export const getAnalysisProgress = () => send('getAnalysisProgress');
// D46: fetch the findings summary for the session's latest completed run (popup card).
export const getSessionFindingsSummary = (sessionId) => send('getSessionFindingsSummary', { sessionId });
// LLM provider config — save encrypts the key on the platform server.
export const saveLlmConfig = (config) => send('saveLlmConfig', { config });
export const testLlmConfig = () => send('testLlmConfig');

// Active tab hostname for "Use this site as scope". Empty when the tab is not a website (a new
// tab, chrome://, a local file), so the one-click scope is offered only where it means something.
export async function getActiveTabHost() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const url = tab?.url ? new URL(tab.url) : null;
    if (url && (url.protocol === 'http:' || url.protocol === 'https:')) return url.hostname;
  } catch (e) { /* ignore */ }
  return '';
}

export function openTab(url) {
  if (!url) return;
  try { chrome.tabs.create({ url }); } catch (e) { /* ignore */ }
}

export const extensionVersion = () => {
  try { return chrome.runtime.getManifest().version; } catch (e) { return ''; }
};

// Trigger a JSON download from the popup (object URL + anchor click).
export function downloadJson(data, filename) {
  const json = JSON.stringify(data, null, 2);
  const blob = new Blob([json], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
