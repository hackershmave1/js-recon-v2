// SearchPalette.jsx — ⌘K command palette. Substring search over the active session's
// findings (secrets + endpoints), keyboard-navigable. Selecting a result jumps to the
// Findings drawer, or Sources when the finding has a reconstructed file. Search logic
// lives in transforms.searchFindings; this owns only query + selection-index state.
import { useState, useMemo, useEffect, useRef } from 'preact/hooks';
import { C, F } from '../theme.js';
import { SearchIcon } from '../icons.jsx';
import { searchFindings } from '../transforms.overlays.js';

export function SearchPalette({ findings, onClose, onPick }) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef(null);

  const results = useMemo(() => searchFindings(findings, query, 8), [findings, query]);
  useEffect(() => { setActive(0); }, [query]);
  useEffect(() => { inputRef.current && inputRef.current.focus(); }, []);

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(results.length - 1, i + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (results[active]) onPick(results[active].finding); }
  };

  const stop = (e) => e.stopPropagation();
  const empty = query.trim() && results.length === 0;
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(5,7,11,0.7)', zIndex: 50, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', paddingTop: '90px', backdropFilter: 'blur(3px)' }}>
      <div onClick={stop} style={{ width: '620px', maxWidth: '90vw', background: C.panel2, border: `1px solid ${C.lineStrong}`, borderRadius: '16px', overflow: 'hidden', boxShadow: '0 30px 80px rgba(0,0,0,0.6)', animation: 'dropin .18s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '16px 20px', borderBottom: `1px solid ${C.line}` }}>
          <span style={{ color: C.lime, display: 'inline-flex' }}><SearchIcon size={18} /></span>
          <input ref={inputRef} value={query} onInput={(e) => setQuery(e.target.value)} onKeyDown={onKeyDown}
            placeholder="Search findings, endpoints, files in this session…"
            style={{ flex: 1, background: 'none', border: 'none', outline: 'none', color: C.text, fontFamily: F.body, fontSize: '15px' }} />
          <kbd style={{ fontFamily: F.mono, fontSize: '10.5px', color: C.dim, border: `1px solid ${C.lineHover}`, borderRadius: '5px', padding: '2px 7px' }}>ESC</kbd>
        </div>
        <div style={{ maxHeight: '380px', overflowY: 'auto', padding: '8px' }}>
          {!query.trim() && (
            <div style={{ padding: '34px', textAlign: 'center', color: C.faint, fontSize: '13px' }}>Type to search this session's findings.</div>
          )}
          {empty && (
            <div style={{ padding: '40px', textAlign: 'center', color: C.faint, fontSize: '13px' }}>No matches for “{query}”</div>
          )}
          {results.map((r, i) => (
            <button key={r.fingerprint} onMouseEnter={() => setActive(i)} onClick={() => onPick(r.finding)}
              style={{ width: '100%', display: 'flex', alignItems: 'center', gap: '12px', padding: '11px 13px', border: 'none', background: i === active ? C.rowActive : 'none', cursor: 'pointer', textAlign: 'left', borderRadius: '9px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.6px', color: r.type.c, border: `1px solid ${r.type.bd}`, padding: '2px 7px', borderRadius: '5px', flex: '0 0 auto' }}>{r.type.label}</span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontSize: '13px', color: C.text, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.label}</span>
                <span style={{ display: 'block', fontFamily: F.mono, fontSize: '11px', color: C.dim, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.fileLine}</span>
              </span>
              <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: r.sev.c, flex: '0 0 7px' }} />
            </button>
          ))}
        </div>
        <div style={{ padding: '10px 18px', borderTop: `1px solid ${C.line}`, display: 'flex', gap: '16px', fontSize: '11px', color: C.faint, fontFamily: F.mono }}>
          <span>↵ open</span><span>↑↓ navigate</span><span>esc close</span>
        </div>
      </div>
    </div>
  );
}
