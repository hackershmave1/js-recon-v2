// Sessions.jsx — grid of recon targets and their latest runs. Each card shows the
// session scope (root domains + subdomain rule) and carries inline actions: Resume
// (continue crawl), Stop (in-flight job), Scope (edit), Rename (inline edit), and
// Delete (two-step confirm). Mirrors prototype SESSIONS.
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

function SessionCard({ s, onOpen, onStop, onResume, onRename, onDelete, onEditScope }) {
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

      <div onClick={stop} style={{ display: 'flex', alignItems: 'center', gap: '7px', marginTop: '14px', paddingTop: '13px', borderTop: `1px solid ${C.line}` }}>
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
            <span style={{ flex: 1 }} />
            <ActionButton onClick={() => onEditScope(s)} title="Edit scope (root domains + subdomains)"><FocusIcon size={11} />Scope</ActionButton>
            <ActionButton onClick={() => { setName(s.name || ''); setRenaming(true); }} title="Rename session"><EditIcon size={11} />Rename</ActionButton>
            <ActionButton onClick={() => setConfirming(true)} title="Delete session"><TrashIcon size={11} /></ActionButton>
          </>
        )}
      </div>
    </div>
  );
}

export function Sessions({ sessions, onNewRecon, onOpen, onStop, onResume, onRename, onDelete, onEditScope }) {
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

      {sessions.length === 0 && (
        <div style={{ padding: '70px 20px', textAlign: 'center', color: C.faint }}>
          <div style={{ fontFamily: F.display, fontSize: '16px', color: C.muted, marginBottom: '6px' }}>No sessions yet</div>
          <div style={{ fontSize: '13px' }}>Capture JS with the extension or start a New Recon to populate this.</div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '14px' }}>
        {sessions.map((s) => (
          <SessionCard key={s.id} s={s} onOpen={onOpen} onStop={onStop} onResume={onResume} onRename={onRename} onDelete={onDelete} onEditScope={onEditScope} />
        ))}
      </div>
    </div>
  );
}
