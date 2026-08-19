// SettingsView.jsx — inline settings screen (replaces the old options.html page).
// Sections: Connection, Capture rules, Noise denylist, About. Mirrors the
// "RECON Capture" prototype SETTINGS view. Sign-IN lives in LoginView (the gate); Settings only
// shows the signed-in account summary, since it is reachable only when already signed in.
import { C, F, TAG_COLOR } from '../theme.js';
import { Switch } from './ui.jsx';
import {
  BackIcon, SearchIcon, LinkIcon, GlobeIcon, EyeOffIcon,
  PulseIcon, SpinnerIcon, CloseIcon
} from '../icons.jsx';

const inputStyle = {
  width: '100%', background: C.inset, border: `1px solid ${C.lineStrong}`, borderRadius: '8px',
  color: C.text, fontFamily: F.mono, fontSize: '12px', padding: '9px 11px', outline: 'none'
};

function SectionHeader({ icon, children }) {
  return (
    <div style={{
      fontSize: '10px', color: C.faint, fontWeight: 700, letterSpacing: '0.9px',
      marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '7px'
    }}>
      {icon}{children}
    </div>
  );
}

function Card({ children, mb = 20 }) {
  return (
    <div style={{
      background: C.panel, border: `1px solid ${C.line}`, borderRadius: '12px',
      padding: '14px', marginBottom: `${mb}px`
    }}>{children}</div>
  );
}

function Label({ children, mb = 5 }) {
  return <label style={{ display: 'block', fontSize: '10px', color: C.dim, marginBottom: `${mb}px` }}>{children}</label>;
}

// Auth feedback: a bad/expired token fails CLOSED to the shared tenant, which is
// otherwise silent (the workspace live-indicator only shows when already paired). Reflect
// the last save-files `paired` ack so a typo is visible. `paired` is undefined until the
// first upload under the current token.
function PairedHint({ token, paired }) {
  const has = !!(token || '').trim();
  let color = C.faint;
  let text = 'No token · captures go to the shared tenant';
  if (has && paired === true) { color = C.lime; text = 'Paired · captures route to your tenant'; }
  else if (has && paired === false) { color = C.pink; text = 'Token not accepted · using shared tenant'; }
  else if (has) { text = 'Token set · verifies on next capture'; }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '8px', fontSize: '10.5px', color }}>
      <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: color, flex: '0 0 auto' }} />
      {text}
    </div>
  );
}

// Signed-in account summary. Sign-IN itself lives in LoginView (the gate); Settings is reachable
// only when signed in, so this always shows the identity + paired indicator + Log out. The session
// token is held by the service worker (never typed/stored here).
function AccountSection({ vm }) {
  return (
    <div>
      <Label>Signed in <span style={{ color: C.faint }}>(captures route to your tenant)</span></Label>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: '12.5px', color: C.text, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {vm.authUser || 'user'}{vm.authTenantName ? ` · ${vm.authTenantName}` : ''}
          </div>
          <PairedHint token="x" paired={vm.paired} />
        </div>
        <button onClick={vm.signOut} style={{
          flex: '0 0 auto', padding: '8px 12px', borderRadius: '8px', border: `1px solid ${C.lineStrong}`,
          background: C.control, color: C.textSoft, cursor: 'pointer', fontSize: '12px', fontWeight: 600
        }}>Log out</button>
      </div>
    </div>
  );
}

const CONN = {
  ok: { label: 'Connected', color: C.lime, pulse: true },
  testing: { label: 'Testing…', color: C.amber, pulse: false },
  fail: { label: 'Unreachable', color: C.pink, pulse: false }
};

export function SettingsView({ vm }) {
  const conn = CONN[vm.connState] || CONN.ok;
  return (
    <div>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '11px', padding: '15px 17px', borderBottom: `1px solid ${C.line}` }}>
        <button class="pp-iconbtn" onClick={vm.closeSettings} aria-label="Back" style={{
          width: '30px', height: '30px', borderRadius: '8px', border: `1px solid ${C.lineStrong}`,
          background: C.control, color: C.textSoft, cursor: 'pointer', display: 'flex',
          alignItems: 'center', justifyContent: 'center'
        }}><BackIcon /></button>
        <div style={{ flex: 1, fontFamily: F.display, fontWeight: 700, fontSize: '15px' }}>Settings</div>
      </div>

      <div style={{ maxHeight: '560px', overflowY: 'auto', padding: '16px 17px 20px' }}>
        {/* CONNECTION */}
        <SectionHeader icon={<LinkIcon />}>CONNECTION</SectionHeader>
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '7px', fontSize: '11.5px', color: conn.color, fontWeight: 600 }}>
              <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: conn.color, position: 'relative', display: 'inline-block' }}>
                {conn.pulse && <span style={{ position: 'absolute', width: '7px', height: '7px', borderRadius: '50%', background: conn.color, animation: 'pulse 1.1s infinite' }} />}
              </span>
              {conn.label}
            </span>
            <span style={{ fontFamily: F.mono, fontSize: '10.5px', color: C.faint }}>{vm.latency}</span>
          </div>
          <Label>Workspace URL</Label>
          <input value={vm.wsUrl} onInput={(e) => vm.setWsUrl(e.target.value)} placeholder="http://localhost:8000"
                 style={{ ...inputStyle, marginBottom: '12px' }} />
          <button onClick={vm.testConnection} style={{
            width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
            padding: '9px', borderRadius: '9px',
            border: `1px solid ${vm.connState === 'testing' ? C.amber : C.lineHover}`,
            background: C.control, color: vm.connState === 'testing' ? C.amber : C.textSoft,
            cursor: 'pointer', fontSize: '12.5px', fontWeight: 600
          }}>
            <span style={{ width: '14px', height: '14px', display: 'inline-flex' }}>
              {vm.connState === 'testing' ? <SpinnerIcon /> : <PulseIcon />}
            </span>
            {vm.connState === 'testing' ? 'Testing…' : 'Test connection'}
          </button>
          <div style={{ borderTop: `1px solid ${C.line}`, margin: '14px 0 12px' }} />
          <AccountSection vm={vm} />
        </Card>

        {/* CAPTURE RULES */}
        <SectionHeader icon={<GlobeIcon />}>CAPTURE RULES</SectionHeader>
        <Card>
          <Label>Standalone default scope <span style={{ color: C.faint }}>(used when no project is selected)</span></Label>
          <input value={vm.defScope} onInput={(e) => vm.setDefScope(e.target.value)} placeholder="auto (active tab domain)"
                 style={{ ...inputStyle, color: C.lime, marginBottom: '13px' }} />
          <button onClick={vm.toggleSubdomains} style={{
            width: '100%', display: 'flex', alignItems: 'center', gap: '10px', padding: '4px 0 13px',
            border: 'none', background: 'none', cursor: 'pointer'
          }}>
            <span style={{ flex: 1, textAlign: 'left', fontSize: '12.5px', color: C.textSoft }}>Include subdomains by default</span>
            <Switch on={vm.includeSubdomains} />
          </button>
          <Label mb={8}>Out-of-scope assets</Label>
          <div style={{ display: 'flex', gap: '6px', marginBottom: '14px' }}>
            {[{ k: 'tag', l: 'Tag' }, { k: 'mute', l: 'Mute' }, { k: 'exclude', l: 'Exclude' }].map((o) => {
              const on = vm.outOfScopeMode === o.k;
              return (
                <button key={o.k} onClick={() => vm.setOutOfScopeMode(o.k)} style={{
                  flex: 1, padding: '8px', borderRadius: '8px',
                  border: `1px solid ${on ? C.lime : C.lineStrong}`,
                  background: on ? C.lime : 'transparent', color: on ? C.onLime : C.muted,
                  cursor: 'pointer', fontSize: '11.5px', fontWeight: 600
                }}>{o.l}</button>
              );
            })}
          </div>
          {/* Max is 10 MB, not the mockup's 25: the backend hard-rejects any file
              over 10 MB (SecurityValidator.MAX_JS_CONTENT_SIZE), so allowing more
              here would only produce assets the server 422s. */}
          <Label mb={8}>Max asset size <span style={{ color: C.lime, fontFamily: F.mono }}>{vm.maxAssetMb} MB</span></Label>
          <input type="range" min="1" max="10" step="1" value={vm.maxAssetMb}
                 onInput={(e) => vm.setMaxAssetMb(Number(e.target.value))}
                 style={{ width: '100%', accentColor: C.lime }} />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '9.5px', color: C.faint, fontFamily: F.mono, marginTop: '2px' }}>
            <span>1 MB</span><span>skip larger files</span><span>10 MB</span>
          </div>
        </Card>

        {/* NOISE DENYLIST */}
        <SectionHeader icon={<EyeOffIcon />}>NOISE DENYLIST</SectionHeader>
        <Card>
          <button onClick={vm.toggleDefaultProfile} style={{
            width: '100%', display: 'flex', alignItems: 'center', gap: '10px', padding: '0 0 12px',
            border: 'none', background: 'none', cursor: 'pointer', borderBottom: `1px solid ${C.line}`, marginBottom: '12px'
          }}>
            <span style={{ flex: 1, textAlign: 'left' }}>
              <span style={{ display: 'block', fontSize: '12.5px', color: C.text, fontWeight: 600 }}>Default profile</span>
              <span style={{ display: 'block', fontSize: '10.5px', color: C.faint }}>WordPress, analytics, ad &amp; CDN libraries</span>
            </span>
            <Switch on={vm.denyDefaultProfile} />
          </button>
          {vm.denyRules.map((r, i) => {
            const tag = TAG_COLOR[r.tag] || TAG_COLOR.HOST;
            return (
              <div key={`${r.tag}-${r.pattern}-${i}`} style={{
                display: 'flex', alignItems: 'center', gap: '9px', padding: '7px 9px',
                background: C.inset, border: `1px solid ${C.line}`, borderRadius: '8px', marginBottom: '6px'
              }}>
                <span style={{ fontSize: '8.5px', fontWeight: 700, color: tag.c, background: tag.bg, padding: '2px 6px', borderRadius: '5px', flex: '0 0 auto' }}>{r.tag}</span>
                <span style={{ flex: 1, minWidth: 0, fontFamily: F.mono, fontSize: '11.5px', color: C.textSoft, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.pattern}</span>
                <button class="pp-rule-remove" onClick={() => vm.removeRule(i)} aria-label="Remove rule" style={{
                  flex: '0 0 auto', width: '22px', height: '22px', borderRadius: '6px', border: 'none',
                  background: 'none', color: C.faint, cursor: 'pointer', display: 'flex',
                  alignItems: 'center', justifyContent: 'center'
                }}><CloseIcon /></button>
              </div>
            );
          })}
          <div style={{ display: 'flex', gap: '7px', marginTop: '9px' }}>
            <input value={vm.newRule} onInput={(e) => vm.setNewRule(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter') vm.addRule(); }}
                   placeholder="*.doubleclick.net  or  /wp-content/*"
                   style={{ ...inputStyle, flex: 1, minWidth: 0, fontSize: '11.5px', padding: '8px 10px' }} />
            <button onClick={vm.addRule} style={{
              flex: '0 0 auto', padding: '0 13px', borderRadius: '8px', border: 'none',
              background: C.lime, color: C.onLime, cursor: 'pointer', fontSize: '12px', fontWeight: 700
            }}>Add</button>
          </div>
        </Card>

        {/* ABOUT */}
        <SectionHeader>ABOUT</SectionHeader>
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: '12px', padding: '14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '11px' }}>
            <div style={{ width: '30px', height: '30px', borderRadius: '8px', background: C.lime, display: 'flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 30px' }}>
              <SearchIcon />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '13px' }}>RECON Capture</div>
              <div style={{ fontSize: '10.5px', color: C.faint }}>JavaScript recon · passive collector</div>
            </div>
            <span style={{ fontFamily: F.mono, fontSize: '11px', color: C.lime, background: 'rgba(205,235,69,0.1)', border: '1px solid rgba(205,235,69,0.2)', padding: '3px 9px', borderRadius: '6px' }}>v{vm.version}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
