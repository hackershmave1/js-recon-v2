// quarantine-queue.js — holds the scripts of hosts the researcher has not decided on (ADR-0010).
//
// A script admitted as QUARANTINE (modules/script-admission.js) is fetched here WITHOUT credentials
// and uploaded to the platform, which stores it apart from the run and analyzes nothing until Add.
// It is a LEAF: this module never extracts dependencies, chunks or source maps from it, so one
// undecided host cannot pull the capture onto more unknown hosts. Capture and analysis never wait:
// this is a separate serial queue behind the capture's own.
//
// Best effort by design: the queue lives in memory. If the worker is torn down before an upload,
// that script is simply not held, and the host is still offered (the researcher's Add re-releases
// what the platform holds; a reload brings the rest). Nothing here is allowed to fail the capture.
const MAX_QUEUED = 200;  // a page that loads thousands of scripts cannot grow the worker without bound

export class QuarantineQueue {
  // fetchContent(url) -> { success, content }   (ContentFetcher.fetchQuarantined: credentials omit)
  // upload({ runId, sessionId, host, files }) -> { ok, data: { files: [{ url, outcome }] } }
  // onHeld(host) / onIgnored(host): outcome callbacks (the lifecycle counts and remembers them)
  constructor({ fetchContent, upload, onHeld = () => {}, onIgnored = () => {}, maxQueued = MAX_QUEUED }) {
    this.fetchContent = fetchContent;
    this.upload = upload;
    this.onHeld = onHeld;
    this.onIgnored = onIgnored;
    this.maxQueued = maxQueued;
    this.queue = [];
    this.seen = new Set();
    this.running = null;
  }

  // Forget what was seen when a capture starts: a new capture re-sends a url the last one held.
  reset() {
    this.queue = [];
    this.seen = new Set();
  }

  enqueue({ url, host, runId, sessionId }) {
    const key = `${runId}|${url}`;
    if (!runId || this.seen.has(key) || this.queue.length >= this.maxQueued) return false;
    this.seen.add(key);
    this.queue.push({ url, host, runId, sessionId });
    this.running = this.running || this.pump();
    return true;
  }

  async pump() {
    try {
      while (this.queue.length) {
        const item = this.queue.shift();
        try {
          await this.hold(item);
        } catch (e) { /* one bad script never stops the rest */ }
      }
    } finally {
      this.running = null;
    }
  }

  async hold({ url, host, runId, sessionId }) {
    const fetched = await this.fetchContent(url);
    if (!fetched || !fetched.success || typeof fetched.content !== 'string' || !fetched.content) return;
    const res = await this.upload({ runId, sessionId, host, files: [{ url, content: fetched.content }] });
    if (!res || !res.ok || !res.data) return;
    for (const file of res.data.files || []) {
      if (file.outcome === 'stored') this.onHeld(host);
      else if (file.outcome === 'ignored') this.onIgnored(host);
    }
  }

  // Resolves when everything queued so far has been attempted (tests; End does not wait on it).
  async idle() {
    while (this.running) await this.running;
  }
}
