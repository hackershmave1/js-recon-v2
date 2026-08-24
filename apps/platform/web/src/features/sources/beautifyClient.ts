// D35: the main-thread client for the beautify Web Worker. Lazily spins up ONE module
// worker on first use, so merely importing this module (e.g. in a jsdom test, which has
// no real Worker) never touches the Worker API — tests mock `beautify` instead. Each
// request is tagged with an id and resolved when the worker posts its result back, so
// overlapping requests (the user switching files mid-format) can't cross their answers.

let worker: Worker | null = null;
let sequence = 0;
// Each pending request keeps its raw code so a worker crash / chunk-load failure can fail
// SOFT (resolve with the raw text) rather than hang the viewer's "Formatting…" state
// forever — matching the old main-thread `try/catch` fallback.
const pending = new Map<number, { resolve: (text: string) => void; code: string }>();

function failAllPendingSoft(): void {
  for (const [id, entry] of pending) {
    pending.delete(id);
    entry.resolve(entry.code);
  }
}

function ensureWorker(): Worker {
  if (worker) return worker;
  const created = new Worker(new URL("./beautify.worker.ts", import.meta.url), { type: "module" });
  created.onmessage = (event: MessageEvent<{ id: number; text: string }>) => {
    const entry = pending.get(event.data.id);
    if (entry) {
      pending.delete(event.data.id);
      entry.resolve(event.data.text);
    }
  };
  // A worker that fails to load or crashes never posts a message, so without this the
  // pending promise never settles. Resolve every outstanding request soft (raw code) and
  // drop the worker so the next call retries with a fresh one.
  created.onerror = () => {
    failAllPendingSoft();
    worker = null;
  };
  worker = created;
  return created;
}

// Beautify `code` off the main thread. Always resolves — with the formatted text, or the
// raw code on any worker load/runtime failure (fail-soft, never rejects, never hangs).
export function beautify(code: string): Promise<string> {
  const id = ++sequence;
  return new Promise<string>((resolve) => {
    pending.set(id, { resolve, code });
    try {
      ensureWorker().postMessage({ id, code });
    } catch {
      // `new Worker` can throw synchronously (Worker undefined, or a CSP SecurityError).
      pending.delete(id);
      resolve(code);
    }
  });
}
