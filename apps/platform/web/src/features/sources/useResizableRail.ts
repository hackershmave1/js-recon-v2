import { useCallback, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

// A resizable + collapsible left rail. The rail width is dragged via a divider
// (pointer-capture so the drag survives the cursor leaving the 6px handle) and
// persisted to localStorage so it survives per-run remounts of the Sources page.
const MIN_W = 180;
const MAX_W = 640;
const DEFAULT_W = 300;
const KEY_W = "recon.sourcesRailWidth";
const KEY_COLLAPSED = "recon.sourcesRailCollapsed";

function initialWidth(): number {
  const v = Number(localStorage.getItem(KEY_W));
  return Number.isFinite(v) && v >= MIN_W && v <= MAX_W ? v : DEFAULT_W;
}

export function useResizableRail() {
  const [width, setWidthState] = useState(initialWidth);
  const [collapsed, setCollapsedState] = useState(() => localStorage.getItem(KEY_COLLAPSED) === "1");
  const dragging = useRef(false);

  const setWidth = useCallback((w: number) => {
    const clamped = Math.min(MAX_W, Math.max(MIN_W, w));
    setWidthState(clamped);
    try { localStorage.setItem(KEY_W, String(clamped)); } catch { /* storage may be unavailable */ }
  }, []);

  const setCollapsed = useCallback((c: boolean) => {
    setCollapsedState(c);
    try { localStorage.setItem(KEY_COLLAPSED, c ? "1" : "0"); } catch { /* storage may be unavailable */ }
  }, []);

  const onPointerDown = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    dragging.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
  }, []);

  // Width = pointer X relative to the split container's left edge (the rail is the
  // leftmost child), so dragging left shrinks the rail and expands the code view.
  const onPointerMove = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    // Defensive: if no button is held — a pointerup we missed, or the handle
    // remounting mid-drag — stop dragging instead of resizing on a bare hover.
    if (e.buttons === 0) { dragging.current = false; return; }
    const host = e.currentTarget.parentElement;
    if (!host) return;
    setWidth(e.clientX - host.getBoundingClientRect().left);
  }, [setWidth]);

  const onPointerUp = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    dragging.current = false;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* not captured */ }
  }, []);

  const toggleCollapsed = useCallback(() => setCollapsed(!collapsed), [collapsed, setCollapsed]);

  return { width, collapsed, toggleCollapsed, resizerProps: { onPointerDown, onPointerMove, onPointerUp } };
}
