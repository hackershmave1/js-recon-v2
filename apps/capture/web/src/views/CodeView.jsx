// CodeView.jsx — DevTools-style source viewer: syntax-highlighted (Prism via
// syntax.js), line-numbered, with per-line finding highlights, an in-file find bar
// (Cmd/Ctrl+F) with match navigation, and go-to-line. The focused line (from
// "Open in Sources") is scrolled into view.
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { C, F, SEV } from '../theme.js';
import { highlightLines, lineText, matchRanges, TOKEN_COLOR } from '../syntax.js';
import { CloseIcon, ChevronDown } from '../icons.jsx';

// Guard against pathological inputs: minified bundles arrive as one multi-megabyte
// line (soft-wrap it), and a runaway reconstructed file could blow up the DOM (cap).
const MAX_LINES = 6000;
const colorOf = (type) => TOKEN_COLOR[type] || C.textSoft;

export function CodeView({ content, byLine, focusLine, path, onPickFinding }) {
  const [findOpen, setFindOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const activeRef = useRef(null);
  const inputRef = useRef(null);
  const scrollRef = useRef(null);
  const lineRefs = useRef({});

  const doc = useMemo(() => highlightLines(content || '', path), [content, path]);
  const allLines = doc.lines;
  const minified = allLines.length <= 2 && (content || '').length > 2000;
  const truncated = allLines.length > MAX_LINES;
  const lines = truncated ? allLines.slice(0, MAX_LINES) : allLines;
  const rawLines = useMemo(() => lines.map(lineText), [lines]);

  // Flat, ordered list of all matches across the visible lines.
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    const out = [];
    rawLines.forEach((text, i) => matchRanges(text, q).forEach((r) => out.push({ line: i + 1, start: r[0], end: r[1] })));
    return out;
  }, [rawLines, query]);

  // Keep the active index in range as matches change; reset to first on new query.
  useEffect(() => { setActive(0); }, [query]);
  const activeMatch = matches[active] || null;

  // Per-line match ranges (with active flag) for inline highlighting.
  const rangesByLine = useMemo(() => {
    const map = {};
    matches.forEach((m, i) => {
      (map[m.line] = map[m.line] || []).push({ start: m.start, end: m.end, active: i === active });
    });
    return map;
  }, [matches, active]);

  // Scroll the focused (deep-linked) line into view. Reads the current line element
  // from lineRefs (rather than a parallel ref) so a file switch can't scroll a stale node.
  useEffect(() => {
    const el = focusLine && lineRefs.current[focusLine];
    if (el) el.scrollIntoView({ block: 'center' });
  }, [focusLine, content]);
  // Scroll the active search match into view as the user steps through matches, then
  // return focus to the find input. Stepping (Enter or the prev/next buttons) re-renders
  // and the buttons steal focus; without this the bar feels "gone" because a second Enter
  // does nothing. preventScroll keeps focusing the (overlay) input from nudging the page.
  useEffect(() => {
    if (activeRef.current) activeRef.current.scrollIntoView({ block: 'center' });
    if (findOpen && inputRef.current) inputRef.current.focus({ preventScroll: true });
  }, [active, matches, findOpen]);

  // Cmd/Ctrl+F opens find (intercepting the browser's own find while a file is open);
  // Esc closes it. Enter / Shift+Enter step through matches from inside the input.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault(); setFindOpen(true);
        setTimeout(() => inputRef.current && inputRef.current.select(), 0);
      } else if (e.key === 'Escape' && findOpen) {
        e.stopPropagation(); setFindOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [findOpen]);

  const step = (dir) => { if (matches.length) setActive((a) => (a + dir + matches.length) % matches.length); };
  const gotoLine = (n) => {
    const clamped = Math.min(lines.length, Math.max(1, n | 0));
    const el = lineRefs.current[clamped];
    if (el) el.scrollIntoView({ block: 'center' });
  };

  if (content == null) {
    return <Empty text="Could not load this file's content (it may have been purged by retention)." />;
  }

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', background: C.inset }}>
      {/* The find bar sits above the scroll container (not inside it), so it stays pinned
          and usable while you step through matches and scroll the code — and reserves its
          own height, so no manual top-padding on the code list is needed. */}
      {findOpen && (
        <FindBar
          inputRef={inputRef} query={query} setQuery={setQuery}
          count={matches.length} active={active} step={step}
          onGoto={gotoLine} onClose={() => setFindOpen(false)} maxLine={lines.length}
        />
      )}
      <div ref={scrollRef} style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
      <div style={{ display: 'table', width: '100%', fontFamily: F.mono, fontSize: '12.5px', lineHeight: 1.7 }}>
        {lines.map((segs, i) => {
          const n = i + 1;
          const hits = byLine[n];
          const sev = hits ? (SEV[hits[0].severity] || SEV.low) : null;
          const focused = n === focusLine;
          const ranges = rangesByLine[n];
          return (
            <div key={n} ref={(el) => { lineRefs.current[n] = el; }}
              style={{ display: 'table-row', background: focused ? 'rgba(107,168,255,0.16)' : (sev ? sev.bg : 'transparent') }}>
              <div style={{ display: 'table-cell', width: '52px', minWidth: '52px', textAlign: 'right', padding: '0 12px 0 14px', color: focused ? C.blue : (sev ? sev.c : C.faint), userSelect: 'none', borderLeft: `3px solid ${sev ? sev.c : 'transparent'}`, verticalAlign: 'top' }}>{n}</div>
              <div style={{ display: 'table-cell', padding: '0 16px 0 4px', whiteSpace: minified ? 'pre-wrap' : 'pre', wordBreak: minified ? 'break-all' : 'normal', verticalAlign: 'top' }}>
                {renderCode(segs, ranges, activeRef)}
                {hits && (
                  <span onClick={() => onPickFinding && onPickFinding(hits[0])} title="View finding" style={{ marginLeft: '12px', cursor: 'pointer', fontFamily: F.body, fontSize: '10.5px', fontWeight: 700, letterSpacing: '0.4px', color: sev.c, background: sev.bg, padding: '1px 7px', borderRadius: '10px', whiteSpace: 'nowrap' }}>
                    {hits.length > 1 ? `${hits.length} findings` : hits[0].label}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {truncated && (
        <div style={{ padding: '14px 18px', color: C.faint, fontFamily: F.body, fontSize: '12px' }}>
          … {allLines.length - MAX_LINES} more lines truncated — use Download to view the full file.
        </div>
      )}
      </div>
    </div>
  );
}

// Render one line's coloured segments, splitting at find-match ranges so matches get a
// highlight (the active match stronger + ref'd for scroll). Fast path when no matches.
function renderCode(segs, ranges, activeRef) {
  if (!ranges || !ranges.length) {
    return segs.map((s, i) => <span key={i} style={{ color: colorOf(s.type) }}>{s.text || ' '}</span>);
  }
  const out = [];
  let pos = 0; let key = 0;
  for (const s of segs) {
    const segStart = pos; const segEnd = pos + s.text.length; const color = colorOf(s.type);
    let cursor = segStart;
    for (const r of ranges) {
      if (r.end <= segStart || r.start >= segEnd) continue;
      const a = Math.max(r.start, segStart); const b = Math.min(r.end, segEnd);
      if (a > cursor) out.push(<span key={key++} style={{ color }}>{s.text.slice(cursor - segStart, a - segStart)}</span>);
      out.push(
        <span key={key++} ref={r.active ? activeRef : null}
          style={{ color: r.active ? C.onLime : color, background: r.active ? C.amber : 'rgba(255,199,61,0.32)', borderRadius: '2px' }}>
          {s.text.slice(a - segStart, b - segStart)}
        </span>
      );
      cursor = b;
    }
    if (cursor < segEnd) out.push(<span key={key++} style={{ color }}>{s.text.slice(cursor - segStart)}</span>);
    pos = segEnd;
  }
  return out;
}

function FindBar({ inputRef, query, setQuery, count, active, step, onGoto, onClose, maxLine }) {
  const btn = { width: '24px', height: '24px', borderRadius: '6px', border: `1px solid ${C.lineStrong}`, background: C.control, color: C.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' };
  return (
    <div style={{ flex: '0 0 auto', display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 12px', background: C.panel2, borderBottom: `1px solid ${C.lineStrong}`, boxShadow: '0 6px 16px rgba(0,0,0,0.35)' }}>
      <input ref={inputRef} value={query} placeholder="Find in file…" data-find-in-file autofocus
        onInput={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); step(e.shiftKey ? -1 : 1); } if (e.key === 'Escape') onClose(); }}
        style={{ width: '220px', background: C.inset, border: `1px solid ${C.lineStrong}`, borderRadius: '7px', color: C.text, fontFamily: F.mono, fontSize: '12.5px', padding: '6px 10px', outline: 'none' }} />
      <span style={{ fontFamily: F.mono, fontSize: '11.5px', color: C.faint, minWidth: '54px' }}>
        {count ? `${active + 1} / ${count}` : (query ? 'no matches' : '')}
      </span>
      <button onClick={() => step(-1)} title="Previous (Shift+Enter)" disabled={!count} style={btn}><ChevronUp /></button>
      <button onClick={() => step(1)} title="Next (Enter)" disabled={!count} style={btn}><ChevronDown size={13} /></button>
      <span style={{ width: '1px', height: '20px', background: C.lineStrong, margin: '0 2px' }} />
      <input type="number" min="1" max={maxLine} placeholder="Ln" title="Go to line"
        onKeyDown={(e) => { if (e.key === 'Enter') { const n = parseInt(e.target.value, 10); if (n) onGoto(n); } }}
        style={{ width: '58px', background: C.inset, border: `1px solid ${C.lineStrong}`, borderRadius: '7px', color: C.text, fontFamily: F.mono, fontSize: '12.5px', padding: '6px 8px', outline: 'none' }} />
      <span style={{ flex: 1 }} />
      <button onClick={onClose} title="Close (Esc)" style={btn}><CloseIcon size={13} /></button>
    </div>
  );
}

function ChevronUp() {
  return <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m18 15-6-6-6 6" /></svg>;
}

function Empty({ text }) {
  return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: C.inset, padding: '40px' }}>
      <div style={{ maxWidth: '320px', textAlign: 'center', color: C.faint, fontSize: '13px', lineHeight: 1.6 }}>{text}</div>
    </div>
  );
}
