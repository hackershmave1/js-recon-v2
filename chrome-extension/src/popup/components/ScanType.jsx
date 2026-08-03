// ScanType.jsx — popup "scan type" control: Quick/Standard/Deep presets + an
// Advanced expander of extractor toggles. Controlled via vm.scan ({profile, options})
// and vm.onScanChange. The chosen options are sent as analysisOptions on upload.
import { useState } from 'preact/hooks';
import { C, F } from '../theme.js';
import { SCAN_PROFILES, ANALYSIS_TOGGLES, matchProfile } from '../scanProfiles.js';

export function ScanType({ scan, onChange, disabled }) {
  const [advanced, setAdvanced] = useState(false);
  const { profile, options } = scan;

  const pickPreset = (key) => onChange({ profile: key, options: { ...SCAN_PROFILES[key].options } });
  const toggleOpt = (key) => {
    const next = { ...options, [key]: !options[key] };
    onChange({ profile: matchProfile(next), options: next });
  };
  const desc = profile === 'custom' ? 'Custom extractors' : (SCAN_PROFILES[profile]?.desc || '');
  const dim = disabled ? 0.5 : 1;

  return (
    <div style={{ opacity: dim, pointerEvents: disabled ? 'none' : 'auto' }}>
      <div style={{ display: 'flex', gap: '6px', marginBottom: '6px' }}>
        {Object.entries(SCAN_PROFILES).map(([key, p]) => {
          const on = profile === key;
          return (
            <button key={key} onClick={() => pickPreset(key)} style={{ flex: 1, padding: '7px 0', borderRadius: '8px', border: `1px solid ${on ? C.lime : C.lineStrong}`, background: on ? 'rgba(205,235,69,0.1)' : C.inset, color: on ? C.lime : C.muted, cursor: 'pointer', fontSize: '11.5px', fontWeight: 700 }}>{p.label}</button>
          );
        })}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: advanced ? '8px' : 0 }}>
        <span style={{ fontSize: '10px', color: profile === 'custom' ? C.amber : C.faint }}>{desc}</span>
        <button onClick={() => setAdvanced(!advanced)} style={{ fontSize: '10px', color: C.muted, background: 'none', border: 'none', cursor: 'pointer', fontFamily: F.mono }}>{advanced ? 'hide' : 'advanced'}</button>
      </div>
      {advanced && (
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {ANALYSIS_TOGGLES.map((t) => {
            const on = !!options[t.key];
            return (
              <button key={t.key} onClick={() => toggleOpt(t.key)} style={{ padding: '5px 9px', borderRadius: '7px', border: `1px solid ${on ? 'rgba(205,235,69,0.4)' : C.lineStrong}`, background: on ? 'rgba(205,235,69,0.1)' : C.inset, color: on ? C.lime : C.muted, cursor: 'pointer', fontSize: '10.5px', fontWeight: 600 }}>{t.label}</button>
            );
          })}
        </div>
      )}
    </div>
  );
}
