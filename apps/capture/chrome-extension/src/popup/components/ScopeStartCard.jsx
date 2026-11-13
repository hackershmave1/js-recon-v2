// ScopeStartCard.jsx — the idle popup: scope first, then what to capture into, then Start.
// Nothing captures until a scope is chosen; Start is disabled until then. The scope is set here,
// per capture (it clears at End) — there is no scope editor in Settings any more.
import { useState } from 'preact/hooks';
import { C, F } from '../theme.js';
import { Dot } from './ui.jsx';
import { PlayIcon, CloseIcon } from '../icons.jsx';
import { EngagementPicker, EngagementLocked } from './EngagementPicker.jsx';
import { scopeLabel } from '../viewmodels/captureVm.js';

const sectionLabel = { display: 'block', fontSize: '10.5px', color: C.faint, fontWeight: 700, letterSpacing: '0.8px', marginBottom: '7px' };

function Option({ on, title, line, best, tag, onPick }) {
  return (
    <button role="radio" aria-checked={on} onClick={onPick} style={{
      display: 'flex', gap: '9px', alignItems: 'flex-start', textAlign: 'left', width: '100%',
      border: `1px solid ${on ? 'rgba(205,235,69,0.6)' : C.lineStrong}`, borderRadius: '10px',
      padding: '9px 11px', background: on ? 'rgba(205,235,69,0.05)' : C.inset, cursor: 'pointer'
    }}>
      <span style={{
        width: '13px', height: '13px', borderRadius: '50%', flex: '0 0 13px', marginTop: '2px',
        border: `1.5px solid ${on ? C.lime : C.faint}`, display: 'flex', alignItems: 'center', justifyContent: 'center'
      }}>{on && <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: C.lime }} />}</span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: 'block', fontSize: '12.5px', fontWeight: 600, color: C.text }}>
          {title}{tag && <span style={{ color: C.muted, fontWeight: 400 }}> · {tag}</span>}
        </span>
        <span style={{ display: 'block', fontFamily: F.mono, fontSize: '10.5px', color: C.faint, marginTop: '2px' }}>
          {line}{best && <span> · <span style={{ color: C.lime }}>{best}</span></span>}
        </span>
      </span>
    </button>
  );
}

export function ScopeStartCard({ vm }) {
  const f = vm.flow;
  const [typed, setTyped] = useState('');
  const draft = f.scopeDraft;
  const checking = f.lookupState === 'loading';
  const canStart = !!draft && !checking && !f.busy;
  const newLine = f.options.length
    ? 'Starts a separate history for this target'
    : f.lookupState === 'error' ? "Couldn't check for earlier sessions" : `No earlier session for ${draft}`;

  return (
    <div>
      <div style={{ padding: '15px 17px 14px' }}>
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: '13px', padding: '14px 15px', display: 'grid', gap: '11px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '9px' }}>
            <Dot color={C.dim} size={8} />
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: 'block', fontFamily: F.mono, fontSize: '13px', color: C.text }}>New capture</span>
              <span style={{ display: 'block', fontSize: '10.5px', color: C.faint }}>{draft ? 'Choose what to capture into' : 'Set a scope to start'}</span>
            </span>
            <span style={{ fontSize: '10px', fontWeight: 700, color: C.dim, background: 'rgba(126,138,163,0.13)', padding: '3px 9px', borderRadius: '20px' }}>IDLE</span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '9px', background: C.inset, border: `1px solid ${C.lineStrong}`, borderRadius: '10px', padding: '9px 12px' }}>
            <span style={{ fontSize: '10px', color: C.faint, fontWeight: 700, letterSpacing: '0.6px' }}>SCOPE</span>
            {draft ? (
              <>
                <span style={{ fontFamily: F.mono, fontSize: '12px', color: C.lime, flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{scopeLabel(draft)}</span>
                <button onClick={f.clearScope} aria-label="Clear scope" style={{ border: 'none', background: 'none', color: C.faint, cursor: 'pointer', padding: 0, display: 'flex' }}><CloseIcon /></button>
              </>
            ) : (
              <span style={{ fontSize: '12px', color: C.faint, fontStyle: 'italic', flex: 1 }}>No scope set</span>
            )}
          </div>

          {!draft && (f.suggestion ? (
            <button onClick={f.useTabScope} style={{
              display: 'block', textAlign: 'left', width: '100%', border: '1px dashed rgba(205,235,69,0.55)',
              borderRadius: '10px', padding: '9px 11px', background: 'none', color: C.lime, cursor: 'pointer', fontSize: '12px', fontWeight: 600
            }}>
              Use {f.suggestion.host} as scope
              <span style={{ display: 'block', color: C.faint, fontWeight: 400, fontSize: '10.5px' }}>
                → {f.suggestion.subdomains ? `${f.suggestion.scope} and all its subdomains` : 'only this host'}
              </span>
            </button>
          ) : (
            <div style={{ border: `1px dashed ${C.lineStrong}`, borderRadius: '10px', padding: '9px 11px', color: C.faint, fontSize: '12px', fontWeight: 600 }}>
              Use current site
              <span style={{ display: 'block', fontWeight: 400, fontSize: '10.5px' }}>This tab isn't a website. Type a domain instead.</span>
            </div>
          ))}

          {!draft && (
            <form onSubmit={(e) => { e.preventDefault(); f.setTypedScope(typed); setTyped(''); }} style={{ display: 'flex', gap: '7px' }}>
              <input value={typed} onInput={(e) => setTyped(e.target.value)} aria-label="Scope domain"
                     placeholder="or type a domain, e.g. acme.io" style={{
                       flex: 1, minWidth: 0, background: C.inset, border: `1px solid ${C.lineStrong}`, borderRadius: '9px',
                       padding: '9px 11px', fontFamily: F.mono, fontSize: '12px', color: C.text, outline: 'none'
                     }} />
              <button type="submit" disabled={!typed.trim()} style={{
                flex: '0 0 auto', padding: '0 13px', borderRadius: '9px', border: `1px solid ${C.lineHover}`,
                background: C.control, color: typed.trim() ? C.textSoft : C.dim, cursor: typed.trim() ? 'pointer' : 'default', fontSize: '12px', fontWeight: 600
              }}>Set</button>
            </form>
          )}
        </div>
      </div>

      {draft && (
        <div style={{ padding: '0 17px 14px' }}>
          <span style={sectionLabel}>CAPTURE INTO</span>
          {checking ? (
            <div role="status" style={{ fontSize: '11px', color: C.faint, padding: '9px 2px' }}>Checking for earlier sessions…</div>
          ) : (
            <div role="radiogroup" aria-label="Capture into" style={{ display: 'grid', gap: '6px' }}>
              {f.options.slice(0, 3).map((o) => (
                <Option key={o.key} on={f.choice === o.key} title={o.title} tag={o.engagement} line={o.line} best={o.best}
                        onPick={() => f.setChoice(o.key)} />
              ))}
              <Option on={!f.picked} title={`New session for ${draft}`} line={newLine} onPick={() => f.setChoice('new')} />
            </div>
          )}
          <div style={{ marginTop: '10px' }}>
            {f.picked
              ? <EngagementLocked name={f.picked.engagement} why="set by that session" />
              : <EngagementPicker vm={vm} scope={draft} />}
          </div>
        </div>
      )}

      <div style={{ padding: '0 17px 15px' }}>
        <button onClick={f.start} disabled={!canStart} style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
          padding: '10px', borderRadius: '10px', border: 'none', background: C.lime, color: C.onLime,
          opacity: canStart ? 1 : 0.4, cursor: canStart ? 'pointer' : 'default', fontSize: '13px', fontWeight: 700
        }}>
          <span style={{ width: '14px', height: '14px', display: 'inline-flex' }}><PlayIcon /></span>
          {f.busy === 'start' ? 'Starting…' : 'Start capture'}
        </button>
        {vm.everyTab && (
          <div style={{ fontSize: '10px', color: C.orange, marginTop: '6px', textAlign: 'center' }}>
            “Capture every tab” is on: the scope names the session, but every tab is captured.
          </div>
        )}
      </div>
    </div>
  );
}
