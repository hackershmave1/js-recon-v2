// Overview.jsx — session summary: metric cards, attack-surface map, capture coverage,
// priority findings. Mirrors the prototype OVERVIEW view.
import { C, F } from '../theme.js';
import { METRIC_ICONS, RefreshIcon, FocusIcon } from '../icons.jsx';

// Lay out surface-map nodes on concentric rings inside the 296px panel.
function layoutNodes(nodes) {
  const cx = 50, cy = 50;
  return nodes.slice(0, 24).map((n, i, arr) => {
    if (i === 0) return { ...n, x: cx, y: cy, r: 9 };
    const ring = i <= 8 ? 1 : 2;
    const inRing = ring === 1 ? Math.min(8, arr.length - 1) : arr.length - 9;
    const idx = ring === 1 ? i - 1 : i - 9;
    const angle = (idx / Math.max(1, inRing)) * Math.PI * 2;
    const rad = ring === 1 ? 24 : 40;
    return { ...n, x: cx + Math.cos(angle) * rad, y: cy + Math.sin(angle) * rad * 0.82, r: ring === 1 ? 6 : 4.5 };
  });
}

function SurfaceMap({ surface }) {
  const nodes = layoutNodes(surface.nodes || []);
  return (
    <div style={{ position: 'relative', height: '296px' }}>
      {nodes.length === 0 && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.faint, fontSize: '12.5px' }}>No asset graph yet</div>
      )}
      <svg width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: 'absolute', inset: 0 }}>
        {nodes.slice(1).map((n, i) => (
          <line key={`l${i}`} x1={nodes[0].x} y1={nodes[0].y} x2={n.x} y2={n.y} stroke="#1c2230" stroke-width="0.4" />
        ))}
      </svg>
      <svg width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: 'absolute', inset: 0 }}>
        {nodes.map((n, i) => (
          <circle key={i} cx={n.x} cy={n.y} r={n.r / 2} fill={n.color} opacity={i === 0 ? 1 : 0.85} />
        ))}
      </svg>
    </div>
  );
}

function MetricCard({ m }) {
  return (
    <button class="ws-card" onClick={m.go} style={{ textAlign: 'left', background: C.panel, border: `1px solid ${C.line}`, borderRadius: '14px', padding: '16px 17px', cursor: 'pointer', transition: 'border-color .12s' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
        <span style={{ fontSize: '11.5px', color: C.muted, fontWeight: 600, letterSpacing: '0.3px' }}>{m.label}</span>
        <span style={{ width: '26px', height: '26px', borderRadius: '8px', background: m.iconbg, color: m.iconc, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>{m.icon}</span>
      </div>
      <div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '30px', lineHeight: 1, letterSpacing: '-1px', color: C.text }}>{m.value}</div>
      <div style={{ marginTop: '9px', fontSize: '11.5px', color: C.faint }}>{m.sub}</div>
    </button>
  );
}

export function Overview({ vm }) {
  return (
    <div style={{ padding: '26px 30px 60px', maxWidth: '1280px', animation: 'dropin .25s ease' }}>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '18px', marginBottom: '24px' }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: C.faint, fontSize: '12px', fontFamily: F.mono, marginBottom: '7px' }}>
            <span style={{ color: C.lime }}>●</span> SESSION · {vm.sessionId} · last run {vm.lastRun}
          </div>
          <h1 style={{ fontFamily: F.display, fontWeight: 700, fontSize: '28px', margin: 0, letterSpacing: '-0.6px' }}>{vm.host}</h1>
          <button onClick={vm.onEditScope} title="Edit scope" style={{ display: 'inline-flex', alignItems: 'center', gap: '7px', marginTop: '9px', padding: '4px 10px', borderRadius: '20px', border: `1px solid ${vm.hasScope ? 'rgba(91,214,192,0.3)' : C.lineStrong}`, background: vm.hasScope ? 'rgba(91,214,192,0.08)' : C.control, color: vm.hasScope ? C.teal : C.muted, cursor: 'pointer', fontFamily: F.mono, fontSize: '11.5px', fontWeight: 600 }}>
            <FocusIcon size={12} />{vm.scope}
          </button>
        </div>
        <button onClick={vm.onNewRecon} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '10px 16px', borderRadius: '10px', border: `1px solid ${C.lineHover}`, background: C.control, color: C.textSoft, cursor: 'pointer', fontSize: '13px', fontWeight: 600 }}>
          <RefreshIcon />Re-run
        </button>
      </div>

      {/* metric cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '14px', marginBottom: '22px' }}>
        {vm.metrics.map((m) => <MetricCard key={m.label} m={m} />)}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.55fr 1fr', gap: '16px', marginBottom: '16px' }}>
        {/* attack surface map */}
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: '14px', padding: '18px 20px 8px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
            <div>
              <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: '15px' }}>Attack Surface Map</div>
              <div style={{ fontSize: '11.5px', color: C.faint, marginTop: '2px' }}>Hosts &amp; asset clusters discovered from JS + source maps</div>
            </div>
            <span style={{ fontSize: '11px', fontFamily: F.mono, color: C.dim }}>{vm.surface.hosts} hosts · {vm.surface.maps} maps</span>
          </div>
          <SurfaceMap surface={vm.surface} />
        </div>

        {/* coverage */}
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: '14px', padding: '18px 20px' }}>
          <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: '15px', marginBottom: '2px' }}>Capture Coverage</div>
          <div style={{ fontSize: '11.5px', color: C.faint, marginBottom: '16px' }}>Asset lifecycle from latest recon job</div>
          {vm.coverage.map((c) => (
            <div key={c.label} style={{ marginBottom: '15px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '6px' }}>
                <span style={{ fontSize: '12.5px', color: C.textSoft, fontWeight: 500 }}>{c.label}</span>
                <span style={{ fontFamily: F.mono, fontSize: '12px', color: C.muted }}>{c.frac} <span style={{ color: c.pctc }}>{c.pct}%</span></span>
              </div>
              <div style={{ height: '7px', borderRadius: '6px', background: '#1a1f2c', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${c.pct}%`, background: c.color, borderRadius: '6px' }} />
              </div>
            </div>
          ))}
          <div style={{ marginTop: '18px', paddingTop: '15px', borderTop: `1px solid ${C.line}` }}>
            <div style={{ fontSize: '11px', color: C.faint, fontWeight: 700, letterSpacing: '0.5px', marginBottom: '9px' }}>TOP MISS REASONS</div>
            {vm.missReasons.length === 0 && <div style={{ fontSize: '12px', color: C.faint, fontFamily: F.mono }}>none</div>}
            {vm.missReasons.map((r) => (
              <div key={r.label} style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '7px' }}>
                <span style={{ fontFamily: F.mono, fontSize: '10.5px', color: C.orange, background: 'rgba(255,138,71,0.1)', padding: '1px 7px', borderRadius: '5px' }}>{r.n}</span>
                <span style={{ fontSize: '12px', color: C.muted, fontFamily: F.mono }}>{r.label}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* priority findings */}
      <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: '14px', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px', borderBottom: `1px solid ${C.line}` }}>
          <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: '15px' }}>Priority Findings</div>
          <button onClick={vm.goFindings} style={{ fontSize: '12.5px', color: C.lime, background: 'none', border: 'none', cursor: 'pointer', fontWeight: 600 }}>View all →</button>
        </div>
        {vm.topFindings.length === 0 && (
          <div style={{ padding: '40px 20px', textAlign: 'center', color: C.faint, fontSize: '13px' }}>No high-severity findings yet. Run analysis on a session to populate this.</div>
        )}
        {vm.topFindings.map((f) => (
          <button key={f.id} onClick={() => vm.openFinding(f)} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: '14px', padding: '13px 20px', border: 'none', borderBottom: '1px solid #161b26', background: 'none', cursor: 'pointer', textAlign: 'left' }}>
            <span style={{ width: '3px', height: '30px', borderRadius: '3px', background: f.sev.c, flex: '0 0 3px' }} />
            <span style={{ fontSize: '10px', fontWeight: 700, letterSpacing: '0.5px', color: f.sev.c, background: f.sev.bg, padding: '3px 8px', borderRadius: '5px', width: '62px', textAlign: 'center', flex: '0 0 62px' }}>{f.sev.label}</span>
            <span style={{ fontSize: '9.5px', fontWeight: 700, letterSpacing: '0.6px', color: f.type.c, border: `1px solid ${f.type.bd}`, padding: '2px 7px', borderRadius: '5px' }}>{f.type.label}</span>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: 'block', fontWeight: 600, fontSize: '13.5px', color: C.text }}>{f.label}</span>
              <span style={{ display: 'block', fontFamily: F.mono, fontSize: '11.5px', color: C.dim, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.fileLine}</span>
            </span>
            <span style={{ fontFamily: F.mono, fontSize: '11.5px', color: C.blue }}>trace →</span>
          </button>
        ))}
      </div>
    </div>
  );
}
