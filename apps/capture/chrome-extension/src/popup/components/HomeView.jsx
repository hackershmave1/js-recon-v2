// HomeView.jsx — the default popup screen. Idle: the scope-first Start card (ScopeStartCard). With
// a capture in progress: its live card, then stats, delivery, Analyze, the findings summary and the
// captures feed. Quick toggles and the footer show in both.
import { C, F, CLASS_COLOR, CLASS_LABEL } from '../theme.js';
import { Switch, Dot } from './ui.jsx';
import { LogoMark, GearIcon, DownloadIcon, ArrowRightIcon } from '../icons.jsx';
import { ScopeStartCard } from './ScopeStartCard.jsx';
import { LiveCaptureCard, NewSiteBanner, EndingBanner, EndedBanner } from './LiveCaptureCard.jsx';
import { analyzeLabel } from '../viewmodels/captureVm.js';
import { typeLabel } from '../../../modules/finding-labels.js';

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
  const phase = vm.flow.phase;
  const hasCapture = phase !== 'idle';
  const cap = phase === 'live';
  // Connection dot/label colour = real delivery health (D42), no longer a fake constant green.
  const healthColor = { ok: C.lime, warn: C.amber, fail: C.orange, testing: C.blue }[vm.deliveryHealth] || C.lime;
  const d = vm.delivery || { uploaded: 0, pending: 0, failedTotal: 0, paired: null, lastReason: '', lastFile: '' };
  const statBox = (value, label, color) => (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: '11px', padding: '12px' }}>
      <div style={{ fontFamily: F.display, fontWeight: 700, fontSize: '22px', lineHeight: 1, color: color || C.text }}>{value}</div>
      <div style={{ fontSize: '10px', color: C.faint, marginTop: '4px' }}>{label}</div>
    </div>
  );

  // Decoupled analysis button state.
  const a = vm.analysis || { status: 'idle' };
  const analyzing = a.status === 'starting' || a.status === 'running';
  const analyzeText = analyzeLabel(a, vm.stats.js);
  const analyzeDisabled = !vm.canAnalyze || analyzing || phase === 'ending';

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

      {!hasCapture && <EndedBanner vm={vm} />}
      {hasCapture ? <LiveCaptureCard vm={vm} /> : <ScopeStartCard vm={vm} />}
      {hasCapture && <NewSiteBanner vm={vm} />}
      {hasCapture && <EndingBanner vm={vm} />}

      {hasCapture && (<>
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
          {analyzeText}
        </button>
        <div style={{ fontSize: '10px', color: C.faint, marginTop: '6px', textAlign: 'center' }}>
          {a.status === 'done'
            ? 'New files are added to this analysis automatically.'
            : 'Capture keeps going while you analyze.'}
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
                  Top: {typeLabel(s.top_findings[0].type)}{s.top_findings[0].value && s.top_findings[0].value !== '[redacted]' ? ` · ${s.top_findings[0].value}` : ''}
                </div>
              )}
            </div>
          </div>
        );
      })()}

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

      </>)}

      {/* quick toggles */}
      <div style={{ padding: '13px 17px', borderTop: `1px solid ${C.line}`, marginTop: hasCapture ? '13px' : 0 }}>
        {vm.toggles.map((t) => (
          <button key={t.key} onClick={() => vm.toggleSetting(t.key)} style={{
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
