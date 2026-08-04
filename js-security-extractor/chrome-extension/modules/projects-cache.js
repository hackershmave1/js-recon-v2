// A tiny read-through cache for the projects list so the popup's engagement picker keeps
// working through a brief workspace outage (spec §8.1). Pure + injectable (storage + fetcher);
// no chrome/fetch references — the background worker injects chrome.storage.local and a
// WorkspaceClient.listProjects call. Unit-tested with an in-memory storage fake.

const CACHE_KEY = 'projectsCache';
const CACHE_AT_KEY = 'projectsCacheAt';

export async function readCache(storage) {
  const got = (await storage.get([CACHE_KEY, CACHE_AT_KEY])) || {};
  const projects = Array.isArray(got[CACHE_KEY]) ? got[CACHE_KEY] : null;
  const cachedAt = typeof got[CACHE_AT_KEY] === 'number' ? got[CACHE_AT_KEY] : 0;
  return { projects, cachedAt };
}

export async function writeCache(storage, projects, now = Date.now()) {
  await storage.set({ [CACHE_KEY]: projects, [CACHE_AT_KEY]: now });
  return projects;
}

// Try the live fetch; on success refresh the cache and return source 'live'. On failure OR a
// reachable-but-unsuccessful response, fall back to the last cached list ('cache'), or [] if
// there is none ('empty'). Never throws — capture must not break when the workspace blips.
export async function listProjectsWithCache(fetcher, storage, now = Date.now()) {
  try {
    const result = await fetcher();
    if (result && result.success && Array.isArray(result.projects)) {
      await writeCache(storage, result.projects, now);
      return { projects: result.projects, source: 'live' };
    }
  } catch (e) {
    // fall through to cache
  }
  try {
    const { projects } = await readCache(storage);
    return { projects: projects || [], source: projects ? 'cache' : 'empty' };
  } catch (e) {
    return { projects: [], source: 'empty' };   // storage itself failed — still never throw
  }
}
