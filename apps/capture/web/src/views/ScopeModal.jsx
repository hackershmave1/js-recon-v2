// ScopeModal.jsx — edit a session's scope: its root domain(s) and whether their
// subdomains count as in-scope. Roots are entered one per line (or comma-separated);
// the backend normalizes them (strips scheme/www, de-dupes). PATCH /api/sessions/{id}.
import { useState } from 'preact/hooks';
import { C, F } from '../theme.js';
import { CloseIcon, CheckIcon, FocusIcon } from '../icons.jsx';
import { parseDomainList } from '../scopeImport.js';
import { DomainListInput } from './DomainListInput.jsx';

export function ScopeModal({ session, onClose, onSave, busy }) {
  const [rootsText, setRootsText] = useState((session.rootDomains || []).join('\n'));
  const [includeSubdomains, setIncludeSubdomains] = useState(session.includeSubdomains !== false);
  const roots = parseDomainList(rootsText);

  const submit = () => { if (!busy) onSave(roots, includeSubdomains); };
  const stop = (e) => e.stopPropagation();

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(5,7,11,0.72)', zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px', backdropFilter: 'blur(3px)' }}>
      <div onClick={stop} style={{ width: '520px', maxWidth: '94vw', background: C.panel2, border: `1px solid ${C.lineStrong}`, borderRadius: '16px', overflow: 'hidden', boxShadow: '0 30px 80px rgba(0,0,0,0.6)', animation: 'dropin .18s ease' }}>
        <div style={{ padding: '20px 24px', borderBottom: `1px solid ${C.line}`, display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
          <div style={{ flex: 1 }}>
            <h2 style={{ fontFamily: F.display, fontWeight: 700, fontSize: '19px', margin: 0 }}>Session Scope</h2>
            <div style={{ fontSize: '12.5px', color: C.faint, marginTop: '3px', fontFamily: F.mono, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{session.host}</div>
          </div>
          <button onClick={onClose} style={{ width: '30px', height: '30px', borderRadius: '8px', border: `1px solid ${C.lineStrong}`, background: C.control, color: C.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <CloseIcon />
          </button>
        </div>

        <div style={{ padding: '22px 24px' }}>
          <label style={{ display: 'block', fontSize: '11px', color: C.muted, fontWeight: 700, letterSpacing: '0.5px', marginBottom: '8px' }}>ROOT DOMAINS</label>
          <DomainListInput value={rootsText} onChange={setRootsText} rows={5} placeholder={'app.target.com\napi.target.com'} />
          <div style={{ fontSize: '11px', color: C.faint, marginTop: '6px' }}>Paste or upload a list — JSON, CSV, one-per-line, or comma-separated. Scheme, port and <code>www.</code> are handled automatically.</div>

          <label style={{ display: 'block', fontSize: '11px', color: C.muted, fontWeight: 700, letterSpacing: '0.5px', margin: '18px 0 10px' }}>SUBDOMAINS</label>
          <button onClick={() => setIncludeSubdomains(!includeSubdomains)} style={{ display: 'flex', alignItems: 'center', gap: '9px', padding: '10px 13px', borderRadius: '9px', border: `1px solid ${includeSubdomains ? 'rgba(205,235,69,0.4)' : C.lineStrong}`, background: includeSubdomains ? 'rgba(205,235,69,0.1)' : C.inset, color: includeSubdomains ? C.lime : C.muted, cursor: 'pointer', fontSize: '12.5px', fontWeight: 600 }}>
            <span style={{ width: '13px', height: '13px', borderRadius: '4px', border: `1.5px solid ${includeSubdomains ? C.lime : C.lineHover}`, background: includeSubdomains ? C.lime : 'transparent', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: C.onLime, flex: '0 0 13px' }}>
              {includeSubdomains && <CheckIcon size={9} />}
            </span>
            Include subdomains in scope
          </button>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '16px', padding: '10px 13px', background: 'rgba(91,214,192,0.07)', border: '1px solid rgba(91,214,192,0.2)', borderRadius: '9px', fontSize: '11.5px', color: '#A7DDD2' }}>
            <span style={{ flex: '0 0 14px', display: 'inline-flex', color: C.teal }}><FocusIcon size={13} /></span>
            <span>{roots.length ? `${roots.length} root${roots.length === 1 ? '' : 's'} · ${includeSubdomains ? 'subdomains in-scope' : 'same-origin only'}` : 'No roots — everything will read as third-party.'}</span>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '10px', padding: '16px 24px', borderTop: `1px solid ${C.line}` }}>
          <button onClick={onClose} style={{ flex: '0 0 auto', padding: '11px 18px', borderRadius: '10px', border: `1px solid ${C.lineStrong}`, background: 'none', color: C.muted, cursor: 'pointer', fontSize: '13px', fontWeight: 600 }}>Cancel</button>
          <button onClick={submit} disabled={busy} style={{ flex: 1, padding: '11px', borderRadius: '10px', border: 'none', background: busy ? C.control : C.lime, color: busy ? C.faint : C.onLime, cursor: busy ? 'not-allowed' : 'pointer', fontSize: '13.5px', fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }}>
            <CheckIcon size={11} />{busy ? 'Saving…' : 'Save scope'}
          </button>
        </div>
      </div>
    </div>
  );
}
