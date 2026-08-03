// HomeView.jsx — the default popup screen: capture target, scope, live stats,
// recent captures, quick toggles and footer actions. Pixel layout mirrors the
// "RECON Capture" prototype HOME view.
import { useState } from 'preact/hooks';
import { C, F, CLASS_COLOR, CLASS_LABEL } from '../theme.js';
import { Switch, Dot } from './ui.jsx';
import { ScanType } from './ScanType.jsx';
import {
  SearchIcon, GearIcon, PauseIcon, PlayIcon, DownloadIcon, ArrowRightIcon
} from '../icons.jsx';

const FLAG = { c: C.pink, bg: 'rgba(255,107,138,0.13)' };

function SectionLabel({ children }) {
  return (
    <span style={{ fontSize: '10.5px', color: C.faint, fontWeight: 700, letterSpacing: '0.8px' }}>
      {children}
    </span>
  );
}

function CaptureRow({ c }) {
  const isApp = c.classification === 'app';
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '9px', padding: '9px 8px',
      borderRadius: '9px', animation: 'capflow .25s ease'
    }}>
      <Dot color={c.dot} size={7} pulse={c.analyzing} />
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{
          display: 'block', fontFamily: F.mono, fontSize: '11.5px', color: C.textSoft,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
        }}>{c.name}</span>
        <span style={{ display: 'block', fontSize: '9.5px', color: C.faint }}>{c.meta}</span>
      </span>
      {c.isThirdParty && (
        <span style={{
          fontSize: '8px', fontWeight: 700, color: C.orange, background: 'rgba(255,138,71,0.13)',
          padding: '2px 6px', borderRadius: '9px', flex: '0 0 auto'
        }}>3RD</span>
      )}
      {!isApp && (
        <span style={{ fontSize: '9px', fontWeight: 600, color: CLASS_COLOR[c.classification], flex: '0 0 auto' }}>
          {CLASS_LABEL[c.classification]}
        </span>
      )}
      {c.secretCount > 0 && (
        <span style={{
          fontSize: '9px', fontWeight: 700, color: FLAG.c, background: FLAG.bg,
          padding: '2px 7px', borderRadius: '10px', flex: '0 0 auto'
        }}>{c.secretCount} SEC</span>
      )}
    </div>
  );
}

// Inline "New session" control: rotates the capture session and seeds its scope
// (root domains + the subdomains toggle) so the app-side session reflects it.
function NewSession({ vm }) {
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState('');
  const input = {
    flex: 1, minWidth: 0, background: C.inset, border: `1px solid ${C.lineStrong}`,
    borderRadius: '8px', color: C.text, fontFamily: F.mono, fontSize: '11.5px', padding: '7px 9px', outline: 'none'
  };
  const start = () => { vm.startNewSession(scope); setOpen(false); };
  if (!open) {
    return (
      <button onClick={() => { setScope(vm.startScopeDefault || ''); setOpen(true); }} style={{
        marginTop: '8px', width: '100%', padding: '8px', borderRadius: '9px',
        border: `1px solid ${C.lineHover}`, background: C.control, color: C.textSoft,
        cursor: 'pointer', fontSize: '12px', fontWeight: 600
      }}>+ New session</button>
    );
  }
  return (
    <div style={{ marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <input value={scope} placeholder="root domains (e.g. app.target.com)" autofocus
        onInput={(e) => setScope(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') start(); if (e.key === 'Escape') setOpen(false); }}
        style={input} />
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <button onClick={vm.toggleSubdomains} style={{ display: 'flex', alignItems: 'center', gap: '6px', border: 'none', background: 'none', cursor: 'pointer' }}>
          <span style={{ fontSize: '10.5px', color: C.muted }}>+ subdomains</span>
          <Switch on={vm.includeSubdomains} variant="sm" />
        </button>
        <span style={{ flex: 1 }} />
        <button onClick={() => setOpen(false)} style={{
          padding: '7px 12px', borderRadius: '8px', border: `1px solid ${C.lineHover}`,
          background: C.control, color: C.muted, cursor: 'pointer', fontSize: '11.5px', fontWeight: 600
        }}>Cancel</button>
        <button onClick={start} style={{
          padding: '7px 14px', borderRadius: '8px', border: 'none',
          background: C.lime, color: C.onLime, cursor: 'pointer', fontSize: '11.5px', fontWeight: 700
        }}>Start</button>
      </div>
    </div>
  );
}

export function HomeView({ vm }) {
  const cap = vm.capturing;
  const statBox = (value, label, color) => (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: '11px', padding: '12px' }}>
      <div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '22px', lineHeight: 1, color: color || C.text }}>{value}</div>
      <div style={{ fontSize: '10px', color: C.faint, marginTop: '4px' }}>{label}</div>
    </div>
  );

  // Decoupled analysis button state.
  const a = vm.analysis || { status: 'idle' };
  const ac = a.counts || {};
  const analyzing = a.status === 'starting' || a.status === 'running';
  const analyzeLabel = a.status === 'starting' ? 'Starting analysis…'
    : a.status === 'running' ? `Analyzing… ${ac.completed || 0}/${ac.total || 0}`
    : a.status === 'done' ? `Analyzed ✓ · ${ac.completed || 0} done`
    : `Analyze ${vm.stats.js} ${vm.stats.js === 1 ? 'script' : 'scripts'}`;
  const analyzeDisabled = !vm.canAnalyze || analyzing;

  return (
    <div>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '11px', padding: '15px 17px', borderBottom: `1px solid ${C.line}` }}>
        <div style={{
          width: '28px', height: '28px', borderRadius: '8px', background: C.lime, display: 'flex',
          alignItems: 'center', justifyContent: 'center', boxShadow: '0 0 16px rgba(205,235,69,0.4)'
        }}><SearchIcon /></div>
        <div style={{ flex: 1, lineHeight: 1.05 }}>
          <div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '14px', letterSpacing: '-0.2px' }}>RECON Capture</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '10.5px', color: C.lime, fontFamily: F.mono, marginTop: '1px' }}>
            <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: C.lime, boxShadow: `0 0 6px ${C.lime}` }} />
            {vm.connectionLabel}
          </div>
        </div>
        <button class="pp-iconbtn" onClick={vm.openSettings} aria-label="Open settings" style={{
          width: '30px', height: '30px', borderRadius: '8px', border: `1px solid ${C.lineStrong}`,
          background: C.control, color: C.muted, cursor: 'pointer', display: 'flex',
          alignItems: 'center', justifyContent: 'center'
        }}><GearIcon /></button>
      </div>

      {/* capture target card */}
      <div style={{ padding: '15px 17px' }}>
        <div style={{
          background: C.panel, border: `1px solid ${cap ? 'rgba(205,235,69,0.25)' : C.line}`,
          borderRadius: '13px', padding: '14px 15px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '9px', marginBottom: '12px' }}>
            <Dot color={cap ? C.lime : C.dim} size={8} pulse={cap} />
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{
                display: 'block', fontFamily: F.mono, fontSize: '13px', color: C.text,
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
              }}>{vm.host}</span>
              <span style={{ display: 'block', fontSize: '10.5px', color: C.faint }}>session · {vm.session}</span>
            </span>
            <span style={{
              fontSize: '10px', fontWeight: 700, color: cap ? C.lime : C.dim,
              background: cap ? 'rgba(205,235,69,0.13)' : 'rgba(126,138,163,0.13)',
              padding: '3px 9px', borderRadius: '20px'
            }}>{cap ? 'CAPTURING' : 'PAUSED'}</span>
          </div>
          <button onClick={vm.toggleCapture} style={{
            width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
            padding: '10px', borderRadius: '10px', border: 'none',
            background: cap ? C.control : C.lime, color: cap ? C.orange : C.onLime,
            cursor: 'pointer', fontSize: '13px', fontWeight: 700
          }}>
            <span style={{ width: '15px', height: '15px', display: 'inline-flex' }}>
              {cap ? <PauseIcon /> : <PlayIcon />}
            </span>
            {cap ? 'Pause capture' : 'Resume capture'}
          </button>
        </div>
      </div>

      {/* scope */}
      <div style={{ padding: '0 17px 14px' }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: '9px', background: C.inset,
          border: `1px solid ${C.lineStrong}`, borderRadius: '10px', padding: '9px 12px'
        }}>
          <span style={{ fontSize: '10px', color: C.faint, fontWeight: 700, letterSpacing: '0.6px' }}>SCOPE</span>
          <span style={{
            fontFamily: F.mono, fontSize: '12px', color: C.lime, flex: 1, minWidth: 0,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
          }}>{vm.scope}</span>
          <button onClick={vm.toggleSubdomains} style={{
            display: 'flex', alignItems: 'center', gap: '6px', border: 'none', background: 'none', cursor: 'pointer'
          }}>
            <span style={{ fontSize: '10.5px', color: C.muted }}>+ subdomains</span>
            <Switch on={vm.includeSubdomains} variant="sm" />
          </button>
        </div>
        <NewSession vm={vm} />
      </div>

      {/* stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '9px', padding: '0 17px 15px' }}>
        {statBox(vm.stats.js, 'scripts')}
        {statBox(vm.stats.maps, 'maps', C.teal)}
        {statBox(vm.stats.secrets, 'secrets', C.pink)}
      </div>

      {/* analyze on demand (decoupled from capture) */}
      <div style={{ padding: '0 17px 15px' }}>
        <button onClick={vm.analyzeNow} disabled={analyzeDisabled} style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
          padding: '10px', borderRadius: '10px', border: `1px solid ${analyzing ? 'rgba(91,214,192,0.4)' : C.lineHover}`,
          background: C.panel, color: analyzeDisabled && !analyzing ? C.dim : C.textSoft,
          cursor: analyzeDisabled ? 'default' : 'pointer', fontSize: '12.5px', fontWeight: 600
        }}>
          {analyzing && <Dot color={C.teal} size={7} pulse />}
          {analyzeLabel}
        </button>
        <div style={{ fontSize: '10px', color: C.faint, marginTop: '6px', textAlign: 'center' }}>
          Capture stays fast — analysis runs on demand.
        </div>
      </div>

      {/* recent captures */}
      <div style={{ padding: '0 17px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
          <SectionLabel>RECENT CAPTURES</SectionLabel>
          <span style={{ display: 'flex', alignItems: 'center', gap: '9px' }}>
            {vm.mutedCount > 0 && (
              <button onClick={vm.showAllCaptures} style={{
                fontSize: '10.5px', color: C.purple, background: 'none', border: 'none', cursor: 'pointer', fontFamily: F.mono
              }}>{vm.mutedCount} muted</button>
            )}
            <span style={{ fontSize: '10.5px', color: C.dim, fontFamily: F.mono }}>{cap ? 'live' : 'idle'}</span>
          </span>
        </div>
        <div style={{ maxHeight: '168px', overflowY: 'auto', margin: '0 -2px' }}>
          {vm.captures.length === 0 && (
            <div style={{ padding: '18px 8px', textAlign: 'center', fontSize: '11px', color: C.faint }}>
              No captures yet
            </div>
          )}
          {vm.captures.map((c) => <CaptureRow key={c.key} c={c} />)}
        </div>
      </div>

      {/* scan type */}
      <div style={{ padding: '13px 17px', borderTop: `1px solid ${C.line}`, marginTop: '13px' }}>
        <div style={{ marginBottom: '9px' }}><SectionLabel>SCAN TYPE</SectionLabel></div>
        <ScanType scan={vm.scan} onChange={vm.onScanChange} />
        {!vm.analyzeOn && (
          <div style={{ fontSize: '10px', color: C.faint, marginTop: '8px' }}>
            Applies when <span style={{ color: C.muted }}>Analyze on upload</span> is on.
          </div>
        )}
      </div>

      {/* quick toggles */}
      <div style={{ padding: '13px 17px', borderTop: `1px solid ${C.line}` }}>
        {vm.toggles.map((t) => (
          <button key={t.key} onClick={() => vm.toggleSetting(t.key)} style={{
            width: '100%', display: 'flex', alignItems: 'center', gap: '10px', padding: '8px 0',
            border: 'none', background: 'none', cursor: 'pointer'
          }}>
            <span style={{ flex: 1, textAlign: 'left', fontSize: '12.5px', color: C.textSoft }}>{t.label}</span>
            <Switch on={t.on} />
          </button>
        ))}
      </div>

      {/* footer */}
      <div style={{ display: 'flex', gap: '9px', padding: '13px 17px', borderTop: `1px solid ${C.line}` }}>
        <button onClick={vm.exportNow} style={{
          flex: '0 0 auto', display: 'flex', alignItems: 'center', gap: '7px', padding: '10px 14px',
          borderRadius: '10px', border: `1px solid ${C.lineHover}`, background: C.control,
          color: C.textSoft, cursor: 'pointer', fontSize: '12.5px', fontWeight: 600
        }}>
          <DownloadIcon />Export
        </button>
        <button onClick={vm.openWorkspace} style={{
          flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '7px',
          padding: '10px', borderRadius: '10px', border: 'none', background: C.lime,
          color: C.onLime, cursor: 'pointer', fontSize: '12.5px', fontWeight: 700
        }}>
          Open Workspace<ArrowRightIcon />
        </button>
      </div>
    </div>
  );
}
