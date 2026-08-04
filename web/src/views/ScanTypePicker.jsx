// ScanTypePicker.jsx — "scan type" control: Quick/Standard/Deep presets plus an
// Advanced expander revealing the discovery engine (recon only) and the individual
// extractor toggles. Controlled: value = { profile, options, engine }; onChange emits
// the next value. Selecting a preset replaces options/engine; toggling anything in
// Advanced switches the profile to "custom".
import { useState } from 'preact/hooks';
import { C, F } from '../theme.js';
import { CheckIcon, ChevronDown, ChevronRight } from '../icons.jsx';
import { SCAN_PROFILES, ANALYSIS_TOGGLES, ENGINES, matchProfile } from '../scanProfiles.js';

export function ScanTypePicker({ value, onChange, showEngine = true }) {
  const [advanced, setAdvanced] = useState(false);
  const { profile, options, engine } = value;

  const pickPreset = (key) => onChange({ profile: key, options: { ...SCAN_PROFILES[key].options }, engine: SCAN_PROFILES[key].engine });
  const toggleOpt = (key) => {
    const nextOpts = { ...options, [key]: !options[key] };
    onChange({ profile: matchProfile(nextOpts, showEngine ? engine : undefined), options: nextOpts, engine });
  };
  const pickEngine = (key) => onChange({ profile: matchProfile(options, key), options, engine: key });

  const desc = profile === 'custom' ? 'Custom — individual extractors selected below' : (SCAN_PROFILES[profile]?.desc || '');

  return (
    <div>
      <div style={{ display: 'flex', gap: '7px', marginBottom: '8px' }}>
        {Object.entries(SCAN_PROFILES).map(([key, p]) => {
          const on = profile === key;
          return (
            <button key={key} onClick={() => pickPreset(key)} style={{ flex: 1, padding: '9px 0', borderRadius: '9px', border: `1px solid ${on ? C.lime : C.lineStrong}`, background: on ? 'rgba(205,235,69,0.1)' : C.inset, color: on ? C.lime : C.muted, cursor: 'pointer', fontSize: '12.5px', fontWeight: 700 }}>{p.label}</button>
          );
        })}
      </div>
      <div style={{ fontSize: '11.5px', color: profile === 'custom' ? C.amber : C.faint, marginBottom: '12px' }}>{desc}</div>

      <button onClick={() => setAdvanced(!advanced)} style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'none', border: 'none', color: C.muted, cursor: 'pointer', fontSize: '11.5px', fontWeight: 600, padding: 0 }}>
        {advanced ? <ChevronDown size={12} /> : <ChevronRight size={12} />} Advanced
      </button>

      {advanced && (
        <div style={{ marginTop: '12px', padding: '14px', background: C.inset, border: `1px solid ${C.line}`, borderRadius: '11px' }}>
          {showEngine && (
            <>
              <label style={{ display: 'block', fontSize: '10.5px', color: C.muted, fontWeight: 700, letterSpacing: '0.4px', marginBottom: '7px' }}>DISCOVERY ENGINE</label>
              <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap', marginBottom: '14px' }}>
                {ENGINES.map((e) => {
                  const on = engine === e.key;
                  return (
                    <button key={e.key} onClick={() => pickEngine(e.key)} title={e.hint} style={{ padding: '6px 11px', borderRadius: '7px', border: `1px solid ${on ? C.blue : C.lineStrong}`, background: on ? 'rgba(107,168,255,0.12)' : C.panel, color: on ? C.blue : C.muted, cursor: 'pointer', fontFamily: F.mono, fontSize: '11.5px', fontWeight: 600 }}>{e.label}</button>
                  );
                })}
              </div>
            </>
          )}
          <label style={{ display: 'block', fontSize: '10.5px', color: C.muted, fontWeight: 700, letterSpacing: '0.4px', marginBottom: '7px' }}>EXTRACTORS</label>
          <div style={{ display: 'flex', gap: '7px', flexWrap: 'wrap' }}>
            {ANALYSIS_TOGGLES.map((t) => {
              const on = !!options[t.key];
              return (
                <button key={t.key} onClick={() => toggleOpt(t.key)} title={t.hint} style={{ display: 'flex', alignItems: 'center', gap: '7px', padding: '7px 11px', borderRadius: '8px', border: `1px solid ${on ? 'rgba(205,235,69,0.4)' : C.lineStrong}`, background: on ? 'rgba(205,235,69,0.1)' : C.panel, color: on ? C.lime : C.muted, cursor: 'pointer', fontSize: '11.5px', fontWeight: 600 }}>
                  <span style={{ width: '12px', height: '12px', borderRadius: '4px', border: `1.5px solid ${on ? C.lime : C.lineHover}`, background: on ? C.lime : 'transparent', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: C.onLime, flex: '0 0 12px' }}>
                    {on && <CheckIcon size={8} />}
                  </span>
                  {t.label}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
