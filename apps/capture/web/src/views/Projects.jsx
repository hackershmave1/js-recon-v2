// Projects.jsx — engagement landing (the workspace's default view). Full CRUD over
// projects: create (name + default scope), rename, edit default scope, delete. Selecting
// a project scopes the Sessions list to it; the Standalone card is the escape hatch to
// work across every session without picking a project. Mirrors the Sessions card design.
import { useState } from 'preact/hooks';
import { C, F } from '../theme.js';
import { relTime } from '../transforms.js';
import { PlusIcon, EditIcon, TrashIcon, CheckIcon, CloseIcon, FocusIcon, FolderIcon, ArrowRight } from '../icons.jsx';
import { DomainListInput } from './DomainListInput.jsx';
import { parseDomainList } from '../scopeImport.js';

const inputStyle = {
  flex: 1, minWidth: 0, background: C.inset, border: `1px solid ${C.lime}`, borderRadius: '7px',
  color: C.text, fontFamily: F.mono, fontSize: '13px', padding: '5px 9px', outline: 'none'
};
const formInput = { ...inputStyle, width: '100%', border: `1px solid ${C.lineStrong}`, fontFamily: F.body, fontSize: '13px', padding: '9px 11px' };
const ghostBtn = { padding: '8px 14px', borderRadius: '8px', border: `1px solid ${C.lineHover}`, background: C.control, color: C.muted, cursor: 'pointer', fontSize: '12px', fontWeight: 600 };
const limeBtn = { padding: '8px 16px', borderRadius: '8px', border: 'none', background: C.lime, color: C.onLime, cursor: 'pointer', fontSize: '12px', fontWeight: 700 };

const scopeOf = (p) => ((p.defaults && p.defaults.scope && p.defaults.scope.rootDomains) || []);

function ActionButton({ onClick, color, title, children }) {
  return (
    <button onClick={onClick} title={title} style={{ display: 'flex', alignItems: 'center', gap: '5px', padding: '5px 9px', borderRadius: '7px', border: `1px solid ${C.lineStrong}`, background: C.control, color: color || C.muted, cursor: 'pointer', fontSize: '11px', fontWeight: 600 }}>
      {children}
    </button>
  );
}

function ProjectCard({ p, sessionCount, fileCount, onOpen, onRename, onRescope, onDelete }) {
  const [mode, setMode] = useState(null);   // null | 'rename' | 'scope' | 'confirm'
  const [name, setName] = useState(p.name || '');
  const doms = scopeOf(p);
  const [scope, setScope] = useState(doms.join(' '));
  const stop = (e) => e.stopPropagation();

  const saveName = () => { const t = name.trim(); if (t) { onRename(p.id, t); setMode(null); } };
  const saveScope = () => { onRescope(p.id, parseDomainList(scope)); setMode(null); };

  return (
    <div class="ws-card" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: '14px', padding: '18px 20px', transition: 'border-color .12s' }}>
      <button onClick={() => onOpen(p)} style={{ display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'inherit' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '14px' }}>
          <span style={{ width: '30px', height: '30px', borderRadius: '8px', background: C.control2, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.lime, flex: '0 0 auto' }}><FolderIcon size={15} /></span>
          {mode === 'rename' ? (
            <input value={name} onClick={stop} onInput={(e) => setName(e.target.value)} autofocus
              onKeyDown={(e) => { if (e.key === 'Enter') saveName(); if (e.key === 'Escape') { setMode(null); setName(p.name || ''); } }}
              style={inputStyle} />
          ) : (
            <span style={{ flex: 1, minWidth: 0, fontFamily: F.display, fontSize: '16px', fontWeight: 700, color: C.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</span>
          )}
          <span style={{ color: C.faint, display: 'inline-flex' }}><ArrowRight size={14} /></span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '14px', fontSize: '11.5px', fontFamily: F.mono, color: doms.length ? C.teal : C.faint, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          <span style={{ flex: '0 0 13px', display: 'inline-flex' }}><FocusIcon size={12} /></span>
          <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{doms.length ? doms.join(', ') : 'no default scope'}</span>
        </div>

        <div style={{ display: 'flex', gap: '22px' }}>
          <div><div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '20px', color: C.text }}>{sessionCount}</div><div style={{ fontSize: '10.5px', color: C.faint }}>sessions</div></div>
          <div><div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '20px', color: C.text }}>{fileCount}</div><div style={{ fontSize: '10.5px', color: C.faint }}>files</div></div>
          <div style={{ marginLeft: 'auto', textAlign: 'right' }}><div style={{ fontSize: '11px', color: C.muted, fontFamily: F.mono }}>{relTime(p.createdAt)}</div><div style={{ fontSize: '10.5px', color: C.faint }}>created</div></div>
        </div>
      </button>

      {mode === 'scope' && (
        <div style={{ marginTop: '12px' }}>
          <DomainListInput value={scope} onChange={setScope} rows={3} placeholder={'root domains (e.g. *.target.com)'} />
        </div>
      )}

      <div onClick={stop} style={{ display: 'flex', alignItems: 'center', gap: '7px', marginTop: '14px', paddingTop: '13px', borderTop: `1px solid ${C.line}` }}>
        {mode === 'confirm' ? (
          <>
            <span style={{ flex: 1, fontSize: '11.5px', color: C.textSoft }}>Delete this project? Its sessions become standalone.</span>
            <ActionButton onClick={() => setMode(null)} title="Cancel"><CloseIcon size={11} />Cancel</ActionButton>
            <ActionButton onClick={() => { onDelete(p.id); setMode(null); }} color={C.red} title="Confirm delete"><TrashIcon size={11} />Delete</ActionButton>
          </>
        ) : mode === 'rename' ? (
          <>
            <span style={{ flex: 1 }} />
            <ActionButton onClick={() => { setMode(null); setName(p.name || ''); }} title="Cancel"><CloseIcon size={11} />Cancel</ActionButton>
            <ActionButton onClick={saveName} color={C.lime} title="Save name"><CheckIcon size={10} />Save</ActionButton>
          </>
        ) : mode === 'scope' ? (
          <>
            <span style={{ flex: 1 }} />
            <ActionButton onClick={() => { setMode(null); setScope(doms.join(' ')); }} title="Cancel"><CloseIcon size={11} />Cancel</ActionButton>
            <ActionButton onClick={saveScope} color={C.lime} title="Save scope"><CheckIcon size={10} />Save</ActionButton>
          </>
        ) : (
          <>
            <ActionButton onClick={() => onOpen(p)} color={C.lime} title="Open this project's sessions"><FolderIcon size={11} />Open</ActionButton>
            <span style={{ flex: 1 }} />
            <ActionButton onClick={() => { setScope(doms.join(' ')); setMode('scope'); }} title="Edit default scope"><FocusIcon size={11} />Scope</ActionButton>
            <ActionButton onClick={() => { setName(p.name || ''); setMode('rename'); }} title="Rename project"><EditIcon size={11} />Rename</ActionButton>
            <ActionButton onClick={() => setMode('confirm')} title="Delete project"><TrashIcon size={11} /></ActionButton>
          </>
        )}
      </div>
    </div>
  );
}

export function Projects({ projects, sessions, onOpenProject, onStandalone, onCreate, onRename, onRescope, onDelete }) {
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newScope, setNewScope] = useState('');
  const countFor = (id) => (sessions || []).filter((s) => s.projectId === id).length;
  // Rollup summed from the already-loaded session list (no extra API scan): total files
  // captured across the engagement's sessions.
  const filesFor = (id) => (sessions || []).filter((s) => s.projectId === id).reduce((n, s) => n + (s.fileCount || 0), 0);
  const looseCount = (sessions || []).filter((s) => !s.projectId).length;

  const submit = () => {
    const n = newName.trim();
    if (!n) return;
    onCreate(n, parseDomainList(newScope));
    setNewName(''); setNewScope(''); setCreating(false);
  };

  return (
    <div style={{ padding: '26px 30px 60px', maxWidth: '1180px', animation: 'dropin .25s ease' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
        <div>
          <h1 style={{ fontFamily: F.display, fontWeight: 700, fontSize: '24px', margin: 0 }}>Projects</h1>
          <div style={{ fontSize: '13px', color: C.faint, marginTop: '3px' }}>Engagements — pick one to work in, or open all sessions.</div>
        </div>
        <button onClick={() => setCreating((v) => !v)} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '10px 16px', borderRadius: '10px', border: 'none', background: C.lime, color: C.onLime, cursor: 'pointer', fontSize: '13px', fontWeight: 700 }}>
          <PlusIcon />New project
        </button>
      </div>

      {creating && (
        <div style={{ background: C.panel, border: `1px solid ${C.lineStrong}`, borderRadius: '12px', padding: '16px 18px', marginBottom: '16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <input value={newName} placeholder="Project name (e.g. HoneyBook engagement)" autofocus onInput={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') setCreating(false); }} style={formInput} />
          <DomainListInput value={newScope} onChange={setNewScope} rows={3} placeholder={'Default root domains — one per line, or JSON / CSV / commas'} />
          <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
            <button onClick={() => { setCreating(false); setNewName(''); setNewScope(''); }} style={ghostBtn}>Cancel</button>
            <button onClick={submit} style={limeBtn}>Create project</button>
          </div>
        </div>
      )}

      {/* Standalone escape hatch — work across all sessions without picking a project */}
      <button onClick={onStandalone} class="ws-card" style={{ width: '100%', textAlign: 'left', background: C.panel2, border: `1px dashed ${C.lineStrong}`, borderRadius: '12px', padding: '14px 18px', marginBottom: '16px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '12px' }}>
        <span style={{ width: '30px', height: '30px', borderRadius: '8px', background: C.control2, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.muted, flex: '0 0 auto' }}><FocusIcon size={15} /></span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: '13.5px', fontWeight: 600, color: C.textSoft }}>Standalone · all sessions</span>
          <span style={{ display: 'block', fontSize: '11.5px', color: C.faint, marginTop: '2px' }}>Skip projects and work across every session{looseCount ? ` · ${looseCount} without a project` : ''}.</span>
        </span>
        <span style={{ color: C.faint, display: 'inline-flex' }}><ArrowRight size={14} /></span>
      </button>

      {projects.length === 0 && !creating && (
        <div style={{ padding: '50px 20px', textAlign: 'center', color: C.faint }}>
          <div style={{ fontFamily: F.display, fontSize: '16px', color: C.muted, marginBottom: '6px' }}>No projects yet</div>
          <div style={{ fontSize: '13px' }}>Create one to group an engagement's sessions under a shared scope.</div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '14px' }}>
        {projects.map((p) => (
          <ProjectCard key={p.id} p={p} sessionCount={countFor(p.id)} fileCount={filesFor(p.id)} onOpen={onOpenProject} onRename={onRename} onRescope={onRescope} onDelete={onDelete} />
        ))}
      </div>
    </div>
  );
}
