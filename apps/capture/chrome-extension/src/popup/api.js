// api.js — thin wrappers around the background service worker message API and a
// couple of chrome.* helpers the popup needs. Keeps chrome.* calls out of the views.

// The popup's ONE safety net (C7). The worker owns the real deadlines (every network call it
// makes is bounded, and it answers before slow work like the stop-capture flush), so this only
// fires if the worker never answers at all (killed mid-request, a bug). The outcome is then
// UNKNOWN — the action may well have happened — so the app re-syncs from the worker's state
// instead of showing a failure. newSession rotates and re-binds the session, so it gets longer.
const NO_REPLY_MS = { newSession: 300000 };
const DEFAULT_NO_REPLY_MS = 60000;
// Reads the popup already repeats on its 2 s poll: an unanswered one needs no re-sync of its own
// (re-syncing would send more reads, which would time out too — a feedback loop).
const POLLED_READS = new Set(['getStatus', 'getFiles', 'getAnalysisProgress', 'getHistory', 'listProjects', 'getSessionFindingsSummary']);
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
export const startCapture = () => send('startCapture');
export const stopCapture = () => send('stopCapture');
// Start a fresh session. Payload is the popup-resolved snapshot the background applies:
// { projectId, scope: { rootDomains, includeSubdomains }, captureConfig, overrideKeys }.
export const newSession = (payload) => send('newSession', payload);
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
// D46(c): fetch the persisted capture history list (past sessions).
export const getHistory = () => send('getHistory');
// LLM provider config — save encrypts the key on the platform server.
export const saveLlmConfig = (config) => send('saveLlmConfig', { config });
export const testLlmConfig = () => send('testLlmConfig');

// Active tab hostname for the capture-target card. Falls back gracefully.
export async function getActiveTabHost() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab?.url) return new URL(tab.url).hostname;
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
