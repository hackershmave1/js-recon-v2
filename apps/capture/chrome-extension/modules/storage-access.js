// What a web page's content script may read from extension storage.
//
// chrome.storage.local holds the Bearer token, workspaceUrl and the capture settings, and by
// default it is readable AND writable from content scripts
// (developer.chrome.com/docs/extensions/reference/api/storage, `setAccessLevel`). Code running in
// the content script's context on any page could otherwise read the token, or re-point
// workspaceUrl so the next worker start uploads captures elsewhere — going around the message
// sender check. The content script needs exactly one bit, "is capture on?", read without waking
// the worker, so that bit alone is published to storage.session, which is opened to content
// scripts; storage.local is limited to the extension's own pages and worker.

export async function restrictStorageAccess(storage) {
  await storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' });
  await storage.session.setAccessLevel({ accessLevel: 'TRUSTED_AND_UNTRUSTED_CONTEXTS' });
}

// The ONLY value written to page-readable storage. Never put anything sensitive here.
export function publishCaptureState(storage, isCapturing) {
  return storage.session.set({ isCapturing: isCapturing === true });
}
