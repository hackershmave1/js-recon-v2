// Findings.jsx — filter rail (TYPE/SEVERITY/STATUS/SCOPE/SOURCE) + search +
// "Focus: app code" toggle + findings list. Owns its filter/search/drawer UI
// state; receives the decorated findings + triage status map from app.jsx.
import { useEffect, useMemo, useState } from 'preact/hooks';
import { C, F, SEV, TYPE, STATUS, CLS, SCOPE } from '../theme.js';
import { SearchIcon, FocusIcon, ExportIcon, CheckIcon } from '../icons.jsx';
import { FindingDrawer } from './FindingDrawer.jsx';

const TYPE_VALUES = ['secret', 'endpoint'];
const SEV_VALUES = ['critical', 'high', 'medium', 'low'];
const STATUS_VALUES = ['new', 'reviewed', 'confirmed', 'false_positive'];
const SCOPE_VALUES = ['in', 'sub', 'third'];
const CLS_VALUES = ['app', 'lib', 'cms', 'tracker'];

function fileShort(file) {
  return (file || '').replace('webpack://app/', '').replace('webpack://', '').replace(/^https?:\/\//, '');
}
function toggle(list, v) {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

export function Findings({ findings, statusMap, onTriage, onCopy, onOpenSource, onExport, openFp, onOpened }) {
  const [types, setTypes] = useState([]);
  const [severities, setSeverities] = useState([]);
  const [statuses, setStatuses] = useState([]);
  const [scopes, setScopes] = useState([]);
  const [classes, setClasses] = useState([]);
  const [query, setQuery] = useState('');
  const [focus, setFocus] = useState(true);
  const [drawerFp, setDrawerFp] = useState(null);

  // A ⌘K search pick (or other deep-link) requests a specific finding's drawer.
  // Disable Focus so the row is reachable even if it's lib/CMS/tracker noise.
  useEffect(() => {
    if (!openFp) return;
    setDrawerFp(openFp);
    setFocus(false);
    onOpened && onOpened();
  }, [openFp]);

  const statusOf = (f) => statusMap[f.fingerprint] || 'new';

  // counts over the full dataset (per facet value), so the rail reflects totals
  const counts = useMemo(() => {
    const c = { type: {}, sev: {}, status: {}, scope: {}, cls: {} };
    findings.forEach((f) => {
      c.type[f.kind] = (c.type[f.kind] || 0) + 1;
      c.sev[f.severity] = (c.sev[f.severity] || 0) + 1;
      c.status[statusOf(f)] = (c.status[statusOf(f)] || 0) + 1;
      c.scope[f.scope] = (c.scope[f.scope] || 0) + 1;
      c.cls[f.cls] = (c.cls[f.cls] || 0) + 1;
    });
    return c;
  }, [findings, statusMap]);

  const q = query.trim().toLowerCase();
  const matchesFacets = (f) =>
    (!types.length || types.includes(f.kind)) &&
    (!severities.length || severities.includes(f.severity)) &&
    (!statuses.length || statuses.includes(statusOf(f))) &&
    (!scopes.length || scopes.includes(f.scope)) &&
    (!classes.length || classes.includes(f.cls)) &&
    (!q || `${f.label}${f.value}${f.file}`.toLowerCase().includes(q));

  const beforeFocus = findings.filter(matchesFacets);
  const visible = focus ? beforeFocus.filter((f) => f.cls === 'app') : beforeFocus;
  const mutedCount = beforeFocus.length - visible.length;

  const clearFilters = () => { setTypes([]); setSeverities([]); setStatuses([]); setScopes([]); setClasses([]); setQuery(''); };

  const drawerFinding = findings.find((f) => f.fingerprint === drawerFp) || null;

  return (
    <div style={{ display: 'flex', height: '100%', animation: 'dropin .25s ease' }}>
      {/* filter rail */}
      <div style={{ width: '230px', flex: '0 0 230px', borderRight: `1px solid ${C.line}`, padding: '20px 16px', overflowY: 'auto' }}>
        <div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '18px', marginBottom: '2px' }}>Findings</div>
        <div style={{ fontSize: '12px', color: C.faint, marginBottom: '18px' }}>
          <span style={{ color: C.lime, fontFamily: F.mono }}>{visible.length}</span> of {findings.length} shown
        </div>

        <FacetGroup title="TYPE" values={TYPE_VALUES} sel={types} counts={counts.type}
          onToggle={(v) => setTypes(toggle(types, v))} render={(v) => ({ label: TYPE[v].label, c: TYPE[v].c })} box />
        <FacetGroup title="SEVERITY" values={SEV_VALUES} sel={severities} counts={counts.sev}
          onToggle={(v) => setSeverities(toggle(severities, v))} render={(v) => ({ label: SEV[v].label, c: SEV[v].c, dot: 'circle' })} />
        <FacetGroup title="STATUS" values={STATUS_VALUES} sel={statuses} counts={counts.status}
          onToggle={(v) => setStatuses(toggle(statuses, v))} render={(v) => ({ label: STATUS[v].label, c: STATUS[v].c, dot: 'square' })} />
        <FacetGroup title="SCOPE" values={SCOPE_VALUES} sel={scopes} counts={counts.scope}
          onToggle={(v) => setScopes(toggle(scopes, v))} render={(v) => ({ label: SCOPE[v].label, c: SCOPE[v].c, dot: 'square' })} />
        <FacetGroup title="SOURCE" values={CLS_VALUES} sel={classes} counts={counts.cls}
          onToggle={(v) => setClasses(toggle(classes, v))} render={(v) => ({ label: CLS[v].label, c: CLS[v].c, dot: 'circle' })} />

        <button onClick={clearFilters} style={{ marginTop: '18px', width: '100%', padding: '8px', borderRadius: '8px', border: `1px solid ${C.lineStrong}`, background: 'none', color: C.muted, cursor: 'pointer', fontSize: '12px', fontWeight: 600 }}>Clear filters</button>
      </div>

      {/* list */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '14px 22px', borderBottom: `1px solid ${C.line}` }}>
          <div style={{ flex: 1, position: 'relative' }}>
            <span style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', color: C.faint, display: 'inline-flex' }}><SearchIcon size={14} /></span>
            <input value={query} onInput={(e) => setQuery(e.target.value)} placeholder="Filter by value, label, or file path…"
              style={{ width: '100%', padding: '9px 12px 9px 34px', borderRadius: '9px', border: `1px solid ${C.lineStrong}`, background: C.panel, color: C.text, fontFamily: F.mono, fontSize: '12.5px', outline: 'none' }} />
          </div>
          <button onClick={() => setFocus(!focus)} style={{ display: 'flex', alignItems: 'center', gap: '7px', padding: '8px 13px', borderRadius: '9px', border: `1px solid ${focus ? 'rgba(205,235,69,0.4)' : C.lineStrong}`, background: focus ? 'rgba(205,235,69,0.1)' : C.panel, color: focus ? C.lime : C.muted, cursor: 'pointer', fontSize: '12px', fontWeight: 600, whiteSpace: 'nowrap' }}>
            <FocusIcon />Focus: app code
          </button>
          <button onClick={onExport} style={{ display: 'flex', alignItems: 'center', gap: '7px', padding: '8px 13px', borderRadius: '9px', border: `1px solid ${C.lineHover}`, background: C.control, color: C.textSoft, cursor: 'pointer', fontSize: '12px', fontWeight: 600 }}>
            <ExportIcon />Export
          </button>
        </div>

        {mutedCount > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '9px 22px', background: 'rgba(199,146,234,0.07)', borderBottom: `1px solid ${C.line}` }}>
            <span style={{ flex: 1, fontSize: '12px', color: C.purple }}><b style={{ fontFamily: F.mono }}>{mutedCount}</b> library / CMS / tracker findings muted by Focus</span>
            <button onClick={() => setFocus(false)} style={{ fontSize: '12px', color: C.lime, background: 'none', border: 'none', cursor: 'pointer', fontWeight: 600 }}>Show all</button>
          </div>
        )}

        <div style={{ flex: 1, overflowY: 'auto' }}>
          {visible.length === 0 && (
            <div style={{ padding: '70px 20px', textAlign: 'center', color: C.faint }}>
              <div style={{ fontFamily: F.display, fontSize: '16px', color: C.muted, marginBottom: '6px' }}>No findings match</div>
              <div style={{ fontSize: '13px' }}>Adjust filters or clear the search.</div>
            </div>
          )}
          {visible.map((f) => {
            const sev = SEV[f.severity] || SEV.low;
            const type = TYPE[f.kind] || TYPE.endpoint;
            const st = STATUS[statusOf(f)] || STATUS.new;
            const showCls = f.cls !== 'app';
            return (
              <button key={f.fingerprint} onClick={() => setDrawerFp(f.fingerprint)} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: '13px', padding: '13px 22px', border: 'none', borderBottom: '1px solid #161b26', background: drawerFp === f.fingerprint ? C.rowActive : 'transparent', cursor: 'pointer', textAlign: 'left', borderLeft: `3px solid ${sev.c}` }}>
                <span style={{ fontSize: '9.5px', fontWeight: 700, letterSpacing: '0.5px', color: sev.c, background: sev.bg, padding: '3px 7px', borderRadius: '5px', width: '58px', textAlign: 'center', flex: '0 0 58px' }}>{sev.label}</span>
                <span style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.6px', color: type.c, border: `1px solid ${type.bd}`, padding: '2px 6px', borderRadius: '5px', flex: '0 0 auto' }}>{type.label}</span>
                {f.scope === 'third' && <span style={{ fontSize: '9px', fontWeight: 700, color: C.orange, background: 'rgba(255,138,71,0.12)', padding: '2px 6px', borderRadius: '5px', flex: '0 0 auto' }}>3RD-PARTY</span>}
                {showCls && (
                  <span style={{ display: 'flex', alignItems: 'center', gap: '4px', flex: '0 0 auto' }}>
                    <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: (CLS[f.cls] || CLS.app).c }} />
                    <span style={{ fontSize: '10px', color: (CLS[f.cls] || CLS.app).c }}>{(CLS[f.cls] || CLS.app).label}</span>
                  </span>
                )}
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontWeight: 600, fontSize: '13.5px', color: C.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.label}</span>
                  </span>
                  <span style={{ display: 'block', fontFamily: F.mono, fontSize: '12px', color: C.muted, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', marginTop: '2px' }}>{f.value}</span>
                </span>
                <span style={{ flex: '0 0 200px', minWidth: 0, textAlign: 'right' }}>
                  <span style={{ display: 'block', fontFamily: F.mono, fontSize: '11.5px', color: C.blue, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{fileShort(f.file)}</span>
                  <span style={{ display: 'block', fontFamily: F.mono, fontSize: '10.5px', color: C.faint }}>line {f.line}:{f.col}</span>
                </span>
                <span style={{ flex: '0 0 auto', fontSize: '10px', fontWeight: 600, color: st.c, background: st.bg, padding: '3px 8px', borderRadius: '20px' }}>{st.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {drawerFinding && (
        <FindingDrawer finding={drawerFinding} status={statusOf(drawerFinding)}
          onClose={() => setDrawerFp(null)} onCopy={onCopy} onTriage={onTriage} onOpenSource={onOpenSource} />
      )}
    </div>
  );
}

function FacetGroup({ title, values, sel, counts, onToggle, render, box }) {
  return (
    <>
      <div style={{ fontSize: '10.5px', color: C.faint, fontWeight: 700, letterSpacing: '0.8px', margin: title === 'TYPE' ? '0 0 9px' : '18px 0 9px' }}>{title}</div>
      {values.map((v) => {
        const m = render(v);
        const active = sel.includes(v);
        return (
          <button key={v} onClick={() => onToggle(v)} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: '9px', padding: '7px 9px', borderRadius: '8px', border: 'none', background: active ? C.filterActive : 'transparent', cursor: 'pointer', marginBottom: '3px' }}>
            {box
              ? <span style={{ width: '13px', height: '13px', borderRadius: '4px', border: `1.5px solid ${active ? m.c : C.lineHover}`, background: active ? m.c : 'transparent', flex: '0 0 13px', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: C.onLime }}>{active && <CheckIcon size={9} />}</span>
              : <span style={{ width: '8px', height: '8px', borderRadius: m.dot === 'square' ? '3px' : '50%', background: m.c, flex: '0 0 8px' }} />}
            <span style={{ flex: 1, textAlign: 'left', fontSize: '12.5px', color: active ? C.text : C.textSoft }}>{m.label}</span>
            <span style={{ fontFamily: F.mono, fontSize: '11px', color: C.faint }}>{counts[v] || 0}</span>
          </button>
        );
      })}
    </>
  );
}
