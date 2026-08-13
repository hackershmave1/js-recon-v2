import { useEffect, useMemo, useRef, useState } from "react";
import { highlightJsLines, type HighlightedSpan } from "./highlight";

// A minified bundle arrives as one multi-MiB line, and even the server-beautified path can be
// hundreds of thousands of lines. Rendering one DOM node per line UNVIRTUALIZED exploded to
// ~1M nodes and froze the whole machine (found in QA on a large AEM clientlib). So the viewer
// commits at most RENDER_MAX_LINES rows and clamps any single line to RENDER_MAX_CHARS; the
// full bytes stay reachable via Download, and a live note says how much is shown. Highlighting
// still runs on the FULL text (its own 200k guard applies) so spans align by absolute index.
const RENDER_MAX_LINES = 10_000;
const RENDER_MAX_CHARS = 512_000;

// Presentational: renders `text` line-by-line, capped. `marks` (line -> finding type) is null
// when the view is pretty-printed (original line numbers no longer map after reformatting).
// `focusLine` is the jumped-to line (highlighted + scrolled into view); it still applies to
// pretty-printed text even though marks don't. `canHighlight` is gauged by the caller from the
// RAW source length, not `text.length`.
export function CodeViewer({ text, truncated, marks, focusLine, canHighlight }: {
  text: string; truncated: boolean; marks: Map<number, string> | null; focusLine?: number | null; canHighlight: boolean;
}) {
  // Bound what we COMMIT to the DOM. Clamp the character span first (caps the width of a raw
  // single-line bundle), then split and cap the row count.
  const { lines, clamped, totalLines, charClamped } = useMemo(() => {
    const all = text.split("\n");
    const charClamped = text.length > RENDER_MAX_CHARS;
    const shown = (charClamped ? text.slice(0, RENDER_MAX_CHARS) : text).split("\n").slice(0, RENDER_MAX_LINES);
    return { lines: shown, totalLines: all.length, charClamped, clamped: all.length > RENDER_MAX_LINES || charClamped };
  }, [text]);

  // Lazily syntax-highlight into per-line spans. Plain text until ready and on failure (S3);
  // skipped for very large files (S2). Runs on the FULL `text`, not the clamped view.
  const [highlighted, setHighlighted] = useState<HighlightedSpan[][] | null>(null);
  useEffect(() => {
    setHighlighted(null);
    if (!canHighlight) return;
    let live = true;
    void highlightJsLines(text)
      .then((out) => { if (live) setHighlighted(out); })
      .catch(() => { /* fall back to plain text */ });
    return () => { live = false; };
  }, [text, canHighlight]);

  // Scroll the jumped-to line into view after it renders. Re-run when highlighting resolves (it
  // reflows the line). jsdom's scrollIntoView throws, so guard it. A focus line beyond the
  // render cap simply has no row to scroll to (surfaced by the large-file note).
  const focusRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (focusLine == null) return;
    try { focusRef.current?.scrollIntoView({ block: "center" }); } catch { /* jsdom no-op */ }
  }, [focusLine, text, highlighted]);

  return (
    <div className="sv-code">
      {truncated && <div className="sv-note sv-warn">File truncated — showing a capped preview.</div>}
      {clamped && (
        <div className="sv-note sv-warn">
          Large file — showing {lines.length.toLocaleString()} of {totalLines.toLocaleString()} line
          {totalLines === 1 ? "" : "s"}{charClamped ? " (long lines truncated)" : ""}. Download to view or search the full file.
        </div>
      )}
      {lines.map((line, i) => {
        const n = i + 1;
        const mark = marks?.get(n);
        const focused = focusLine === n;
        const spans = highlighted?.[i];
        return (
          <div key={n} ref={focused ? focusRef : undefined}
            className={"sv-line" + (mark ? " marked" : "") + (focused ? " focus" : "")}>
            <span className="sv-ln">{n}</span>
            <span className="sv-code-txt">
              {spans ? spans.map((s, j) => <span key={j} className={s.className}>{s.text}</span>) : line}
            </span>
            {mark && <span className="sv-mark">{mark}</span>}
          </div>
        );
      })}
    </div>
  );
}
