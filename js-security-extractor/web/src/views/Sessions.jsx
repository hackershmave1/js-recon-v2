// Sessions.jsx — grid of recon targets and their latest runs. Each card shows the
// session scope (root domains + subdomain rule) and carries inline actions: Resume
// (continue crawl), Stop (in-flight job), Move (reassign to an engagement / Standalone),
// Scope (edit), Rename (inline edit), and Delete (two-step confirm). When a project is
// open, a second "Unassigned sessions" section lets loose sessions be adopted into it,
// so an engagement is never a dead-end even before anything is bound. Mirrors SESSIONS.
import { useState } from 'preact/hooks';
import { C, F } from '../theme.js';
import { PlusIcon, PlayIcon, StopIcon, EditIcon, TrashIcon, CheckIcon, CloseIcon, FocusIcon } from '../icons.jsx';

function ActionButton({ onClick, color, title, children }) {
  return (
    <button onClick={onClick} title={title} style={{ display: 'flex', alignItems: 'center', gap: '5px', padding: '5px 9px', borderRadius: '7px', border: `1px solid ${C.lineStrong}`, background: C.control, color: color || C.muted, cursor: 'pointer', fontSize: '11px', fontWeight: 600 }}>
      {children}
    </button>
  );
}

// Reassign control on every card. Its value reflects the current binding, so it also
// reads as a "which engagement is this in?" indicator. Empty value = Standalone.
function MoveSelect({ current, projects, onChange }) {
  return (
    <select value={current || ''} onChange={(e) => onChange(e.target.value || null)}
      title="Move to an engagement"
      style={{ maxWidth: '160px', padding: '5px 8px', borderRadius: '7px', border: `1px solid ${C.lineStrong}`, background: C.control, color: current ? C.lime : C.muted, cursor: 'pointer', fontSize: '11px', fontWeight: 600, fontFamily: F.body }}>
      <option value="">Standalone</option>
      {(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select>
  );
}

function SessionCard({ s, projects, quickAssignTo, onOpen, onStop, onResume, onRename, onDelete, onEditScope, onAssign }) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(s.name || '');
  const [confirming, setConfirming] = useState(false);

  const stop = (e) => e.stopPropagation();
  const saveName = () => { if (name.trim()) { onRename(s.id, name.trim()); setRenaming(false); } };

  return (
    <div class="ws-card" style={{ background: C.panel, border: `1px solid ${s.border}`, borderRadius: '14px', padding: '18px 20px', transition: 'border-color .12s' }}>
      <button onClick={() => onOpen(s)} style={{ display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'inherit' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '14px' }}>
          <span style={{ position: 'relative', width: '9px', height: '9px', borderRadius: '50%', background: s.statusc, boxShadow: `0 0 8px ${s.statusc}`, flex: '0 0 9px' }}>
            {s.running && <span style={{ position: 'absolute', inset: 0, borderRadius: '50%', background: s.statusc, animation: 'pulse 1s infinite' }} />}
          </span>
          {renaming ? (
            <input value={name} onClick={stop} onInput={(e) => setName(e.target.value)} autofocus
              onKeyDown={(e) => { if (e.key === 'Enter') saveName(); if (e.key === 'Escape') { setRenaming(false); setName(s.name || ''); } }}
              style={{ flex: 1, minWidth: 0, background: C.inset, border: `1px solid ${C.lime}`, borderRadius: '7px', color: C.text, fontFamily: F.mono, fontSize: '13px', padding: '5px 9px', outline: 'none' }} />
          ) : (
            <span style={{ flex: 1, minWidth: 0, fontFamily: F.mono, fontSize: '14px', color: C.text, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.host}</span>
          )}
          <span style={{ fontSize: '10.5px', fontWeight: 700, color: s.statusc, background: s.statusbg, padding: '3px 9px', borderRadius: '20px', flex: '0 0 auto' }}>{s.statusLabel}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '14px', fontSize: '11.5px', fontFamily: F.mono, color: s.rootDomains.length ? C.teal : C.faint, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          <span style={{ flex: '0 0 13px', display: 'inline-flex', color: s.rootDomains.length ? C.teal : C.faint }}><FocusIcon size={12} /></span>
          <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.scopeLabel}</span>
        </div>
        <div style={{ display: 'flex', gap: '22px', marginBottom: '14px' }}>
          <div><div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '20px', color: C.text }}>{s.files}</div><div style={{ fontSize: '10.5px', color: C.faint }}>files</div></div>
          <div><div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '20px', color: C.indigo }}>{s.endpoints}</div><div style={{ fontSize: '10.5px', color: C.faint }}>endpoints</div></div>
          <div><div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '20px', color: C.pink }}>{s.secrets}</div><div style={{ fontSize: '10.5px', color: C.faint }}>secrets</div></div>
          <div style={{ marginLeft: 'auto', textAlign: 'right' }}><div style={{ fontSize: '11px', color: C.muted, fontFamily: F.mono }}>{s.lastRun}</div><div style={{ fontSize: '10.5px', color: C.faint }}>last run</div></div>
        </div>
        <div style={{ height: '6px', borderRadius: '6px', background: '#1a1f2c', overflow: 'hidden', display: 'flex' }}>
          <div style={{ height: '100%', width: `${s.cov}%`, background: s.statusc }} />
        </div>
        <div style={{ fontSize: '10.5px', color: C.faint, marginTop: '7px', fontFamily: F.mono }}>{s.cov}% analyzed</div>
      </button>

      <div onClick={stop} style={{ display: 'flex', alignItems: 'center', gap: '7px', marginTop: '14px', paddingTop: '13px', borderTop: `1px solid ${C.line}`, flexWrap: 'wrap' }}>
        {confirming ? (
          <>
            <span style={{ flex: 1, fontSize: '11.5px', color: C.textSoft }}>Delete this session and all its files?</span>
            <ActionButton onClick={() => setConfirming(false)} title="Cancel"><CloseIcon size={11} />Cancel</ActionButton>
            <ActionButton onClick={() => { onDelete(s.id); setConfirming(false); }} color={C.red} title="Confirm delete"><TrashIcon size={11} />Delete</ActionButton>
          </>
        ) : renaming ? (
          <>
            <span style={{ flex: 1 }} />
            <ActionButton onClick={() => { setRenaming(false); setName(s.name || ''); }} title="Cancel"><CloseIcon size={11} />Cancel</ActionButton>
            <ActionButton onClick={saveName} color={C.lime} title="Save name"><CheckIcon size={10} />Save</ActionButton>
          </>
        ) : (
          <>
            {s.running && <ActionButton onClick={() => onStop(s.jobId)} color={C.amber} title="Stop the running crawl"><StopIcon size={11} />Stop</ActionButton>}
            {s.canResume && <ActionButton onClick={() => onResume(s.resumePayload)} color={C.lime} title="Continue crawling this target"><PlayIcon size={11} />Continue</ActionButton>}
            {quickAssignTo && <ActionButton onClick={() => onAssign(s.id, quickAssignTo.id)} color={C.lime} title={`Add to ${quickAssignTo.name}`}><PlusIcon size={11} />Add to {quickAssignTo.name}</ActionButton>}
            <span style={{ flex: 1 }} />
            {(projects || []).length > 0 && <MoveSelect current={s.projectId} projects={projects} onChange={(pid) => onAssign(s.id, pid)} />}
            <ActionButton onClick={() => onEditScope(s)} title="Edit scope (root domains + subdomains)"><FocusIcon size={11} />Scope</ActionButton>
            <ActionButton onClick={() => { setName(s.name || ''); setRenaming(true); }} title="Rename session"><EditIcon size={11} />Rename</ActionButton>
            <ActionButton onClick={() => setConfirming(true)} title="Delete session"><TrashIcon size={11} /></ActionButton>
          </>
        )}
      </div>
    </div>
  );
}

const gridStyle = { display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '14px' };

export function Sessions({ sessions, unassigned = [], projects = [], activeProject, onNewRecon, onOpen, onStop, onResume, onRename, onDelete, onEditScope, onAssign, onViewAll }) {
  const boundEmpty = sessions.length === 0;
  const cardProps = { projects, onOpen, onStop, onResume, onRename, onDelete, onEditScope, onAssign };

  return (
    <div style={{ padding: '26px 30px 60px', maxWidth: '1180px', animation: 'dropin .25s ease' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
        <div>
          <h1 style={{ fontFamily: F.display, fontWeight: 700, fontSize: '24px', margin: 0 }}>Sessions</h1>
          <div style={{ fontSize: '13px', color: C.faint, marginTop: '3px' }}>Recon targets and their latest runs</div>
        </div>
        <button onClick={onNewRecon} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '10px 16px', borderRadius: '10px', border: 'none', background: C.lime, color: C.onLime, cursor: 'pointer', fontSize: '13px', fontWeight: 700 }}>
          <PlusIcon />New Recon
        </button>
      </div>

      {boundEmpty && (activeProject ? (
        <div style={{ padding: '44px 20px', textAlign: 'center', color: C.faint, border: `1px dashed ${C.lineStrong}`, borderRadius: '14px' }}>
          <div style={{ fontFamily: F.display, fontSize: '16px', color: C.muted, marginBottom: '6px' }}>No sessions in {activeProject.name} yet</div>
          <div style={{ fontSize: '13px', marginBottom: '14px' }}>Start a New Recon (it's filed here automatically){unassigned.length ? ', adopt an unassigned session below,' : ''} or work across every session.</div>
          <button onClick={onViewAll} style={{ padding: '9px 16px', borderRadius: '9px', border: `1px solid ${C.lineHover}`, background: C.control, color: C.textSoft, cursor: 'pointer', fontSize: '12.5px', fontWeight: 600 }}>View all sessions</button>
        </div>
      ) : (
        <div style={{ padding: '70px 20px', textAlign: 'center', color: C.faint }}>
          <div style={{ fontFamily: F.display, fontSize: '16px', color: C.muted, marginBottom: '6px' }}>No sessions yet</div>
          <div style={{ fontSize: '13px' }}>Capture JS with the extension or start a New Recon to populate this.</div>
        </div>
      ))}

      {!boundEmpty && (
        <div style={gridStyle}>
          {sessions.map((s) => <SessionCard key={s.id} s={s} {...cardProps} />)}
        </div>
      )}

      {activeProject && unassigned.length > 0 && (
        <div style={{ marginTop: boundEmpty ? '22px' : '30px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '9px', marginBottom: '4px' }}>
            <h2 style={{ fontFamily: F.display, fontWeight: 700, fontSize: '16px', margin: 0, color: C.textSoft }}>Unassigned sessions</h2>
            <span style={{ fontFamily: F.mono, fontSize: '11px', color: C.muted, background: C.control2, padding: '2px 8px', borderRadius: '20px' }}>{unassigned.length}</span>
          </div>
          <div style={{ fontSize: '12.5px', color: C.faint, marginBottom: '14px' }}>Not in {activeProject.name}. Add one to group it under this engagement.</div>
          <div style={gridStyle}>
            {unassigned.map((s) => <SessionCard key={s.id} s={s} quickAssignTo={activeProject} {...cardProps} />)}
          </div>
        </div>
      )}
    </div>
  );
}
