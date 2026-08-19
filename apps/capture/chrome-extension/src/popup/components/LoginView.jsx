// LoginView.jsx — the sign-in gate: the FIRST screen a signed-out operator sees (app.jsx renders
// it whenever there is no auth token). It owns the workspace URL (must be set before login) +
// test-connection + the username/password form. On success the worker holds the session token and
// the gate flips to Home. Mirrors the popup's visual system (theme C/F) and the old Settings
// AuthSection form, now promoted to a first-class step in the funnel.
import { useState } from 'preact/hooks';
import { C, F } from '../theme.js';
import { SearchIcon, LinkIcon, PulseIcon, SpinnerIcon } from '../icons.jsx';

const inputStyle = {
  width: '100%', background: C.inset, border: `1px solid ${C.lineStrong}`, borderRadius: '8px',
  color: C.text, fontFamily: F.mono, fontSize: '12px', padding: '9px 11px', outline: 'none'
};

const CONN = {
  ok: { label: 'Connected', color: C.lime, pulse: true },
  testing: { label: 'Testing…', color: C.amber, pulse: false },
  fail: { label: 'Unreachable', color: C.pink, pulse: false }
};

function Label({ children }) {
  return <label style={{ display: 'block', fontSize: '10px', color: C.dim, marginBottom: '5px' }}>{children}</label>;
}

export function LoginView({ vm }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const conn = CONN[vm.connState] || CONN.ok;

  const submit = async () => {
    if (!username.trim() || !password || busy) return;
    setBusy(true); setError('');
    const res = await vm.signIn(username.trim(), password);
    setBusy(false);
    if (res && res.success) { setUsername(''); setPassword(''); }
    else {
      setError(res?.status === 401 ? 'Invalid username or password'
        : res?.status === 503 ? 'Auth is not configured on the server'
        : (res?.error || 'Sign in failed'));
    }
  };

  return (
    <div>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '11px', padding: '17px', borderBottom: `1px solid ${C.line}` }}>
        <div style={{
          width: '30px', height: '30px', borderRadius: '8px', background: C.lime, display: 'flex',
          alignItems: 'center', justifyContent: 'center', flex: '0 0 30px', boxShadow: '0 0 16px rgba(205,235,69,0.4)'
        }}><SearchIcon /></div>
        <div style={{ flex: 1, lineHeight: 1.15 }}>
          <div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '14px', letterSpacing: '-0.2px' }}>RECON Capture</div>
          <div style={{ fontSize: '10.5px', color: C.faint }}>Sign in to your workspace</div>
        </div>
      </div>

      <div style={{ padding: '16px 17px 20px' }}>
        {/* workspace URL + connection test — the URL must be set before login */}
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: '12px', padding: '14px', marginBottom: '16px' }}>
          <div style={{
            fontSize: '10px', color: C.faint, fontWeight: 700, letterSpacing: '0.9px',
            marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '7px'
          }}><LinkIcon />WORKSPACE</div>
          <Label>Workspace URL</Label>
          <input value={vm.wsUrl} onInput={(e) => vm.setWsUrl(e.target.value)} placeholder="http://localhost:8000"
                 style={{ ...inputStyle, marginBottom: '12px' }} />
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '7px', fontSize: '11.5px', color: conn.color, fontWeight: 600 }}>
              <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: conn.color, position: 'relative', display: 'inline-block' }}>
                {conn.pulse && <span style={{ position: 'absolute', width: '7px', height: '7px', borderRadius: '50%', background: conn.color, animation: 'pulse 1.1s infinite' }} />}
              </span>
              {conn.label}
              {vm.latency && <span style={{ fontFamily: F.mono, fontSize: '10.5px', color: C.faint, marginLeft: '2px' }}>{vm.latency}</span>}
            </span>
            <button onClick={vm.testConnection} style={{
              flex: '0 0 auto', display: 'flex', alignItems: 'center', gap: '7px', padding: '8px 12px', borderRadius: '9px',
              border: `1px solid ${vm.connState === 'testing' ? C.amber : C.lineHover}`,
              background: C.control, color: vm.connState === 'testing' ? C.amber : C.textSoft,
              cursor: 'pointer', fontSize: '12px', fontWeight: 600
            }}>
              <span style={{ width: '14px', height: '14px', display: 'inline-flex' }}>
                {vm.connState === 'testing' ? <SpinnerIcon /> : <PulseIcon />}
              </span>
              Test
            </button>
          </div>
        </div>

        {/* credentials */}
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: '12px', padding: '14px' }}>
          <Label>Username</Label>
          <input value={username} onInput={(e) => setUsername(e.target.value)} placeholder="username"
                 autocomplete="username" spellcheck={false} style={{ ...inputStyle, marginBottom: '10px' }} />
          <Label>Password</Label>
          <input type="password" value={password} onInput={(e) => setPassword(e.target.value)}
                 onKeyDown={(e) => { if (e.key === 'Enter') submit(); }} placeholder="password"
                 autocomplete="current-password" style={inputStyle} />
          {error && <div style={{ color: C.pink, fontSize: '10.5px', marginTop: '8px' }}>{error}</div>}
          <button onClick={submit} disabled={busy} style={{
            width: '100%', marginTop: '12px', padding: '10px', borderRadius: '9px', border: 'none',
            background: C.lime, color: C.onLime, cursor: busy ? 'default' : 'pointer',
            fontSize: '13px', fontWeight: 700, opacity: busy ? 0.7 : 1
          }}>{busy ? 'Signing in…' : 'Sign in'}</button>
          <div style={{ fontSize: '10px', color: C.faint, marginTop: '10px', textAlign: 'center' }}>
            Signing in routes captures into your workspace tenant.
          </div>
        </div>

        <div style={{ textAlign: 'center', marginTop: '14px' }}>
          <span style={{ fontFamily: F.mono, fontSize: '10.5px', color: C.faint }}>RECON Capture · v{vm.version}</span>
        </div>
      </div>
    </div>
  );
}
