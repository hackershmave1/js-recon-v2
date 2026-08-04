// Topbar.jsx — search trigger, Export, New Recon. Mirrors the prototype HEADER.
import { C, F } from '../theme.js';
import { SearchIcon, ExportIcon, PlusIcon } from '../icons.jsx';

export function Topbar({ onSearch, onExport, onNewRecon }) {
  return (
    <header style={{ height: '60px', flex: '0 0 60px', display: 'flex', alignItems: 'center', gap: '14px', padding: '0 22px', borderBottom: `1px solid ${C.line}`, background: 'rgba(15,18,26,0.6)', backdropFilter: 'blur(8px)' }}>
      <div style={{ flex: 1, maxWidth: '520px' }}>
        <button onClick={onSearch} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: '10px', padding: '9px 13px', borderRadius: '10px', border: `1px solid ${C.lineStrong}`, background: C.panel, cursor: 'text', color: C.faint }}>
          <SearchIcon />
          <span style={{ flex: 1, textAlign: 'left', fontSize: '13px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>Search findings, endpoints, files…</span>
          <kbd style={{ fontFamily: F.mono, fontSize: '10.5px', color: C.dim, border: `1px solid ${C.lineHover}`, borderRadius: '5px', padding: '1px 6px', background: C.control }}>⌘K</kbd>
        </button>
      </div>
      <div style={{ flex: 1 }} />
      <button onClick={onExport} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '9px 14px', borderRadius: '10px', border: `1px solid ${C.lineHover}`, background: C.control, color: C.textSoft, cursor: 'pointer', fontSize: '13px', fontWeight: 600 }}>
        <ExportIcon />Export
      </button>
      <button onClick={onNewRecon} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '9px 16px', borderRadius: '10px', border: 'none', background: C.lime, color: C.onLime, cursor: 'pointer', fontSize: '13px', fontWeight: 700, boxShadow: '0 0 20px rgba(205,235,69,0.25)' }}>
        <PlusIcon />New Recon
      </button>
    </header>
  );
}
