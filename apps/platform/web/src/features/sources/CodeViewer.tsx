import { useEffect, useMemo, useRef, useState } from "react";
import { highlightLine, loadPrism, type HighlightedSpan } from "./highlight";

// D35: the code body is VIRTUALIZED — a beautified multi-MB bundle can be hundreds of
// thousands of lines, and committing one DOM node per line (the old approach) exploded
// to ~1M nodes and froze the machine. We render only the rows intersecting the scroll
// viewport (plus overscan), between two spacer divs that reserve the full scroll height.
// Normal-flow rows (not absolute) keep horizontal scroll of a long line working. Fixed
// row height makes windowing + jump-to-line O(1).

// MUST equal .sv-line height in sources.css, or windowed offsets drift over ~100K rows.
const CODE_ROW_HEIGHT = 21;
// Rows rendered above/below the viewport so a fast scroll doesn't flash blank.
const OVERSCAN = 20;
// Per-line highlight cap (Monaco's maxTokenizationLineLength): a line longer than this —
// a surviving data-URI or long string literal — is rendered plain, never tokenized, so
// one pathological line can't stall Prism.
const HIGHLIGHT_MAX_LINE = 20_000;
// Per-line RENDER cap: a genuinely huge single line (a raw bundle that beautify couldn't
// format) is truncated in the DOM so laying out one multi-MB line can't jank; the full
// bytes remain reachable via Download.
const RENDER_MAX_LINE = 100_000;

const UpIcon = () => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="m18 15-6-6-6 6" />
  </svg>
);

// Presentational: renders `text` line-by-line, windowed to the viewport. `marks` (line ->
// finding type) is null when the view is pretty-printed (original line numbers no longer
// map after reformatting). `focusLine` is the jumped-to line (highlighted + scrolled to
// the middle); it still applies to pretty-printed text even though marks don't.
export function CodeViewer({ text, truncated, marks, focusLine }: {
  text: string; truncated: boolean; marks: Map<number, string> | null; focusLine?: number | null;
}) {
  const lines = useMemo(() => text.split("\n"), [text]);
  const total = lines.length;

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewHeight, setViewHeight] = useState(600);
  // Mirror of viewHeight for the jump-to-line effect to read WITHOUT depending on it, so a
  // window resize (which changes viewHeight) doesn't re-fire the jump and yank the user's
  // scroll position back to the last focused line.
  const viewHeightRef = useRef(600);
  const [scrolled, setScrolled] = useState(false);

  // Track the scroll viewport height. ResizeObserver is absent in jsdom (and older
  // runtimes); without it the viewport falls back to 600px and windowing still works.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = () => {
      const h = el.clientHeight || 600;
      setViewHeight(h);
      viewHeightRef.current = h;
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    setScrollTop(el.scrollTop);
    setScrolled(el.scrollTop > 300);
  };

  const start = Math.max(0, Math.floor(scrollTop / CODE_ROW_HEIGHT) - OVERSCAN);
  const end = Math.min(total, Math.ceil((scrollTop + viewHeight) / CODE_ROW_HEIGHT) + OVERSCAN);

  // Load Prism once; re-render to highlight when it resolves. Plain text until then / on failure.
  const [prism, setPrism] = useState<Awaited<ReturnType<typeof loadPrism>> | null>(null);
  useEffect(() => {
    let live = true;
    void loadPrism().then((p) => { if (live) setPrism(p); }).catch(() => { /* plain text */ });
    return () => { live = false; };
  }, []);

  // Highlight ONLY the visible window, per line (a few dozen short lines — cheap even on
  // every scroll). A too-long line is left plain (see HIGHLIGHT_MAX_LINE).
  const spansByIndex = useMemo(() => {
    const map = new Map<number, HighlightedSpan[]>();
    if (!prism) return map;
    for (let i = start; i < end; i++) {
      const line = lines[i];
      if (line && line.length <= HIGHLIGHT_MAX_LINE) {
        try { map.set(i, highlightLine(prism, line)); } catch { /* leave plain */ }
      }
    }
    return map;
  }, [prism, lines, start, end]);

  // Jump-to-line: center the focused row. Fixed row height makes this O(1) and works even
  // when the row isn't currently mounted (unlike scrollIntoView on a ref) — we drive the
  // window via state AND scroll the DOM. Keyed on the focus target + line count only, so a
  // viewport resize doesn't re-center (viewHeight is read from a ref).
  useEffect(() => {
    if (focusLine == null) return;
    const target = Math.min(Math.max(focusLine, 1), total); // clamp a stale/beyond-EOF jump
    const top = Math.max(0, (target - 1) * CODE_ROW_HEIGHT - viewHeightRef.current / 2);
    setScrollTop(top);
    const el = scrollRef.current;
    if (el) { try { el.scrollTo({ top }); } catch { el.scrollTop = top; } }
  }, [focusLine, total]);

  const scrollToTop = () => {
    const el = scrollRef.current;
    if (!el) return;
    try { el.scrollTo({ top: 0, behavior: "smooth" }); } catch { el.scrollTop = 0; }
  };

  const rows = [];
  for (let i = start; i < end; i++) {
    const n = i + 1;
    const raw = lines[i] ?? "";
    const line = raw.length > RENDER_MAX_LINE ? raw.slice(0, RENDER_MAX_LINE) + " …" : raw;
    const mark = marks?.get(n);
    const focused = focusLine === n;
    const spans = spansByIndex.get(i);
    rows.push(
      <div key={n} className={"sv-line" + (mark ? " marked" : "") + (focused ? " focus" : "")}>
        <span className="sv-ln">{n}</span>
        <span className="sv-code-txt">
          {spans ? spans.map((s, j) => <span key={j} className={s.className}>{s.text}</span>) : line}
        </span>
        {mark && <span className="sv-mark">{mark}</span>}
      </div>,
    );
  }

  return (
    <div className="sv-code-wrap">
      {truncated && (
        <div className="sv-note sv-warn">File truncated — showing a capped preview. Download to view the full file.</div>
      )}
      <div className="sv-code" ref={scrollRef} onScroll={onScroll}>
        <div style={{ height: start * CODE_ROW_HEIGHT }} />
        {rows}
        <div style={{ height: (total - end) * CODE_ROW_HEIGHT }} />
      </div>
      {scrolled && (
        <button type="button" className="sv-top" onClick={scrollToTop}
          title="Jump to top of file" aria-label="Scroll to top of file">
          <UpIcon />
        </button>
      )}
    </div>
  );
}
