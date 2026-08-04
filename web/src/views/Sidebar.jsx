// Sidebar.jsx — left rail: brand, primary nav (with live counts), target selector,
// Activity button. Mirrors the prototype SIDEBAR.
import { C, F } from '../theme.js';
import { Logo, NAV_ICONS, ActivityIcon, ChevronDown } from '../icons.jsx';

const NAV = [
  { key: 'projects', label: 'Projects' },
  { key: 'overview', label: 'Overview' },
  { key: 'findings', label: 'Findings' },
  { key: 'sources', label: 'Sources' },
  { key: 'sessions', label: 'Sessions' }
];

export function Sidebar({ view, onNav, criticalCount, target, runningJobs, onActivity, onTarget }) {
  return (
    <aside style={{ width: '236px', flex: '0 0 236px', background: C.panel2, borderRight: `1px solid ${C.line}`, display: 'flex', flexDirection: 'column' }}>
      {/* brand */}
      <div style={{ height: '60px', display: 'flex', alignItems: 'center', gap: '11px', padding: '0 20px', borderBottom: `1px solid ${C.line}` }}>
        <div style={{ width: '30px', height: '30px', borderRadius: '8px', background: C.lime, display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 0 18px rgba(205,235,69,0.35)' }}>
          <Logo />
        </div>
        <div style={{ lineHeight: 1.05 }}>
          <div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '15px', letterSpacing: '-0.3px' }}>RECON</div>
          <div style={{ fontSize: '10px', color: C.faint, letterSpacing: '1.5px', fontWeight: 600 }}>WORKSPACE</div>
        </div>
      </div>

      {/* nav */}
      <div style={{ padding: '14px 12px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
        <div style={{ fontSize: '10px', color: C.faint, fontWeight: 700, letterSpacing: '1px', padding: '6px 10px 4px' }}>ANALYZE</div>
        {NAV.map((n) => {
          const active = view === n.key;
          const count = n.key === 'findings' ? criticalCount : 0;
          return (
            <button key={n.key} class="ws-navbtn" onClick={() => onNav(n.key)} style={{
              display: 'flex', alignItems: 'center', gap: '11px', padding: '9px 11px', borderRadius: '9px',
              border: 'none', cursor: 'pointer', textAlign: 'left', fontSize: '13.5px', fontWeight: 500,
              background: active ? C.filterActive : 'transparent', color: active ? C.text : C.muted, transition: 'background .12s'
            }}>
              <span style={{ width: '18px', height: '18px', display: 'inline-flex', color: active ? C.lime : C.faint }}>{NAV_ICONS[n.key]}</span>
              <span style={{ flex: 1 }}>{n.label}</span>
              {count > 0 && (
                <span style={{ fontFamily: F.mono, fontSize: '11px', color: C.red, background: 'rgba(255,77,94,0.13)', padding: '1px 7px', borderRadius: '20px', fontWeight: 500 }}>{count}</span>
              )}
            </button>
          );
        })}
      </div>

      {/* target */}
      <div style={{ marginTop: '6px', padding: '14px 12px', borderTop: `1px solid ${C.line}` }}>
        <div style={{ fontSize: '10px', color: C.faint, fontWeight: 700, letterSpacing: '1px', padding: '2px 10px 8px' }}>TARGET</div>
        <button onClick={onTarget} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 11px', borderRadius: '10px', border: `1px solid ${C.lineStrong}`, background: C.control2, cursor: 'pointer', textAlign: 'left' }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: C.lime, boxShadow: `0 0 8px ${C.lime}`, flex: '0 0 8px' }} />
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: 'block', fontFamily: F.mono, fontSize: '12.5px', color: C.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{target.host}</span>
            <span style={{ display: 'block', fontSize: '10.5px', color: C.faint }}>{target.sub}</span>
          </span>
          <span style={{ color: C.faint, display: 'inline-flex' }}><ChevronDown /></span>
        </button>
      </div>

      {/* activity */}
      <div style={{ marginTop: 'auto', padding: '14px 12px' }}>
        <button onClick={onActivity} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: '10px', padding: '11px', borderRadius: '10px', border: `1px solid ${runningJobs > 0 ? 'rgba(205,235,69,0.3)' : C.lineStrong}`, background: runningJobs > 0 ? 'rgba(205,235,69,0.06)' : C.control2, cursor: 'pointer', textAlign: 'left' }}>
          <span style={{ width: '18px', height: '18px', color: runningJobs > 0 ? C.lime : C.muted, display: 'inline-flex' }}><ActivityIcon /></span>
          <span style={{ flex: 1, fontSize: '12.5px', fontWeight: 600, color: C.textSoft }}>Activity</span>
          {runningJobs > 0 && (
            <span style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontFamily: F.mono, color: C.lime }}>
              <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: C.lime, animation: 'pulse 1.1s infinite' }} />{runningJobs}
            </span>
          )}
        </button>
      </div>
    </aside>
  );
}
