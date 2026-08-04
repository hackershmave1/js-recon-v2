// Sources.jsx — reconstructed-source browser: a left bundle/file tree rail and a
// code viewer with finding highlights. The tree is grouped by captured JS bundle;
// each bundle's reconstructed sources are fetched lazily on expand (the API
// re-processes the sourcemap per call, so session-wide prefetch is avoided).
// Bundles without reconstructed sources fall back to their raw /content body.
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { C, F } from '../theme.js';
import * as api from '../api.js';
import {
  isJsBundle, hasReconstructed, bundleLabel, sortedSourceRows, findingsForDoc, findingsByLine
} from '../transforms.js';
import { ChevronRight, DownloadIcon } from '../icons.jsx';
import { CodeView } from './CodeView.jsx';
import { SourcesTree } from './SourcesTree.jsx';

// Reconstructed/raw source caches hoisted to MODULE scope so they survive both leaving the
// Sources tab (unmount) and switching sessions. Keyed by fileId, which is globally unique
// across sessions, so entries never collide; a file's reconstructed source is immutable, so
// a cached entry is always valid. Without this, the API re-processed every sourcemap on each
// visit (slow on the single worker) — the "reloads everything each time" complaint. Unbounded
// by design for this local single-user tool; a size cap is a possible future refinement.
const RECON_CACHE = {};   // fileId -> {files,rows} | {error}
const RAW_CACHE = {};     // fileId -> {content}

function download(name, content) {
  const blob = new Blob([content || ''], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = (name || 'source.txt').split('/').pop();
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Tree-rail sizing. Below COLLAPSE_AT a drag snaps the rail closed; width persists.
const RAIL_MIN = 200, RAIL_MAX = 620, RAIL_DEFAULT = 300, COLLAPSE_AT = 150;
const RAIL_KEY = 'recon.sources.railWidth';
function readRailWidth() {
  const n = parseInt(localStorage.getItem(RAIL_KEY) || '', 10);
  return Number.isFinite(n) ? Math.min(RAIL_MAX, Math.max(RAIL_MIN, n)) : RAIL_DEFAULT;
}

export function Sources({ sessionId, findings, openTarget, onClearTarget, onPickFinding }) {
  const [files, setFiles] = useState([]);
  const [filesLoading, setFilesLoading] = useState(false);   // true while the session file list is in flight
  const [expanded, setExpanded] = useState({});
  const [extra, setExtra] = useState([]);        // synthetic bundles (dedupe-mismatch fallback)
  const [recon, setRecon] = useState(() => ({ ...RECON_CACHE }));  // fileId -> {loading}|{files,rows}|{error}
  const [raw, setRaw] = useState(() => ({ ...RAW_CACHE }));        // fileId -> {loading}|{content}
  const [sel, setSel] = useState(null);          // {fileId, path|null}
  const [focusLine, setFocusLine] = useState(0);
  // Resizable/collapsible tree rail. Drag the divider to resize; drag past the
  // collapse threshold (or use the header chevron) to hide the tree entirely so the
  // code pane is full-width, with a restore button to bring it back.
  const [railWidth, setRailWidth] = useState(readRailWidth);
  const [collapsed, setCollapsed] = useState(false);
  const splitRef = useRef(null);

  const reconRef = useRef({ ...RECON_CACHE });
  const rawRef = useRef({ ...RAW_CACHE });
  const didDefault = useRef(false);
  // Write-through to the module cache (persists across unmount + session switch) as well as
  // the ref (sync source of truth for in-flight checks) and the mirror state (drives render).
  const setReconEntry = (id, e) => { RECON_CACHE[id] = e; reconRef.current = { ...reconRef.current, [id]: e }; setRecon(reconRef.current); };
  const setRawEntry = (id, e) => { RAW_CACHE[id] = e; rawRef.current = { ...rawRef.current, [id]: e }; setRaw(rawRef.current); };

  // Divider drag: track the pointer against the split container's left edge. Below
  // the collapse threshold the rail snaps shut; otherwise the clamped width persists.
  const startResize = (e) => {
    e.preventDefault();
    const left = splitRef.current ? splitRef.current.getBoundingClientRect().left : 0;
    const onMove = (ev) => {
      const w = ev.clientX - left;
      if (w < COLLAPSE_AT) { setCollapsed(true); return; }
      setCollapsed(false);
      setRailWidth(Math.min(RAIL_MAX, Math.max(RAIL_MIN, w)));
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.userSelect = '';
      setRailWidth((w) => { try { localStorage.setItem(RAIL_KEY, String(w)); } catch (err) { /* private mode */ } return w; });
    };
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  // bundles: captured JS files (+ any synthetic fallbacks), reconstructed first.
  const bundles = useMemo(() => {
    const base = (files || []).filter(isJsBundle).map((f) => ({
      fileId: f.id, url: f.url, label: bundleLabel(f.url),
      hasRecon: hasReconstructed(f), reconCount: f.sourceMap?.reconstructedFilesCount || 0
    }));
    const merged = [...base];
    extra.forEach((s) => { if (!merged.some((b) => b.fileId === s.fileId)) merged.push(s); });
    return merged.sort((a, b) => (b.hasRecon - a.hasRecon) || (b.reconCount - a.reconCount));
  }, [files, extra]);

  const reconTotal = bundles.reduce((n, b) => n + (b.hasRecon ? b.reconCount : 0), 0);
  const subtitle = filesLoading && bundles.length === 0
    ? 'Loading…'
    : reconTotal > 0
      ? `${reconTotal} files · reconstructed from source maps`
      : `${bundles.length} JS bundle${bundles.length === 1 ? '' : 's'} captured`;

  async function openBundle(b, opts = {}) {
    const { targetPath = null, line = 0, select = false } = opts;
    setExpanded((e) => ({ ...e, [b.fileId]: true }));
    if (b.hasRecon) {
      let entry = reconRef.current[b.fileId];
      if (!entry) {
        setReconEntry(b.fileId, { loading: true });
        const data = await api.getReconstructedSources(b.fileId);
        entry = data && Array.isArray(data.files)
          ? { files: data.files, rows: sortedSourceRows(data.files) }
          : { error: true, files: [], rows: [] };
        setReconEntry(b.fileId, entry);
      } else if (entry.loading) {
        return;
      }
      if (select) {
        const rows = entry.rows || [];
        const path = (targetPath && rows.some((r) => r.path === targetPath)) ? targetPath : (rows[0] && rows[0].path);
        if (path != null) { setSel({ fileId: b.fileId, path }); setFocusLine(line || 0); }
      }
    } else {
      if (select) { setSel({ fileId: b.fileId, path: null }); setFocusLine(line || 0); }
      ensureRaw(b.fileId);
    }
  }

  async function ensureRaw(fileId) {
    if (rawRef.current[fileId] !== undefined) return;
    setRawEntry(fileId, { loading: true });
    const content = await api.getFileContent(fileId);
    setRawEntry(fileId, { content });
  }

  function toggle(b) {
    if (expanded[b.fileId]) { setExpanded((e) => ({ ...e, [b.fileId]: false })); return; }
    openBundle(b);
  }
  function pickRecon(fileId, path) { setSel({ fileId, path }); setFocusLine(0); }
  function pickRaw(b) { setSel({ fileId: b.fileId, path: null }); setFocusLine(0); ensureRaw(b.fileId); }

  // load session files when the target changes; reset only the per-session VIEW state and
  // keep the reconstructed/raw caches (module + ref + mirror) so returning to a session
  // doesn't re-reconstruct every bundle. fileIds are globally unique, so the retained cache
  // never mis-serves another session.
  useEffect(() => {
    didDefault.current = false;
    setExpanded({}); setExtra([]); setSel(null); setFocusLine(0); setFiles([]);
    if (!sessionId) { setFilesLoading(false); return; }
    let alive = true;
    setFilesLoading(true);
    // The files list is fetched once per session. On the single-worker API a transient
    // failure (timeout under load) returns null; without a retry the tree would stay
    // permanently empty since sessionId never changes again. Retry a few times with
    // backoff, distinguishing a real empty array (done) from a failed fetch (null).
    (async () => {
      for (let attempt = 0; attempt < 4 && alive; attempt += 1) {
        const list = await api.getSessionFiles(sessionId);
        if (!alive) return;
        if (Array.isArray(list)) { setFiles(list); setFilesLoading(false); return; }
        await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
      }
      if (alive) setFilesLoading(false);   // retries exhausted — stop showing the loader
    })();
    return () => { alive = false; };
  }, [sessionId]);

  // honour "Open in Sources →": expand the finding's bundle and focus its line
  useEffect(() => {
    if (!openTarget || !files.length) return;
    const b = bundles.find((x) => x.fileId === openTarget.fileId);
    if (b) {
      openBundle(b, { select: true, targetPath: openTarget.file, line: openTarget.line });
    } else if (openTarget.fileId) {
      const synth = { fileId: openTarget.fileId, url: openTarget.file, label: bundleLabel(openTarget.file), hasRecon: true, reconCount: 0 };
      setExtra((xs) => (xs.some((s) => s.fileId === synth.fileId) ? xs : [...xs, synth]));
      openBundle(synth, { select: true, targetPath: openTarget.file, line: openTarget.line });
    }
    // Set before clearing the target: onClearTarget nulls openTarget in the parent,
    // which re-runs both selection effects — didDefault must already be true so the
    // default-select effect below bails instead of overriding the deep-linked file.
    didDefault.current = true;
    onClearTarget && onClearTarget();
  }, [openTarget, bundles]);

  // default selection: open the largest reconstructed bundle on first load
  useEffect(() => {
    if (openTarget || didDefault.current || !bundles.length) return;
    didDefault.current = true;
    openBundle(bundles[0], { select: true });
  }, [bundles, openTarget]);

  const docFindings = sel ? findingsForDoc(findings, sel.fileId, sel.path) : [];
  const byLine = findingsByLine(docFindings);
  const selPath = sel ? (sel.path != null ? sel.path : (bundles.find((b) => b.fileId === sel.fileId)?.url || sel.fileId)) : '';
  // Read the selected document from state (not the refs) so the content pane
  // re-renders when a lazy fetch resolves; the refs exist only for the in-flight
  // checks inside openBundle/ensureRaw, where a synchronous read is needed.
  const selEntry = sel ? (sel.path != null ? recon[sel.fileId] : raw[sel.fileId]) : null;
  const loading = !!(selEntry && selEntry.loading);
  let content = null;
  if (sel && !loading) {
    if (sel.path != null) {
      const f = (selEntry?.files || []).find((x) => x.path === sel.path);
      content = f ? f.content : null;
    } else {
      content = selEntry ? selEntry.content : null;
    }
  }

  return (
    <div ref={splitRef} style={{ display: 'flex', height: '100%', position: 'relative', animation: 'dropin .25s ease' }}>
      {/* tree rail (hidden when collapsed → code pane goes full-width) */}
      {!collapsed && (
        <SourcesTree
          railWidth={railWidth} subtitle={subtitle} bundles={bundles} loading={filesLoading}
          expanded={expanded} recon={recon} sel={sel} findings={findings}
          findingsForDoc={findingsForDoc} onCollapse={() => setCollapsed(true)}
          onToggle={toggle} onPickRaw={pickRaw} onPickRecon={pickRecon}
        />
      )}

      {/* drag divider — resize the rail; drag past the threshold to collapse it */}
      {!collapsed && (
        <div onMouseDown={startResize} title="Drag to resize · drag left to hide"
          style={{ flex: '0 0 6px', cursor: 'col-resize', background: C.line }} />
      )}

      {/* code viewer */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', position: 'relative' }}>
        {!sel ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: C.inset, color: C.faint, fontSize: '13px' }}>
            {collapsed && <RestoreRailButton onClick={() => setCollapsed(false)} />}
            Select a file to view its source.
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '14px 22px', borderBottom: `1px solid ${C.line}` }}>
              {collapsed && (
                <button onClick={() => setCollapsed(false)} title="Show the sources panel" style={{ flex: '0 0 auto', width: '28px', height: '28px', borderRadius: '8px', border: `1px solid ${C.lineStrong}`, background: C.control, color: C.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <ChevronRight size={14} />
                </button>
              )}
              <span style={{ fontFamily: F.mono, fontSize: '12.5px', color: C.textSoft, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{selPath}</span>
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: '11.5px', fontWeight: 600, padding: '3px 10px', borderRadius: '20px', color: docFindings.length ? C.orange : C.faint, background: docFindings.length ? 'rgba(255,138,71,0.1)' : 'transparent' }}>
                {docFindings.length} findings in this file
              </span>
              <button onClick={() => download(selPath, content)} disabled={content == null} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '7px 12px', borderRadius: '8px', border: `1px solid ${C.lineStrong}`, background: C.panel, color: content == null ? C.faint : C.muted, cursor: content == null ? 'default' : 'pointer', fontFamily: F.body, fontSize: '12px', fontWeight: 600 }}>
                <DownloadIcon />Download
              </button>
            </div>
            {loading
              ? <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: C.inset, color: C.faint, fontSize: '13px' }}>Loading…</div>
              : <CodeView content={content} byLine={byLine} focusLine={focusLine} path={selPath} onPickFinding={onPickFinding} />}
          </>
        )}
      </div>
    </div>
  );
}

function RestoreRailButton({ onClick }) {
  return (
    <button onClick={onClick} title="Show the sources panel"
      style={{ position: 'absolute', top: '12px', left: '12px', display: 'flex', alignItems: 'center', gap: '6px', padding: '6px 11px', borderRadius: '8px', border: `1px solid ${C.lineStrong}`, background: C.panel, color: C.muted, cursor: 'pointer', fontFamily: F.body, fontSize: '12px', fontWeight: 600 }}>
      <ChevronRight size={13} />Sources
    </button>
  );
}
