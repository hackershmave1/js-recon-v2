// HomeView.jsx — the default popup screen: capture target, scope, live stats,
// recent captures, quick toggles and footer actions. Pixel layout mirrors the
// "RECON Capture" prototype HOME view.
import { useState } from 'preact/hooks';
import { C, F, CLASS_COLOR, CLASS_LABEL } from '../theme.js';
import { Switch, Dot } from './ui.jsx';
import {
  LogoMark, GearIcon, PauseIcon, PlayIcon, DownloadIcon, ArrowRightIcon
} from '../icons.jsx';
import { EngagementPicker } from './EngagementPicker.jsx';

const FLAG = { c: C.pink, bg: 'rgba(255,107,138,0.13)' };

function SectionLabel({ children }) {
  return (
    <span style={{ fontSize: '10.5px', color: C.faint, fontWeight: 700, letterSpacing: '0.8px' }}>
      {children}
    </span>
  );
}

function CaptureRow({ c }) {
  const isApp = c.classification === 'app';
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '9px', padding: '9px 8px',
      borderRadius: '9px', animation: 'capflow .25s ease'
    }}>
      <Dot color={c.dot} size={7} pulse={c.analyzing} />
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{
          display: 'block', fontFamily: F.mono, fontSize: '11.5px', color: C.textSoft,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
        }}>{c.name}</span>
        <span style={{ display: 'block', fontSize: '9.5px', color: C.faint }}>{c.meta}</span>
      </span>
      {c.isThirdParty && (
        <span style={{
          fontSize: '8px', fontWeight: 700, color: C.orange, background: 'rgba(255,138,71,0.13)',
          padding: '2px 6px', borderRadius: '9px', flex: '0 0 auto'
        }}>3RD</span>
      )}
      {!isApp && (
        <span style={{ fontSize: '9px', fontWeight: 600, color: CLASS_COLOR[c.classification], flex: '0 0 auto' }}>
          {CLASS_LABEL[c.classification]}
        </span>
      )}
      {c.secretCount > 0 && (
        <span style={{
          fontSize: '9px', fontWeight: 700, color: FLAG.c, background: FLAG.bg,
          padding: '2px 7px', borderRadius: '10px', flex: '0 0 auto'
        }}>{c.secretCount} SEC</span>
      )}
    </div>
  );
}

export function HomeView({ vm }) {
  const cap = vm.capturing;
  // Scope-badge colour tracks the real capture-gate state: red = wide open (all tabs),
  // amber = no scope (capturing nothing), lime = scoped.
  const scopeColor = vm.scopeMode === 'open' ? C.orange : vm.scopeMode === 'none' ? C.amber : C.lime;
  // Connection dot/label colour = real delivery health (D42), no longer a fake constant green.
  const healthColor = { ok: C.lime, warn: C.amber, fail: C.orange, testing: C.blue }[vm.deliveryHealth] || C.lime;
  const d = vm.delivery || { uploaded: 0, pending: 0, failedTotal: 0, paired: null, lastReason: '', lastFile: '' };
  // Confirm-before-enable for "Capture every tab" (D44): a cross-tenant footgun, so it takes an
  // explicit in-app confirm (window.confirm is suppressed in the popup) rather than a silent toggle.
  const [confirmEvery, setConfirmEvery] = useState(false);
  const statBox = (value, label, color) => (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: '11px', padding: '12px' }}>
      <div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '22px', lineHeight: 1, color: color || C.text }}>{value}</div>
      <div style={{ fontSize: '10px', color: C.faint, marginTop: '4px' }}>{label}</div>
    </div>
  );

  // Decoupled analysis button state.
  const a = vm.analysis || { status: 'idle' };
  const ac = a.counts || {};
  const analyzing = a.status === 'starting' || a.status === 'running';
  const analyzeLabel = a.status === 'starting' ? 'Starting analysis…'
    : a.status === 'running' ? `Analyzing… ${ac.completed || 0}/${ac.total || 0}`
    : a.status === 'done' ? `Analyzed ✓ · ${ac.completed || 0} done`
    : `Analyze ${vm.stats.js} ${vm.stats.js === 1 ? 'script' : 'scripts'}`;
  const analyzeDisabled = !vm.canAnalyze || analyzing;

  return (
    <div>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '11px', padding: '15px 17px', borderBottom: `1px solid ${C.line}` }}>
        <div style={{
          width: '28px', height: '28px', borderRadius: '8px', background: C.lime, display: 'flex',
          alignItems: 'center', justifyContent: 'center', boxShadow: '0 0 16px rgba(205,235,69,0.4)'
        }}><LogoMark size={17} /></div>
        <div style={{ flex: 1, lineHeight: 1.05 }}>
          <div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '14px', letterSpacing: '-0.2px' }}>RECON Capture</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '10.5px', color: healthColor, fontFamily: F.mono, marginTop: '1px' }}>
            <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: healthColor, boxShadow: `0 0 6px ${healthColor}` }} />
            {vm.connectionLabel}
          </div>
        </div>
        <button class="pp-iconbtn" onClick={vm.openSettings} aria-label="Open settings" style={{
          width: '30px', height: '30px', borderRadius: '8px', border: `1px solid ${C.lineStrong}`,
          background: C.control, color: C.muted, cursor: 'pointer', display: 'flex',
          alignItems: 'center', justifyContent: 'center'
        }}><GearIcon /></button>
      </div>

      {/* session-expired banner — the login token expired/was rejected mid-capture (DEBT D41).
          Capture keeps running and buffers to the durable outbox; only uploads are paused until
          re-auth. Always escapable via "Sign in again", which refreshes the token without dropping
          the capture session. Single source of truth: uploader.authPaused (via getStatus). */}
      {vm.sessionExpired && (
        <div style={{ padding: '13px 17px 0' }}>
          <div style={{
            background: 'rgba(255,138,71,0.1)', border: `1px solid ${C.orange}`,
            borderRadius: '11px', padding: '11px 13px'
          }}>
            <div style={{ fontSize: '11.5px', color: C.orange, fontWeight: 700, marginBottom: '4px' }}>
              Session expired — uploads paused
            </div>
            <div style={{ fontSize: '10.5px', color: C.faint, marginBottom: '9px', lineHeight: 1.5 }}>
              Capture is still running and buffered locally — sign in again to resume uploading. Nothing is lost.
            </div>
            <button onClick={vm.reauth} style={{
              width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '7px',
              padding: '9px', borderRadius: '9px', border: 'none', background: C.orange, color: C.onLime,
              cursor: 'pointer', fontSize: '12px', fontWeight: 700
            }}>
              Sign in again
            </button>
          </div>
        </div>
      )}

      {/* capture target card */}
      <div style={{ padding: '15px 17px' }}>
        <div style={{
          background: C.panel, border: `1px solid ${cap ? 'rgba(205,235,69,0.25)' : C.line}`,
          borderRadius: '13px', padding: '14px 15px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '9px', marginBottom: '12px' }}>
            <Dot color={cap ? C.lime : C.dim} size={8} pulse={cap} />
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{
                display: 'block', fontFamily: F.mono, fontSize: '13px', color: C.text,
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
              }}>{vm.host}</span>
              <span style={{ display: 'block', fontSize: '10.5px', color: C.faint }}>{vm.activeProjectName || 'Solo · standalone'} · {vm.session}</span>
            </span>
            <span style={{
              fontSize: '10px', fontWeight: 700, color: cap ? C.lime : C.dim,
              background: cap ? 'rgba(205,235,69,0.13)' : 'rgba(126,138,163,0.13)',
              padding: '3px 9px', borderRadius: '20px'
            }}>{cap ? 'CAPTURING' : 'PAUSED'}</span>
          </div>
          <button onClick={vm.toggleCapture} style={{
            width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
            padding: '10px', borderRadius: '10px', border: 'none',
            background: cap ? C.control : C.lime, color: cap ? C.orange : C.onLime,
            cursor: 'pointer', fontSize: '13px', fontWeight: 700
          }}>
            <span style={{ width: '15px', height: '15px', display: 'inline-flex' }}>
              {cap ? <PauseIcon /> : <PlayIcon />}
            </span>
            {cap ? 'Pause capture' : 'Resume capture'}
          </button>
        </div>
      </div>

      {/* no-scope prompt — fail-closed capture guard (D40): with no scope AND capture-every-tab off,
          capture collects nothing, so offer a one-tap "capture this site" instead of a fake-green
          CAPTURING. Shown whenever the gate is empty, capturing or not. */}
      {vm.scopeMode === 'none' && (
        <div style={{ padding: '0 17px 14px' }}>
          <div style={{
            background: 'rgba(240,199,94,0.08)', border: `1px solid ${C.amber}`,
            borderRadius: '11px', padding: '11px 13px'
          }}>
            <div style={{ fontSize: '11.5px', color: C.amber, fontWeight: 700, marginBottom: '4px' }}>
              No scope — capturing nothing
            </div>
            <div style={{ fontSize: '10.5px', color: C.faint, marginBottom: vm.activeHost ? '9px' : 0, lineHeight: 1.5 }}>
              Capture is fail-closed: set a target scope, or turn on “Capture every tab” below.
            </div>
            {vm.activeHost && (
              <button onClick={() => vm.armScope(vm.activeHost)} style={{
                width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '7px',
                padding: '9px', borderRadius: '9px', border: 'none', background: C.lime, color: C.onLime,
                cursor: 'pointer', fontSize: '12px', fontWeight: 700
              }}>
                <span style={{ width: '14px', height: '14px', display: 'inline-flex' }}><PlayIcon /></span>
                Capture {vm.activeHost}
              </button>
            )}
          </div>
        </div>
      )}

      {/* scope */}
      <div style={{ padding: '0 17px 14px' }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: '9px', background: C.inset,
          border: `1px solid ${C.lineStrong}`, borderRadius: '10px', padding: '9px 12px'
        }}>
          <span style={{ fontSize: '10px', color: C.faint, fontWeight: 700, letterSpacing: '0.6px' }}>SCOPE</span>
          <span style={{
            fontFamily: F.mono, fontSize: '12px', color: scopeColor, flex: 1, minWidth: 0,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
          }}>{vm.scope}</span>
          <button onClick={vm.toggleSubdomains} style={{
            display: 'flex', alignItems: 'center', gap: '6px', border: 'none', background: 'none', cursor: 'pointer'
          }}>
            <span style={{ fontSize: '10.5px', color: C.muted }}>+ subdomains</span>
            <Switch on={vm.includeSubdomains} variant="sm" />
          </button>
        </div>
        <EngagementPicker vm={vm} />
      </div>

      {/* out-of-scope script hosts — discovery aid (D44): app JS served from a separate apex the
          scope missed, with a one-click add so the operator stops silently missing that bundle. */}
      {vm.outOfScopeHosts && vm.outOfScopeHosts.length > 0 && (
        <div style={{ padding: '0 17px 14px' }}>
          <div style={{ background: C.inset, border: `1px solid ${C.lineStrong}`, borderRadius: '10px', padding: '10px 12px' }}>
            <div style={{ fontSize: '10px', color: C.faint, fontWeight: 700, letterSpacing: '0.6px', marginBottom: '7px' }}>
              OUT-OF-SCOPE SCRIPT HOSTS
            </div>
            {vm.outOfScopeHosts.slice(0, 5).map((h) => (
              <div key={h.host} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '4px 0' }}>
                <span style={{
                  flex: 1, minWidth: 0, fontFamily: F.mono, fontSize: '11px', color: C.textSoft,
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
                }}>{h.host} <span style={{ color: C.faint }}>· {h.count}</span></span>
                <button onClick={() => vm.addScopeHost(h.host)} style={{
                  flex: '0 0 auto', padding: '3px 11px', borderRadius: '7px', border: `1px solid ${C.lineHover}`,
                  background: C.control, color: C.lime, cursor: 'pointer', fontSize: '10.5px', fontWeight: 700
                }}>+ add</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '9px', padding: '0 17px 15px' }}>
        {statBox(vm.stats.js, 'scripts')}
        {statBox(vm.stats.maps, 'maps', C.teal)}
        {statBox(vm.stats.secrets, 'secrets', C.pink)}
      </div>

      {/* delivery — real upload/skip/failure health, not just captured counts (D42) */}
      <div style={{ padding: '0 17px 15px' }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: '10px', background: C.inset,
          border: `1px solid ${C.lineStrong}`, borderRadius: '10px', padding: '9px 12px'
        }}>
          <span style={{ fontSize: '10px', color: C.faint, fontWeight: 700, letterSpacing: '0.6px' }}>DELIVERY</span>
          <span style={{
            flex: 1, minWidth: 0, display: 'flex', gap: '13px', fontFamily: F.mono, fontSize: '11px',
            whiteSpace: 'nowrap', overflow: 'hidden'
          }}>
            <span style={{ color: C.teal }}>{d.uploaded} sent</span>
            <span style={{ color: d.pending > 0 ? C.textSoft : C.dim }}>{d.pending} pending</span>
            <span style={{ color: d.failedTotal > 0 ? C.orange : C.dim }}>{d.failedTotal} failed</span>
            {d.paired === false && <span style={{ color: C.orange }}>not paired</span>}
          </span>
        </div>
        {d.failedTotal > 0 && d.lastReason && (
          <div style={{
            fontSize: '10px', color: C.faint, marginTop: '6px',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
          }}>
            last: {d.lastReason}{d.lastFile ? ` · ${d.lastFile}` : ''}
          </div>
        )}
      </div>

      {/* analyze on demand (decoupled from capture) */}
      <div style={{ padding: '0 17px 15px' }}>
        <button onClick={vm.analyzeNow} disabled={analyzeDisabled} style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
          padding: '10px', borderRadius: '10px', border: `1px solid ${analyzing ? 'rgba(91,214,192,0.4)' : C.lineHover}`,
          background: C.panel, color: analyzeDisabled && !analyzing ? C.dim : C.textSoft,
          cursor: analyzeDisabled ? 'default' : 'pointer', fontSize: '12.5px', fontWeight: 600
        }}>
          {analyzing && <Dot color={C.teal} size={7} pulse />}
          {analyzeLabel}
        </button>
        <div style={{ fontSize: '10px', color: C.faint, marginTop: '6px', textAlign: 'center' }}>
          Capture stays fast — analysis runs on demand.
        </div>
      </div>

      {/* D46: findings summary card — shown after analysis completes (status=done) when the
          backend has a completed run for this session. Compact 2-line strip: count summary
          on the first line, top finding type on the second, "View in workspace →" link.
          Hidden until both the analysis job is done AND a summary with real findings exists. */}
      {a.status === 'done' && vm.findingsSummary && vm.findingsSummary.status === 'complete' && (() => {
        const s = vm.findingsSummary;
        const c = s.counts || {};
        // Compact summary line: "42 findings: 20 endpoints · 5 secrets · 3 IPs"
        const parts = [];
        if (c.endpoints > 0) parts.push(`${c.endpoints} endpoint${c.endpoints !== 1 ? 's' : ''}`);
        if (c.secrets > 0) parts.push(`${c.secrets} secret${c.secrets !== 1 ? 's' : ''}`);
        if (c.internal_ips > 0) parts.push(`${c.internal_ips} IP${c.internal_ips !== 1 ? 's' : ''}`);
        if (c.graphql > 0) parts.push(`${c.graphql} GraphQL`);
        if (c.other > 0) parts.push(`${c.other} other`);
        const summaryLine = `${c.total || 0} finding${c.total !== 1 ? 's' : ''}${parts.length ? ': ' + parts.join(' · ') : ''}`;
        return (
          <div style={{ padding: '0 17px 15px' }}>
            <div style={{
              background: 'rgba(205,235,69,0.06)', border: `1px solid rgba(205,235,69,0.25)`,
              borderRadius: '11px', padding: '11px 13px'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
                <span style={{ fontFamily: F.mono, fontSize: '11.5px', color: C.lime, fontWeight: 700, flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {summaryLine}
                </span>
                <button onClick={vm.openWorkspace} style={{
                  flex: '0 0 auto', display: 'flex', alignItems: 'center', gap: '4px',
                  padding: '3px 10px', borderRadius: '7px', border: `1px solid rgba(205,235,69,0.35)`,
                  background: 'none', color: C.lime, cursor: 'pointer', fontSize: '10.5px', fontWeight: 700
                }}>
                  View <ArrowRightIcon />
                </button>
              </div>
              {(s.top_findings || []).length > 0 && (
                <div style={{ marginTop: '5px', fontSize: '10.5px', color: C.faint, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  Top: {s.top_findings[0].type}{s.top_findings[0].value && s.top_findings[0].value !== '[redacted]' ? ` · ${s.top_findings[0].value}` : ''}
                </div>
              )}
            </div>
          </div>
        );
      })()}

      {/* D46(c): past sessions history — shown when there are prior sessions */}
      {vm.captureHistory && vm.captureHistory.length > 0 && (
        <div style={{ padding: '0 17px 13px' }}>
          <div style={{ marginBottom: '7px' }}><SectionLabel>PAST SESSIONS</SectionLabel></div>
          {vm.captureHistory.slice(0, 5).map((entry, i) => {
            const date = entry.timestamp ? new Date(entry.timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';
            const c = entry.findingsSummary?.counts || {};
            const hasSummary = (c.total || 0) > 0;
            const scopeLabel = (entry.scope?.rootDomains || []).join(', ') || '—';
            const workspaceUrl = entry.workspaceUrl || 'http://localhost:8000';
            const viewUrl = entry.sessionId
              ? `${workspaceUrl}${workspaceUrl.includes('?') ? '&' : '?'}capture=${encodeURIComponent(entry.sessionId)}`
              : workspaceUrl;
            return (
              <div key={entry.sessionId || i} style={{
                display: 'flex', alignItems: 'center', gap: '8px', padding: '7px 10px',
                borderRadius: '9px', background: C.panel, marginBottom: '4px'
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: '11px', color: C.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {scopeLabel}
                  </div>
                  <div style={{ fontSize: '10px', color: C.faint, marginTop: '2px' }}>
                    {date && <span style={{ marginRight: '6px' }}>{date}</span>}
                    <span>{entry.fileCount || 0} JS</span>
                    {entry.mapsCount > 0 && <span style={{ marginLeft: '5px' }}>{entry.mapsCount} map</span>}
                    {hasSummary && <span style={{ marginLeft: '5px', color: C.lime }}>{c.total} finding{c.total !== 1 ? 's' : ''}</span>}
                  </div>
                </div>
                <button onClick={() => { try { chrome.tabs.create({ url: viewUrl }); } catch (e) {} }} style={{
                  flex: '0 0 auto', display: 'flex', alignItems: 'center', gap: '3px',
                  padding: '3px 8px', borderRadius: '6px', border: `1px solid ${C.lineHover}`,
                  background: 'none', color: C.dim, cursor: 'pointer', fontSize: '10px'
                }}>
                  View <ArrowRightIcon />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* recent captures */}
      <div style={{ padding: '0 17px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
          <SectionLabel>RECENT CAPTURES</SectionLabel>
          <span style={{ display: 'flex', alignItems: 'center', gap: '9px' }}>
            {vm.mutedCount > 0 && (
              <button onClick={vm.showAllCaptures} style={{
                fontSize: '10.5px', color: C.purple, background: 'none', border: 'none', cursor: 'pointer', fontFamily: F.mono
              }}>{vm.mutedCount} muted</button>
            )}
            <span style={{ fontSize: '10.5px', color: C.dim, fontFamily: F.mono }}>{cap ? 'live' : 'idle'}</span>
          </span>
        </div>
        <div style={{ maxHeight: '168px', overflowY: 'auto', margin: '0 -2px' }}>
          {vm.captures.length === 0 && (
            <div style={{ padding: '18px 8px', textAlign: 'center', fontSize: '11px', color: C.faint }}>
              No captures yet
            </div>
          )}
          {vm.captures.map((c) => <CaptureRow key={c.key} c={c} />)}
        </div>
      </div>

      {/* quick toggles */}
      <div style={{ padding: '13px 17px', borderTop: `1px solid ${C.line}`, marginTop: '13px' }}>
        {confirmEvery && (
          <div style={{
            background: 'rgba(255,138,71,0.08)', border: `1px solid ${C.orange}`,
            borderRadius: '10px', padding: '11px 12px', marginBottom: '11px'
          }}>
            <div style={{ fontSize: '11.5px', color: C.orange, fontWeight: 700, marginBottom: '4px' }}>Capture every tab?</div>
            <div style={{ fontSize: '10.5px', color: C.faint, marginBottom: '9px', lineHeight: 1.5 }}>
              Ignores scope and uploads JS from every tab — including unrelated sites and other tenants — into this engagement.
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button onClick={() => { vm.toggleSetting('captureEverything'); setConfirmEvery(false); }} style={{
                flex: 1, padding: '8px', borderRadius: '8px', border: 'none', background: C.orange,
                color: C.onLime, cursor: 'pointer', fontSize: '11.5px', fontWeight: 700
              }}>Enable anyway</button>
              <button onClick={() => setConfirmEvery(false)} style={{
                flex: 1, padding: '8px', borderRadius: '8px', border: `1px solid ${C.lineHover}`,
                background: C.control, color: C.muted, cursor: 'pointer', fontSize: '11.5px', fontWeight: 600
              }}>Cancel</button>
            </div>
          </div>
        )}
        {vm.toggles.map((t) => (
          <button key={t.key} onClick={() => {
            // "Capture every tab" enable takes an explicit confirm (D44); everything else toggles directly.
            if (t.key === 'captureEverything' && !t.on) { setConfirmEvery(true); return; }
            vm.toggleSetting(t.key);
          }} style={{
            width: '100%', display: 'flex', alignItems: 'center', gap: '10px', padding: '8px 0',
            border: 'none', background: 'none', cursor: 'pointer'
          }}>
            <span style={{ flex: 1, textAlign: 'left', fontSize: '12.5px', color: C.textSoft }}>{t.label}</span>
            <Switch on={t.on} />
          </button>
        ))}
      </div>

      {/* footer */}
      <div style={{ display: 'flex', gap: '9px', padding: '13px 17px', borderTop: `1px solid ${C.line}` }}>
        <button onClick={vm.exportNow} style={{
          flex: '0 0 auto', display: 'flex', alignItems: 'center', gap: '7px', padding: '10px 14px',
          borderRadius: '10px', border: `1px solid ${C.lineHover}`, background: C.control,
          color: C.textSoft, cursor: 'pointer', fontSize: '12.5px', fontWeight: 600
        }}>
          <DownloadIcon />Export
        </button>
        <button onClick={vm.openWorkspace} style={{
          flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '7px',
          padding: '10px', borderRadius: '10px', border: 'none', background: C.lime,
          color: C.onLime, cursor: 'pointer', fontSize: '12.5px', fontWeight: 700
        }}>
          Open Workspace<ArrowRightIcon />
        </button>
      </div>
    </div>
  );
}
