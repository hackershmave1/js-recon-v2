// Client-side performance visibility (D25 follow-up).
//
// The Sources-view freeze was a *main-thread hang* with zero client-side
// instrumentation, so the regression was invisible in logs and had to be
// root-caused by reading code. This module gives the SPA the missing eyes:
//   - a long-task warning: any task that blocks the main thread past the jank
//     threshold is logged with the route that caused it (a heavy render, a
//     synchronous parse, etc.);
//   - `measureSync`: a labelled span in the User Timing API (visible in the
//     devtools Performance panel) that also warns when a block runs long.
// Both no-op safely where the API is absent (jsdom, or a browser without the
// longtask entry type), so they are cheap to leave on in production.

// ~7 dropped frames at 60fps. Below this is normal work; at/above it the user
// feels a stutter, and a multi-hundred-ms entry is the freeze this fixed.
const LONG_TASK_MS = 120;

let observing = false;

export function installPerfObserver(): void {
  if (observing || typeof PerformanceObserver === "undefined") return;
  observing = true;
  try {
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (entry.duration >= LONG_TASK_MS) {
          console.warn(
            `[perf] long task ${Math.round(entry.duration)}ms at ${location.pathname} — main thread blocked`,
          );
        }
      }
    });
    // `buffered` replays tasks recorded before this observer attached (e.g. a
    // freeze during initial load).
    observer.observe({ type: "longtask", buffered: true });
  } catch {
    // The longtask entry type is unsupported (Firefox/Safari) — non-fatal.
  }
}

// Time a synchronous block. Emits a `performance.measure` (User Timing) and warns
// when it exceeds `warnMs`. Returns the block's value unchanged.
export function measureSync<T>(label: string, warnMs: number, fn: () => T): T {
  if (typeof performance === "undefined" || typeof performance.mark !== "function") return fn();
  const startMark = `${label}:start`;
  performance.mark(startMark);
  try {
    return fn();
  } finally {
    try {
      const measure = performance.measure(label, startMark);
      if (measure && measure.duration >= warnMs) {
        console.warn(`[perf] ${label} took ${Math.round(measure.duration)}ms`);
      }
      performance.clearMarks(startMark);
      performance.clearMeasures(label);
    } catch {
      // Marks cleared or unsupported — non-fatal.
    }
  }
}
