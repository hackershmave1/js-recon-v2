// FindingDrawer.jsx — slide-in detail panel for a single finding. Shows the value
// (with copy), a derived "what this is"/impact, scope & source, a trace snippet,
// extractor/confidence meta, and triage buttons wired to backend persistence.
import { C, F, SEV, TYPE, STATUS, CONF, SCOPE, CLS } from '../theme.js';
import { CopyIcon, CloseIcon, AlertIcon } from '../icons.jsx';

const TRIAGE = [
  { key: 'new', label: 'New' },
  { key: 'reviewed', label: 'Reviewed' },
  { key: 'confirmed', label: 'Confirmed' },
  { key: 'false_positive', label: 'False pos.' }
];

function fileShort(file) {
  return (file || '').replace('webpack://app/', '').replace('webpack://', '').replace(/^https?:\/\//, '');
}

export function FindingDrawer({ finding, status, onClose, onCopy, onTriage, onOpenSource }) {
  if (!finding) return null;
  const sev = SEV[finding.severity] || SEV.low;
  const type = TYPE[finding.kind] || TYPE.endpoint;
  const conf = CONF[finding.conf] || CONF.medium;
  const scopeMeta = SCOPE[finding.scope] || SCOPE.in;
  const clsMeta = CLS[finding.cls] || CLS.app;

  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(5,7,11,0.6)', zIndex: 40, backdropFilter: 'blur(2px)' }} />
      <aside style={{ position: 'fixed', top: 0, right: 0, height: '100vh', width: '480px', maxWidth: '100vw', background: C.panel2, borderLeft: `1px solid ${C.lineStrong}`, zIndex: 41, overflowY: 'auto', animation: 'slidein .22s cubic-bezier(.2,.8,.2,1)', boxShadow: '-20px 0 60px rgba(0,0,0,0.5)' }}>
        <div style={{ padding: '20px 24px', borderBottom: `1px solid ${C.line}`, position: 'sticky', top: 0, background: C.panel2, zIndex: 2 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '9px', marginBottom: '12px' }}>
            <span style={{ fontSize: '9.5px', fontWeight: 700, letterSpacing: '0.5px', color: sev.c, background: sev.bg, padding: '3px 9px', borderRadius: '5px' }}>{sev.label}</span>
            <span style={{ fontSize: '9.5px', fontWeight: 700, letterSpacing: '0.6px', color: type.c, border: `1px solid ${type.bd}`, padding: '2px 8px', borderRadius: '5px' }}>{type.label}</span>
            <span style={{ flex: 1 }} />
            <button onClick={onClose} style={{ width: '30px', height: '30px', borderRadius: '8px', border: `1px solid ${C.lineStrong}`, background: C.control, color: C.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><CloseIcon /></button>
          </div>
          <h2 style={{ fontFamily: F.display, fontWeight: 700, fontSize: '19px', margin: 0, letterSpacing: '-0.3px' }}>{finding.label}</h2>
        </div>

        <div style={{ padding: '20px 24px' }}>
          <Label>VALUE</Label>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', background: C.inset, border: `1px solid ${C.lineStrong}`, borderRadius: '10px', padding: '11px 13px', marginBottom: '20px' }}>
            <span style={{ flex: 1, minWidth: 0, fontFamily: F.mono, fontSize: '12.5px', color: C.lime, wordBreak: 'break-all' }}>{finding.value || '—'}</span>
            <button onClick={() => onCopy(finding.value)} style={{ flex: '0 0 auto', width: '30px', height: '30px', borderRadius: '7px', border: `1px solid ${C.lineHover}`, background: C.control, color: C.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><CopyIcon /></button>
          </div>

          <Label>WHAT THIS IS</Label>
          <p style={{ fontSize: '13px', color: C.textSoft, lineHeight: 1.65, margin: '0 0 8px' }}>{finding.description}</p>
          <div style={{ display: 'flex', alignItems: 'center', gap: '7px', padding: '10px 12px', background: sev.bg, borderRadius: '9px', marginBottom: '22px' }}>
            <span style={{ color: sev.c, flex: '0 0 15px', display: 'inline-flex' }}><AlertIcon /></span>
            <span style={{ fontSize: '12px', color: sev.c, fontWeight: 500 }}>{finding.impact}</span>
          </div>

          <Label>SCOPE &amp; SOURCE</Label>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '22px', flexWrap: 'wrap' }}>
            <Pill meta={scopeMeta} />
            <Pill meta={clsMeta} />
            <span style={{ flex: 1 }} />
            <span style={{ fontFamily: F.mono, fontSize: '11px', color: C.dim }}>{finding.origin || '—'}</span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
            <span style={{ fontSize: '10.5px', color: C.faint, fontWeight: 700, letterSpacing: '0.8px' }}>TRACE TO SOURCE</span>
            <button onClick={() => onOpenSource(finding)} style={{ fontSize: '11.5px', color: C.blue, background: 'none', border: 'none', cursor: 'pointer', fontWeight: 600 }}>Open in Sources →</button>
          </div>
          <div style={{ fontFamily: F.mono, fontSize: '11.5px', color: C.blue, marginBottom: '10px', wordBreak: 'break-all' }}>
            {fileShort(finding.file)}<span style={{ color: C.orange }}>:{finding.line}:{finding.col}</span>
          </div>
          <div style={{ background: C.inset, border: `1px solid ${C.lineStrong}`, borderRadius: '10px', overflow: 'hidden', marginBottom: '22px', fontFamily: F.mono, fontSize: '11.5px', lineHeight: 1.75 }}>
            {finding.context
              ? <div style={{ padding: '11px 13px', color: C.textSoft, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{finding.context.trim()}</div>
              : <div style={{ padding: '11px 13px', color: C.faint }}>No source snippet captured for this finding.</div>}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '22px' }}>
            <Meta title="EXTRACTOR"><span style={{ fontFamily: F.mono, color: C.textSoft }}>{finding.extractor}</span></Meta>
            <Meta title="CONFIDENCE"><span style={{ color: conf.c, fontWeight: 600 }}>{conf.label}</span></Meta>
          </div>

          <Label>TRIAGE</Label>
          <div style={{ display: 'flex', gap: '7px', flexWrap: 'wrap' }}>
            {TRIAGE.map((b) => {
              const active = (status || 'new') === b.key;
              const meta = STATUS[b.key];
              return (
                <button key={b.key} onClick={() => onTriage(finding, b.key)} style={{
                  flex: 1, minWidth: '100px', padding: '9px', borderRadius: '9px',
                  border: `1px solid ${active ? meta.c : C.lineStrong}`,
                  background: active ? meta.bg : C.control, color: active ? meta.c : C.muted,
                  cursor: 'pointer', fontSize: '12px', fontWeight: 600
                }}>{b.label}</button>
              );
            })}
          </div>
        </div>
      </aside>
    </>
  );
}

function Label({ children }) {
  return <div style={{ fontSize: '10.5px', color: C.faint, fontWeight: 700, letterSpacing: '0.8px', marginBottom: '8px' }}>{children}</div>;
}
function Pill({ meta }) {
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '6px 11px', borderRadius: '8px', border: `1px solid ${meta.bd}` }}>
      <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: meta.c }} />
      <span style={{ fontSize: '12px', color: meta.c, fontWeight: 600 }}>{meta.label}</span>
    </span>
  );
}
function Meta({ title, children }) {
  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: '10px', padding: '11px 13px' }}>
      <div style={{ fontSize: '10px', color: C.faint, marginBottom: '4px' }}>{title}</div>
      <div style={{ fontSize: '12.5px' }}>{children}</div>
    </div>
  );
}
