// D35: beautify JS OFF the main thread so a multi-MB minified bundle never freezes
// the tab — the freeze that forced the old 200 KB client cap. This mirrors Chrome
// DevTools' FormatterWorker: the main thread posts raw code + a request id, this
// worker runs js-beautify and posts back the formatted text. js-beautify is imported
// lazily inside the handler (matching the app's proven CJS-interop shape) so the
// worker module itself stays tiny.

interface BeautifyRequest {
  id: number;
  code: string;
}

// `self` is the DedicatedWorkerGlobalScope; cast to just the message surface we use so
// this file compiles under the project's DOM lib without pulling the webworker lib.
const ctx = self as unknown as {
  onmessage: ((event: MessageEvent<BeautifyRequest>) => void) | null;
  postMessage: (message: { id: number; text: string }) => void;
};

ctx.onmessage = async (event) => {
  const { id, code } = event.data;
  try {
    const mod = await import("js-beautify");
    // Vite's CJS interop may put the named export directly or under `default`.
    const beautify =
      mod.js_beautify ?? (mod as unknown as { default?: typeof mod }).default?.js_beautify;
    const text = beautify ? beautify(code, { indent_size: 2, end_with_newline: false }) : code;
    ctx.postMessage({ id, text });
  } catch {
    // Fail-soft, exactly like the old main-thread formatJs: hand back the raw code so
    // the viewer still shows something rather than erroring.
    ctx.postMessage({ id, text: code });
  }
};
