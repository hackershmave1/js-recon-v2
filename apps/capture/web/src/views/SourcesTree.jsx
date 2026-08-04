// SourcesTree.jsx — the left rail for the Sources view: a width-controlled, scrollable
// tree of captured JS bundles and their reconstructed files, with a collapse control
// and per-row finding-count badges. Pure renderer; all state/effects live in Sources.jsx.
import { C, F } from '../theme.js';
import { FileIcon, ChevronRight, ChevronDown, ChevronLeft } from '../icons.jsx';

export function SourcesTree({
  railWidth, subtitle, bundles, loading, hasSession, expanded, recon, sel, findings, findingsForDoc,
  onCollapse, onToggle, onPickRaw, onPickRecon
}) {
  return (
    <div style={{ width: `${railWidth}px`, flex: `0 0 ${railWidth}px`, overflowY: 'auto', padding: '18px 0' }}>
      <div style={{ padding: '0 18px 14px', display: 'flex', alignItems: 'flex-start', gap: '8px' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '18px' }}>Sources</div>
          <div style={{ fontSize: '12px', color: C.faint, marginTop: '2px' }}>{subtitle}</div>
        </div>
        <button onClick={onCollapse} title="Hide the sources panel" style={{ flex: '0 0 auto', width: '26px', height: '26px', borderRadius: '7px', border: `1px solid ${C.lineStrong}`, background: C.control, color: C.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <ChevronLeft size={13} />
        </button>
      </div>
      {bundles.length === 0 && (
        !hasSession
          ? <div style={{ padding: '24px 18px', color: C.faint, fontSize: '12.5px', lineHeight: 1.6 }}>No session selected. Capture JS with the extension or start a New Recon, then pick it from the target selector below.</div>
          : loading
            ? <LoadingRow pad="24px 18px" />
            : <div style={{ padding: '24px 18px', color: C.faint, fontSize: '12.5px', lineHeight: 1.6 }}>No JavaScript sources in this session yet.</div>
      )}
      {bundles.map((b) => {
        const count = findingsForDoc(findings, b.fileId, null).length;
        const isOpen = !!expanded[b.fileId];
        const entry = recon[b.fileId];
        const selfSel = sel && sel.fileId === b.fileId && sel.path == null;
        return (
          <div key={b.fileId}>
            <button onClick={() => (b.hasRecon ? onToggle(b) : onPickRaw(b))} style={rowStyle(selfSel, 18)}>
              <span style={{ width: '13px', flex: '0 0 13px', color: C.faint, display: 'inline-flex' }}>
                {b.hasRecon ? (isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />) : <FileIcon size={13} />}
              </span>
              <span style={{ flex: 1, minWidth: 0, color: selfSel ? C.lime : C.textSoft, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{b.label}</span>
              <Badge n={count} />
            </button>
            {b.hasRecon && isOpen && (
              <div>
                {(!entry || entry.loading) && <LoadingRow pad="6px 18px 6px 36px" />}
                {entry && entry.error && <Hint text="Sources unavailable" />}
                {entry && entry.rows && entry.rows.map((r) => {
                  const fc = findingsForDoc(findings, b.fileId, r.path).length;
                  const active = sel && sel.fileId === b.fileId && sel.path === r.path;
                  return (
                    <button key={r.path} onClick={() => onPickRecon(b.fileId, r.path)} title={r.path} style={rowStyle(active, 30 + r.depth * 12)}>
                      <span style={{ width: '13px', flex: '0 0 13px', color: active ? C.lime : C.dim, display: 'inline-flex' }}><FileIcon size={12} /></span>
                      <span style={{ flex: 1, minWidth: 0, color: active ? C.lime : C.muted, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.name}</span>
                      <Badge n={fc} />
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function rowStyle(active, padLeft) {
  return {
    width: '100%', display: 'flex', alignItems: 'center', gap: '8px',
    padding: '6px 18px', paddingLeft: `${padLeft}px`, border: 'none',
    background: active ? C.rowActive : 'transparent', cursor: 'pointer',
    fontFamily: F.mono, fontSize: '12px', textAlign: 'left'
  };
}
function Badge({ n }) {
  if (!n) return null;
  return <span style={{ fontFamily: F.body, fontSize: '9.5px', fontWeight: 700, color: C.orange, background: 'rgba(255,138,71,0.13)', padding: '1px 6px', borderRadius: '10px', flex: '0 0 auto' }}>{n}</span>;
}
function Hint({ text }) {
  return <div style={{ padding: '6px 18px 6px 36px', fontSize: '11.5px', color: C.faint, fontFamily: F.body }}>{text}</div>;
}
// Animated loader so an in-flight fetch reads as alive, not stuck. Indeterminate (a
// pulsing dot) because the file-list/reconstruct fetches don't report byte progress.
function LoadingRow({ text = 'Loading sources…', pad = '6px 18px' }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '9px', padding: pad, fontSize: '12px', color: C.faint, fontFamily: F.body }}>
      <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: C.lime, animation: 'pulse 1.1s infinite', flex: '0 0 7px' }} />
      <span>{text}</span>
    </div>
  );
}
