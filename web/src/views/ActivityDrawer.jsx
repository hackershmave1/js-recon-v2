// ActivityDrawer.jsx — right-side panel listing recon jobs with a Discover→Fetch→
// Analyze→Done stepper, live progress, and a Stop action for active jobs. Renders
// thin view-models from transforms.jobActivityVm; receives the polled jobs from app.jsx.
import { C, F } from '../theme.js';
import { CloseIcon, CheckIcon, ClockIcon, StopIcon } from '../icons.jsx';
import { jobActivityVm } from '../transforms.overlays.js';

function Stepper({ stages }) {
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: '12px' }}>
        {stages.map((st, i) => {
          const dotbg = st.state === 'done' ? C.lime : st.state === 'active' ? 'rgba(205,235,69,0.15)' : '#1a1f2c';
          const dotbd = st.state === 'pending' ? C.lineHover : C.lime;
          return (
            <div key={st.label} style={{ display: 'flex', alignItems: 'center', flex: 1 }}>
              <span style={{ width: '18px', height: '18px', borderRadius: '50%', background: dotbg, border: `2px solid ${dotbd}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: C.onLime, flex: '0 0 18px' }}>
                {st.state === 'done' && <CheckIcon size={9} />}
                {st.state === 'active' && <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: C.lime, animation: 'pulse 1s infinite' }} />}
              </span>
              {i < stages.length - 1 && <div style={{ flex: 1, height: '2px', background: st.state === 'done' ? C.lime : '#1a1f2c' }} />}
            </div>
          );
        })}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '9.5px', color: C.faint, marginBottom: '13px', fontFamily: F.mono }}>
        {stages.map((st) => <span key={st.label}>{st.label}</span>)}
      </div>
    </>
  );
}

function JobCard({ j, onStop }) {
  const border = j.active ? 'rgba(205,235,69,0.25)' : C.line;
  return (
    <div style={{ background: C.panel, border: `1px solid ${border}`, borderRadius: '12px', padding: '15px 16px', marginBottom: '12px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '9px', marginBottom: '4px' }}>
        <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: j.statusc, boxShadow: j.active ? `0 0 8px ${j.statusc}` : 'none' }} />
        <span style={{ flex: 1, fontWeight: 600, fontSize: '13.5px', color: C.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{j.title}</span>
        <span style={{ fontSize: '10.5px', fontWeight: 700, color: j.statusc }}>{j.statusLabel}</span>
      </div>
      <div style={{ fontFamily: F.mono, fontSize: '11px', color: C.dim, marginBottom: '12px', paddingLeft: '17px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{j.target}</div>

      <Stepper stages={j.stages} />

      {j.active && (
        <>
          <div style={{ marginBottom: '9px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', marginBottom: '5px' }}>
              <span style={{ color: C.textSoft, fontWeight: 500 }}>{j.stageLabel}</span>
              <span style={{ fontFamily: F.mono, color: C.muted }}>{j.done}/{j.total}</span>
            </div>
            <div style={{ height: '7px', borderRadius: '6px', background: '#1a1f2c', overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${j.pct}%`, background: 'repeating-linear-gradient(90deg, #CDEB45 0 14px, #b6d63a 14px 28px)', backgroundSize: '28px 100%', animation: 'barflow 0.7s linear infinite', borderRadius: '6px' }} />
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '7px', flex: 1, fontSize: '11.5px', color: C.lime, fontFamily: F.mono }}>
              <ClockIcon />{j.pct}% complete
            </span>
            {j.canStop && (
              <button onClick={() => onStop(j.jobId)} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '5px 11px', borderRadius: '8px', border: `1px solid ${C.lineHover}`, background: C.control, color: C.muted, cursor: 'pointer', fontSize: '11.5px', fontWeight: 600 }}>
                <StopIcon />Stop
              </button>
            )}
          </div>
        </>
      )}
      {j.doneState && (
        <div style={{ fontSize: '11.5px', color: C.muted, fontFamily: F.mono }}>{j.summary}</div>
      )}
    </div>
  );
}

export function ActivityDrawer({ jobs, onClose, onStop }) {
  const vms = (jobs || []).map(jobActivityVm);
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(5,7,11,0.6)', zIndex: 40 }} />
      <aside style={{ position: 'fixed', top: 0, right: 0, height: '100vh', width: '420px', maxWidth: '94vw', background: C.panel2, borderLeft: `1px solid ${C.lineStrong}`, zIndex: 41, overflowY: 'auto', animation: 'slidein .22s cubic-bezier(.2,.8,.2,1)' }}>
        <div style={{ padding: '20px 22px', borderBottom: `1px solid ${C.line}`, display: 'flex', alignItems: 'center', gap: '10px' }}>
          <h2 style={{ fontFamily: F.display, fontWeight: 700, fontSize: '18px', margin: 0, flex: 1 }}>Activity</h2>
          <button onClick={onClose} style={{ width: '30px', height: '30px', borderRadius: '8px', border: `1px solid ${C.lineStrong}`, background: C.control, color: C.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <CloseIcon />
          </button>
        </div>
        <div style={{ padding: '16px 18px' }}>
          {vms.length === 0 && (
            <div style={{ padding: '50px 16px', textAlign: 'center', color: C.faint }}>
              <div style={{ fontFamily: F.display, fontSize: '15px', color: C.muted, marginBottom: '6px' }}>No recon jobs yet</div>
              <div style={{ fontSize: '12.5px' }}>Start a New Recon to see live progress here.</div>
            </div>
          )}
          {vms.map((j) => <JobCard key={j.jobId} j={j} onStop={onStop} />)}
        </div>
      </aside>
    </>
  );
}
