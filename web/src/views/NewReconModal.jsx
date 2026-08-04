// NewReconModal.jsx — start a recon crawl. Collects only fields the backend
// ReconJobStartRequest actually honors (target URL, scope, source maps, analyze,
// asset/depth caps) so the form never implies behavior the API won't perform.
// Builds the POST /api/recon/jobs/start payload and hands it to app.jsx.
import { useState } from 'preact/hooks';
import { C, F } from '../theme.js';
import { CheckIcon, PlayIcon, InfoIcon } from '../icons.jsx';
import { ScanTypePicker } from './ScanTypePicker.jsx';
import { SCAN_PROFILES, DEFAULT_PROFILE } from '../scanProfiles.js';

// Strip a leading scheme so the fixed "https://" affordance is not duplicated.
function stripScheme(raw) {
  return String(raw || '').replace(/^\s*https?:\/\//i, '').trim();
}

function Toggle({ on, label, onClick }) {
  return (
    <button onClick={onClick} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '9px 13px', borderRadius: '9px', border: `1px solid ${on ? 'rgba(205,235,69,0.4)' : C.lineStrong}`, background: on ? 'rgba(205,235,69,0.1)' : C.inset, color: on ? C.lime : C.muted, cursor: 'pointer', fontSize: '12.5px', fontWeight: 600 }}>
      <span style={{ width: '13px', height: '13px', borderRadius: '4px', border: `1.5px solid ${on ? C.lime : C.lineHover}`, background: on ? C.lime : 'transparent', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: C.onLime, flex: '0 0 13px' }}>
        {on && <CheckIcon size={9} />}
      </span>
      {label}
    </button>
  );
}

const DEPTHS = [0, 1, 2, 3, 4, 5];
const ASSET_CAPS = [100, 300, 600, 1000];

export function NewReconModal({ onClose, onStart, busy }) {
  const [url, setUrl] = useState('');
  const [includeSubdomains, setIncludeSubdomains] = useState(false);
  const [maxDepth, setMaxDepth] = useState(2);
  const [maxAssets, setMaxAssets] = useState(300);
  // Scan type: preset + extractor options + discovery engine (seeded from default preset).
  const [scan, setScan] = useState({
    profile: DEFAULT_PROFILE,
    options: { ...SCAN_PROFILES[DEFAULT_PROFILE].options },
    engine: SCAN_PROFILES[DEFAULT_PROFILE].engine
  });

  const host = stripScheme(url);
  const valid = host.length > 0 && /\./.test(host);

  const submit = () => {
    if (!valid || busy) return;
    onStart({
      url: `https://${host}`,
      sameOriginOnly: !includeSubdomains,
      discoveryEngine: scan.engine,
      performAnalysis: true,
      // One source-maps control: the scan-type toggle drives both fetching maps during
      // the crawl and reconstructing sources during analysis.
      includeSourceMaps: !!scan.options.include_sourcemap,
      analysisOptions: scan.options,
      maxDepth,
      maxAssets
    });
  };

  const stop = (e) => e.stopPropagation();
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(5,7,11,0.72)', zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px', backdropFilter: 'blur(3px)' }}>
      <div onClick={stop} style={{ width: '560px', maxWidth: '94vw', maxHeight: '92vh', background: C.panel2, border: `1px solid ${C.lineStrong}`, borderRadius: '16px', overflowY: 'auto', boxShadow: '0 30px 80px rgba(0,0,0,0.6)', animation: 'dropin .18s ease' }}>
        <div style={{ padding: '20px 24px', borderBottom: `1px solid ${C.line}` }}>
          <h2 style={{ fontFamily: F.display, fontWeight: 700, fontSize: '19px', margin: 0 }}>New Recon Run</h2>
          <div style={{ fontSize: '12.5px', color: C.faint, marginTop: '3px' }}>Passive discovery → fetch → analyze. You can keep working while it runs.</div>
        </div>

        <div style={{ padding: '22px 24px' }}>
          <label style={{ display: 'block', fontSize: '11px', color: C.muted, fontWeight: 700, letterSpacing: '0.5px', marginBottom: '8px' }}>TARGET URL</label>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', background: C.inset, border: `1px solid ${C.lineStrong}`, borderRadius: '10px', padding: '0 13px', marginBottom: '20px' }}>
            <span style={{ color: C.faint, fontFamily: F.mono, fontSize: '13px' }}>https://</span>
            <input value={url} onInput={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && submit()} placeholder="app.target.com" autofocus
              style={{ flex: 1, background: 'none', border: 'none', outline: 'none', color: C.text, fontFamily: F.mono, fontSize: '13px', padding: '12px 0' }} />
          </div>

          <label style={{ display: 'block', fontSize: '11px', color: C.muted, fontWeight: 700, letterSpacing: '0.5px', marginBottom: '10px' }}>SCAN TYPE</label>
          <div style={{ marginBottom: '20px' }}>
            <ScanTypePicker value={scan} onChange={setScan} showEngine />
          </div>

          <label style={{ display: 'block', fontSize: '11px', color: C.muted, fontWeight: 700, letterSpacing: '0.5px', marginBottom: '10px' }}>SCOPE</label>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '20px' }}>
            <Toggle on={includeSubdomains} label="Include subdomains" onClick={() => setIncludeSubdomains(!includeSubdomains)} />
          </div>

          <div style={{ display: 'flex', gap: '20px', marginBottom: '20px' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', fontSize: '11px', color: C.muted, fontWeight: 700, letterSpacing: '0.5px', marginBottom: '8px' }}>CRAWL DEPTH</label>
              <div style={{ display: 'flex', gap: '5px' }}>
                {DEPTHS.map((d) => (
                  <button key={d} onClick={() => setMaxDepth(d)} style={{ flex: 1, padding: '8px 0', borderRadius: '7px', border: `1px solid ${maxDepth === d ? C.lime : C.lineStrong}`, background: maxDepth === d ? 'rgba(205,235,69,0.1)' : C.inset, color: maxDepth === d ? C.lime : C.muted, cursor: 'pointer', fontFamily: F.mono, fontSize: '12.5px', fontWeight: 600 }}>{d}</button>
                ))}
              </div>
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', fontSize: '11px', color: C.muted, fontWeight: 700, letterSpacing: '0.5px', marginBottom: '8px' }}>MAX ASSETS</label>
              <div style={{ display: 'flex', gap: '5px' }}>
                {ASSET_CAPS.map((n) => (
                  <button key={n} onClick={() => setMaxAssets(n)} style={{ flex: 1, padding: '8px 0', borderRadius: '7px', border: `1px solid ${maxAssets === n ? C.lime : C.lineStrong}`, background: maxAssets === n ? 'rgba(205,235,69,0.1)' : C.inset, color: maxAssets === n ? C.lime : C.muted, cursor: 'pointer', fontFamily: F.mono, fontSize: '12.5px', fontWeight: 600 }}>{n}</button>
                ))}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '13px 15px', background: 'rgba(108,168,255,0.07)', border: '1px solid rgba(108,168,255,0.2)', borderRadius: '10px' }}>
            <span style={{ color: C.blue, flex: '0 0 17px', display: 'inline-flex' }}><InfoIcon /></span>
            <span style={{ fontSize: '12px', color: '#9FC2F0', lineHeight: 1.5 }}>Progress appears live in the <b style={{ color: C.lime }}>Activity</b> panel — no need to wait on this screen.</span>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '10px', padding: '16px 24px', borderTop: `1px solid ${C.line}` }}>
          <button onClick={onClose} style={{ flex: '0 0 auto', padding: '11px 18px', borderRadius: '10px', border: `1px solid ${C.lineStrong}`, background: 'none', color: C.muted, cursor: 'pointer', fontSize: '13px', fontWeight: 600 }}>Cancel</button>
          <button onClick={submit} disabled={!valid || busy} style={{ flex: 1, padding: '11px', borderRadius: '10px', border: 'none', background: valid && !busy ? C.lime : C.control, color: valid && !busy ? C.onLime : C.faint, cursor: valid && !busy ? 'pointer' : 'not-allowed', fontSize: '13.5px', fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }}>
            <PlayIcon size={15} />{busy ? 'Starting…' : 'Start Recon'}
          </button>
        </div>
      </div>
    </div>
  );
}
