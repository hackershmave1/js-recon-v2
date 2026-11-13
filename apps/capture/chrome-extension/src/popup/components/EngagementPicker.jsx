// EngagementPicker.jsx — the optional engagement for a NEW session, chosen before Start. A capture
// that Continues a session takes that session's engagement (the platform binds a session to one,
// immutably), and a running capture can't change it, so both show it locked instead.
import { useState } from 'preact/hooks';
import { C, F } from '../theme.js';

const rowStyle = {
  display: 'flex', alignItems: 'center', gap: '9px', padding: '8px 12px', background: C.inset,
  border: `1px solid ${C.lineStrong}`, borderRadius: '10px', fontSize: '11.5px', color: C.textSoft
};
const labelStyle = { fontSize: '10px', color: C.faint, fontWeight: 700, letterSpacing: '0.6px', flex: '0 0 auto' };
const input = {
  flex: 1, minWidth: 0, background: C.inset, border: `1px solid ${C.lineStrong}`,
  borderRadius: '8px', color: C.text, fontFamily: F.mono, fontSize: '11.5px', padding: '7px 9px', outline: 'none'
};

// The engagement as a fact, not a control (Continue, or a capture in progress).
export function EngagementLocked({ name, why }) {
  return (
    <div style={rowStyle}>
      <span style={labelStyle}>ENGAGEMENT</span>
      <span style={{ flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</span>
      <span style={{ color: C.faint, fontSize: '10px', flex: '0 0 auto' }}>{why}</span>
    </div>
  );
}

export function EngagementPicker({ vm, scope }) {
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');

  const pick = (value) => {
    if (value === '__new__') { setCreating(true); return; }
    setCreating(false);
    vm.selectProject(value || null);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <label style={{ ...rowStyle, padding: '4px 6px 4px 12px' }}>
        <span style={labelStyle}>ENGAGEMENT</span>
        <select value={creating ? '__new__' : (vm.projectId || '')} onChange={(e) => pick(e.target.value)} style={{
          flex: 1, minWidth: 0, background: 'transparent', border: 'none', color: C.textSoft,
          fontFamily: F.body, fontSize: '11.5px', padding: '5px 0', outline: 'none', cursor: 'pointer'
        }}>
          <option value="">Solo · standalone (no engagement)</option>
          {(vm.projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          <option value="__new__">＋ New engagement…</option>
        </select>
      </label>
      {creating && (
        <div style={{ display: 'flex', gap: '8px' }}>
          <input value={newName} placeholder="engagement name" aria-label="New engagement name"
                 onInput={(e) => setNewName(e.target.value)} style={input} />
          <button onClick={async () => {
            // The new engagement's default scope is the scope being captured.
            const res = await vm.createProject(newName, scope);
            if (res && res.ok) { setCreating(false); setNewName(''); }
          }} style={{ padding: '7px 14px', borderRadius: '8px', border: 'none', background: C.lime, color: C.onLime, cursor: 'pointer', fontSize: '11.5px', fontWeight: 700 }}>Create</button>
          <button onClick={() => setCreating(false)} style={{ padding: '7px 12px', borderRadius: '8px', border: `1px solid ${C.lineHover}`, background: C.control, color: C.muted, cursor: 'pointer', fontSize: '11.5px', fontWeight: 600 }}>Cancel</button>
        </div>
      )}
    </div>
  );
}
