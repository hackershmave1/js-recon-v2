// ExportModal.jsx — export the current findings as JSON or CSV. No backend export
// endpoint exists, so we serialize the in-memory findings array client-side
// (transforms.findingsToJson / findingsToCsv) and trigger a Blob download.
import { useState } from 'preact/hooks';
import { C, F } from '../theme.js';
import { CloseIcon, DownloadIcon } from '../icons.jsx';
import { findingsToJson, findingsToCsv } from '../transforms.overlays.js';

const FORMATS = [
  { key: 'json', ext: 'JSON', label: 'JSON', desc: 'Structured, full fields', iconbg: 'rgba(124,140,255,0.13)', iconc: C.indigo },
  { key: 'csv', ext: 'CSV', label: 'CSV', desc: 'Spreadsheet-friendly', iconbg: 'rgba(91,214,192,0.13)', iconc: C.teal }
];

function download(name, text, mime) {
  const blob = new Blob([text], { type: mime });
  const href = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 0);
}

export function ExportModal({ findings, target, onClose, onDone }) {
  const [format, setFormat] = useState('json');
  const count = (findings || []).length;

  const doExport = () => {
    const host = (target || 'findings').replace(/[^a-z0-9.-]+/gi, '_');
    const stamp = new Date().toISOString().slice(0, 10);
    if (format === 'csv') download(`recon-${host}-${stamp}.csv`, findingsToCsv(findings), 'text/csv');
    else download(`recon-${host}-${stamp}.json`, findingsToJson(findings, { target }), 'application/json');
    onDone(`Exported ${count} findings as ${format.toUpperCase()}`);
  };

  const stop = (e) => e.stopPropagation();
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(5,7,11,0.72)', zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px', backdropFilter: 'blur(3px)' }}>
      <div onClick={stop} style={{ width: '540px', maxWidth: '94vw', background: C.panel2, border: `1px solid ${C.lineStrong}`, borderRadius: '16px', overflow: 'hidden', boxShadow: '0 30px 80px rgba(0,0,0,0.6)', animation: 'dropin .18s ease' }}>
        <div style={{ padding: '20px 24px', borderBottom: `1px solid ${C.line}`, display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
          <div style={{ flex: 1 }}>
            <h2 style={{ fontFamily: F.display, fontWeight: 700, fontSize: '19px', margin: 0 }}>Export Findings</h2>
            <div style={{ fontSize: '12.5px', color: C.faint, marginTop: '3px' }}>{count} findings{target ? ` · ${target}` : ''}</div>
          </div>
          <button onClick={onClose} style={{ width: '30px', height: '30px', borderRadius: '8px', border: `1px solid ${C.lineStrong}`, background: C.control, color: C.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <CloseIcon />
          </button>
        </div>
        <div style={{ padding: '22px 24px' }}>
          <label style={{ display: 'block', fontSize: '11px', color: C.muted, fontWeight: 700, letterSpacing: '0.5px', marginBottom: '10px' }}>FORMAT</label>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '9px' }}>
            {FORMATS.map((f) => {
              const on = format === f.key;
              return (
                <button key={f.key} onClick={() => setFormat(f.key)} style={{ display: 'flex', alignItems: 'center', gap: '11px', padding: '13px 14px', borderRadius: '11px', border: `1px solid ${on ? C.lime : C.lineStrong}`, background: on ? 'rgba(205,235,69,0.08)' : C.inset, cursor: 'pointer', textAlign: 'left' }}>
                  <span style={{ width: '30px', height: '30px', borderRadius: '8px', background: f.iconbg, color: f.iconc, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 30px', fontFamily: F.mono, fontWeight: 700, fontSize: '10px' }}>{f.ext}</span>
                  <span>
                    <span style={{ display: 'block', fontSize: '13px', color: C.text, fontWeight: 600 }}>{f.label}</span>
                    <span style={{ display: 'block', fontSize: '10.5px', color: C.faint }}>{f.desc}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '10px', padding: '16px 24px', borderTop: `1px solid ${C.line}` }}>
          <button onClick={onClose} style={{ flex: '0 0 auto', padding: '11px 18px', borderRadius: '10px', border: `1px solid ${C.lineStrong}`, background: 'none', color: C.muted, cursor: 'pointer', fontSize: '13px', fontWeight: 600 }}>Cancel</button>
          <button onClick={doExport} disabled={count === 0} style={{ flex: 1, padding: '11px', borderRadius: '10px', border: 'none', background: count ? C.lime : C.control, color: count ? C.onLime : C.faint, cursor: count ? 'pointer' : 'not-allowed', fontSize: '13.5px', fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }}>
            <DownloadIcon size={15} />Generate {format.toUpperCase()}
          </button>
        </div>
      </div>
    </div>
  );
}
