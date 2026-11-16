// team-rules.js — the team's additions to the built-in third-party rules (ADR-0010, R2).
//
// The platform is the one home of the team's rules (workspace Settings). The extension keeps the last
// copy it downloaded in memory (read by capture-scope.isNoiseHost and asset-classifier.matchesDenylist)
// and in chrome.storage.local, so a restarted worker or a failed download still has it. The built-in
// list is compiled in (modules/third-party-rules.js) and is never touched here: a team entry can only
// ADD to what is dropped. Pure (no chrome global): storage and the download are injected.

export const STORAGE_KEY = 'teamThirdPartyRules';
const MAX_ENTRIES = 500;       // per kind; the platform caps the team lower, this is a safety net
const MAX_LENGTH = 200;
const DEFAULT_TIMEOUT_MS = 8000;

let rules = { hosts: [], filePatterns: [] };

function cleanList(list, normalize) {
  if (!Array.isArray(list)) return [];
  const out = [];
  for (const item of list) {
    if (typeof item !== 'string') continue;
    const value = normalize(item.trim());
    if (value && value.length <= MAX_LENGTH && !out.includes(value)) out.push(value);
    if (out.length >= MAX_ENTRIES) break;
  }
  return out;
}

export function setTeamRules(raw) {
  const src = raw && typeof raw === 'object' ? raw : {};
  rules = {
    hosts: cleanList(src.hosts, (v) => v.toLowerCase().replace(/^\*\./, '').replace(/\.$/, '')),
    filePatterns: cleanList(src.filePatterns, (v) => v.toLowerCase()),
  };
  return rules;
}

export function getTeamRules() {
  return { hosts: [...rules.hosts], filePatterns: [...rules.filePatterns] };
}

export function resetTeamRules() { setTeamRules(null); }

// Read-only views for the matchers (no copy: they run per request).
export function teamHosts() { return rules.hosts; }
export function teamFilePatterns() { return rules.filePatterns; }

export async function loadSavedTeamRules(storageArea) {
  try {
    const got = await storageArea.get(STORAGE_KEY);
    if (got && got[STORAGE_KEY]) setTeamRules(got[STORAGE_KEY]);
  } catch (e) { /* no saved copy: the built-in list alone */ }
  return getTeamRules();
}

// Download the team's rules and apply + save them. `fetchRules()` resolves { ok, data } where data is
// the platform's /settings/third-party-rules/merged body. Any failure (HTTP error, timeout, throw,
// odd body) keeps what is already loaded (the last saved copy) and reports source 'saved'. Never
// throws and never waits longer than timeoutMs, so capture is never blocked on it.
export async function syncTeamRules({ fetchRules, storageArea, timeoutMs = DEFAULT_TIMEOUT_MS }) {
  let timer;
  try {
    const res = await Promise.race([
      Promise.resolve().then(fetchRules),
      new Promise((resolve) => { timer = setTimeout(() => resolve(null), timeoutMs); }),
    ]);
    const data = res && res.ok ? res.data : null;
    if (data && Array.isArray(data.team_hosts) && Array.isArray(data.team_file_patterns)) {
      setTeamRules({ hosts: data.team_hosts, filePatterns: data.team_file_patterns });
      try { await storageArea.set({ [STORAGE_KEY]: getTeamRules() }); } catch (e) { /* kept in memory */ }
      return { source: 'network' };
    }
  } catch (e) { /* fall through to the saved copy */ } finally { clearTimeout(timer); }
  return { source: 'saved' };
}
