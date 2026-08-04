// api.js — thin wrappers around the background service worker message API and a
// couple of chrome.* helpers the popup needs. Keeps chrome.* calls out of the views.

export function send(action, extra = {}) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ action, ...extra }, (resp) => {
        // swallow "receiving end does not exist" during worker spin-up
        void chrome.runtime.lastError;
        resolve(resp || {});
      });
    } catch (e) {
      resolve({});
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
export const getExportData = () => send('getExportData');
export const testConnection = () => send('testConnection');
export const updateSettings = (settings) => send('updateSettings', { settings });
// Project-scoped capture: list engagements (cached in the worker) and quick-create one.
export const listProjects = () => send('listProjects');
export const createProject = (project) => send('createProject', { project });
// Decoupled analysis: trigger the backend's async job for the current session, and poll
// its per-file progress for the captures feed.
export const analyzeSession = () => send('analyzeSession');
export const getAnalysisProgress = () => send('getAnalysisProgress');

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
