// EngagementPicker.jsx — always-visible engagement selector. Shows the ACTIVE engagement
// the current session is bound to, and lets you switch (which rotates the session, because
// the server binding is immutable), create one inline, or go Solo/Standalone. Advanced
// per-session scope overrides tuck behind a toggle. Switching WHILE capturing starts a fresh
// session and clears the current captures, so we surface that inline and make Switch an
// explicit button press (native window.confirm is suppressed in the popup, so it can't be
// the safeguard).
import { useState, useEffect } from 'preact/hooks';
import { C, F } from '../theme.js';
import { Switch } from './ui.jsx';
import { resolveEffectiveConfig } from '../../../modules/project-config.js';

function SectionLabel({ children }) {
  return (
    <span style={{ fontSize: '10.5px', color: C.faint, fontWeight: 700, letterSpacing: '0.8px' }}>
      {children}
    </span>
  );
}

export function EngagementPicker({ vm }) {
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newScope, setNewScope] = useState('');
  // Raw scope text (Standalone box + the project scope override), kept raw so spaces/commas
  // type freely; parsed to a list on change (project) or at start (Standalone).
  const [scopeText, setScopeText] = useState('');
  const [showScope, setShowScope] = useState(false);

  const input = {
    flex: 1, minWidth: 0, background: C.inset, border: `1px solid ${C.lineStrong}`,
    borderRadius: '8px', color: C.text, fontFamily: F.mono, fontSize: '11.5px', padding: '7px 9px', outline: 'none'
  };
  const selectStyle = { ...input, width: '100%', fontFamily: F.body, cursor: 'pointer' };

  const project = vm.projectId ? (vm.projects || []).find((p) => p.id === vm.projectId) : null;
  const preview = project ? resolveEffectiveConfig(project.defaults || {}, vm.overrides) : null;
  const previewScope = (preview && preview.effective && preview.effective.scope) || {};   // guard a partial defaults doc
  const overridden = new Set(preview ? preview.overrideKeys : []);
  const inheritedScope = (p) => (((p && p.defaults && p.defaults.scope && p.defaults.scope.rootDomains) || [])).join(' ');

  // Keep the scope box in sync with the current selection (inherited project scope, or the
  // standalone default) so the shown scope always equals what Start applies. Depends on `project`
  // too, so a project seeded before the engagements list loads re-syncs its inherited scope once
  // the list arrives (instead of being stuck on the global default).
  useEffect(() => {
    setScopeText(project ? inheritedScope(project) : (vm.startScopeDefault || ''));
  }, [vm.projectId, project]);

  const pick = (v) => {
    if (v === '__new__') { setCreating(true); return; }
    setCreating(false);
    vm.selectProject(v || null);   // scopeText re-syncs via the effect on vm.projectId change
  };
  const onScopeInput = (value) => {
    setScopeText(value);
    if (!project) return;                        // Standalone parses at start
    const list = value.split(/[\s,]+/).filter(Boolean);
    const inherited = (project.defaults && project.defaults.scope && project.defaults.scope.rootDomains) || [];
    if (JSON.stringify(list) === JSON.stringify(inherited)) vm.clearOverride('scope', 'rootDomains');
    else vm.setOverride('scope', 'rootDomains', list);
  };
  const toggleSubs = () => {
    const next = !(previewScope.includeSubdomains !== false);
    const inherited = !(project.defaults && project.defaults.scope && project.defaults.scope.includeSubdomains === false);
    if (next === inherited) vm.clearOverride('scope', 'includeSubdomains');
    else vm.setOverride('scope', 'includeSubdomains', next);
  };

  const staged = vm.projectId || null;
  const active = vm.activeProjectId || null;
  const isSwitch = staged !== active;
  const stagedName = project ? project.name : 'Solo · standalone';
  const activeLabel = vm.activeProjectName || (active ? 'engagement' : 'Solo · standalone');

  return (
    <div style={{ marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <SectionLabel>ENGAGEMENT</SectionLabel>
        <span style={{ fontSize: '10px', color: C.faint }}>
          active · <span style={{ color: active ? C.lime : C.muted, fontFamily: F.mono }}>{activeLabel}</span>
        </span>
      </div>

      <select style={selectStyle} value={creating ? '__new__' : (vm.projectId || '')} onChange={(e) => pick(e.target.value)}>
        <option value="">Solo · standalone (no engagement)</option>
        {(vm.projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        <option value="__new__">＋ New engagement…</option>
      </select>

      {creating && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <input value={newName} placeholder="engagement name" onInput={(e) => setNewName(e.target.value)} style={input} />
          <input value={newScope} placeholder="root domains (e.g. target.com)" onInput={(e) => setNewScope(e.target.value)} style={input} />
          <div style={{ display: 'flex', gap: '8px' }}>
            <button onClick={async () => {
              const r = await vm.createProject(newName, newScope);
              if (r && r.success) { setCreating(false); setNewName(''); setNewScope(''); setScopeText(inheritedScope(r.project)); }
            }} style={{ padding: '7px 14px', borderRadius: '8px', border: 'none', background: C.lime, color: C.onLime, cursor: 'pointer', fontSize: '11.5px', fontWeight: 700 }}>Create</button>
            <button onClick={() => setCreating(false)} style={{ padding: '7px 12px', borderRadius: '8px', border: `1px solid ${C.lineHover}`, background: C.control, color: C.muted, cursor: 'pointer', fontSize: '11.5px', fontWeight: 600 }}>Cancel</button>
          </div>
        </div>
      )}

      {!creating && (
        <>
          <button onClick={() => setShowScope((s) => !s)} style={{
            display: 'flex', alignItems: 'center', gap: '6px', border: 'none', background: 'none',
            cursor: 'pointer', padding: 0, color: C.muted, fontSize: '10.5px', textAlign: 'left'
          }}>
            <span>{showScope ? '▾' : '▸'} Scope{overridden.size ? ' · overridden' : ''}</span>
          </button>

          {showScope && project && preview && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <span style={{ fontSize: '10px', color: C.faint }}>
                SCOPE · <span style={{ color: overridden.has('scope.rootDomains') ? C.amber : C.dim }}>{overridden.has('scope.rootDomains') ? 'overridden' : 'inherited'}</span>
              </span>
              <input value={scopeText} placeholder="root domains" onInput={(e) => onScopeInput(e.target.value)} style={input} />
              <button onClick={toggleSubs} style={{ display: 'flex', alignItems: 'center', gap: '6px', border: 'none', background: 'none', cursor: 'pointer', padding: 0 }}>
                <span style={{ fontSize: '10.5px', color: C.muted }}>+ subdomains{overridden.has('scope.includeSubdomains') ? ' · overridden' : ''}</span>
                <Switch on={previewScope.includeSubdomains !== false} variant="sm" />
              </button>
            </div>
          )}

          {showScope && !project && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <input value={scopeText} placeholder="root domains (e.g. app.target.com)"
                onInput={(e) => setScopeText(e.target.value)} style={input} />
              <button onClick={vm.toggleSubdomains} style={{ display: 'flex', alignItems: 'center', gap: '6px', border: 'none', background: 'none', cursor: 'pointer', padding: 0 }}>
                <span style={{ fontSize: '10.5px', color: C.muted }}>+ subdomains</span>
                <Switch on={vm.includeSubdomains} variant="sm" />
              </button>
            </div>
          )}

          {vm.capturing && (
            <div style={{ fontSize: '10px', color: C.amber }}>
              {isSwitch ? 'Switching' : 'Restarting'} starts a new session and clears the current captures.
            </div>
          )}

          <button onClick={() => vm.startNewSession(scopeText)} style={{
            width: '100%', padding: '8px', borderRadius: '9px', border: 'none',
            background: isSwitch ? C.lime : C.control, color: isSwitch ? C.onLime : C.textSoft,
            cursor: 'pointer', fontSize: '12px', fontWeight: 700
          }}>
            {isSwitch ? `Switch to ${stagedName}` : 'Restart session'}
          </button>
        </>
      )}
    </div>
  );
}
