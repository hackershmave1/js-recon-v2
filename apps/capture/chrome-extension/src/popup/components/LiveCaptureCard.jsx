// LiveCaptureCard.jsx — a capture in progress: what it captures into, for how long, Pause / Resume
// / End, plus the banners that hang off it (a new site seen, End finishing, the last capture ended).
// End has no confirmation: nothing is deleted and the session can be continued later.
import { C, F } from '../theme.js';
import { Dot } from './ui.jsx';
import { PauseIcon, PlayIcon, StopIcon, ArrowRightIcon } from '../icons.jsx';
import { EngagementLocked } from './EngagementPicker.jsx';

const TONE = {
  teal: { c: C.teal, bg: 'rgba(91,214,192,0.07)', line: 'rgba(91,214,192,0.5)' },
  blue: { c: C.blue, bg: 'rgba(107,168,255,0.08)', line: 'rgba(107,168,255,0.55)' },
  amber: { c: C.amber, bg: 'rgba(255,199,61,0.08)', line: C.amber },
};
const PILL = {
  live: { text: 'CAPTURING', c: C.lime, bg: 'rgba(205,235,69,0.13)' },
  paused: { text: 'PAUSED', c: C.amber, bg: 'rgba(255,199,61,0.13)' },
  ending: { text: 'ENDING', c: C.teal, bg: 'rgba(91,214,192,0.13)' },
};

function Banner({ tone, title, children, first }) {
  const t = TONE[tone];
  return (
    <div style={{ padding: first ? '15px 17px 0' : '0 17px 14px' }}>
      <div role="status" style={{ background: t.bg, border: `1px solid ${t.line}`, borderRadius: '11px', padding: '11px 13px', display: 'grid', gap: '4px' }}>
        <b style={{ fontSize: '11.5px', fontWeight: 700, color: t.c, overflowWrap: 'anywhere' }}>{title}</b>
        {children}
      </div>
    </div>
  );
}

const bannerText = { fontSize: '10.5px', color: C.faint, lineHeight: 1.5, margin: 0 };
const smallBtn = (primary) => ({
  flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px', padding: '8px',
  borderRadius: '9px', fontSize: '12px', fontWeight: 700, cursor: 'pointer',
  border: primary ? 'none' : `1px solid ${C.lineHover}`,
  background: primary ? C.lime : C.control, color: primary ? C.onLime : C.textSoft
});

export function LiveCaptureCard({ vm }) {
  const f = vm.flow;
  const pill = PILL[f.phase];
  const live = f.phase === 'live';
  const ctl = (warn) => ({
    flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '10px',
    borderRadius: '10px', border: `1px solid ${C.lineHover}`, background: C.control,
    color: warn ? C.orange : C.textSoft, cursor: f.busy ? 'default' : 'pointer', fontSize: '13px', fontWeight: 700
  });
  return (
    <div style={{ padding: '15px 17px 14px' }}>
      <div style={{
        background: C.panel, border: `1px solid ${live ? 'rgba(205,235,69,0.25)' : C.line}`,
        borderRadius: '13px', padding: '14px 15px', display: 'grid', gap: '11px'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '9px' }}>
          <Dot color={pill.c} size={8} pulse={live} />
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{
              display: 'block', fontFamily: F.mono, fontSize: '13px', color: C.text,
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
            }}>{vm.captureScope}</span>
            <span style={{ display: 'block', fontSize: '10.5px', color: C.faint }}>{vm.captureSubtitle}</span>
          </span>
          <span style={{ fontSize: '10px', fontWeight: 700, color: pill.c, background: pill.bg, padding: '3px 9px', borderRadius: '20px', flex: '0 0 auto' }}>{pill.text}</span>
        </div>
        <EngagementLocked name={vm.activeProjectName || 'Solo · standalone'} why="locked while capturing" />
        {vm.everyTab && (
          <div style={{ fontSize: '10.5px', color: C.orange }}>
            Capturing every tab — the scope is ignored (Settings).
          </div>
        )}
        {f.phase !== 'ending' && (
          <div style={{ display: 'flex', gap: '8px' }}>
            <button onClick={live ? f.pause : f.resume} disabled={!!f.busy} style={ctl(false)}>
              <span style={{ width: '14px', height: '14px', display: 'inline-flex' }}>{live ? <PauseIcon /> : <PlayIcon />}</span>
              {live ? 'Pause' : 'Resume'}
            </button>
            <button onClick={f.end} disabled={!!f.busy} style={ctl(true)}>
              <span style={{ width: '13px', height: '13px', display: 'inline-flex' }}><StopIcon /></span>
              End capture
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// JS loaded from a site outside the scope. Capture is never blocked and the banner stays until
// answered (the toolbar icon carries a dot meanwhile).
export function NewSiteBanner({ vm }) {
  const f = vm.flow;
  const [site, ...more] = vm.newSites;
  if (!site || f.phase === 'ending') return null;
  return (
    <Banner tone="blue" title={`New site seen: ${site}`}>
      <p style={bannerText}>
        It is outside this capture's scope, so its scripts are not being captured.
        {more.length > 0 && ` ${more.length} more site${more.length === 1 ? '' : 's'} waiting.`}
      </p>
      <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
        <button onClick={() => f.addSite(site)} disabled={!!f.busy} style={smallBtn(true)}>Add to scope</button>
        <button onClick={() => f.endAndStartNew(site)} disabled={!!f.busy} style={smallBtn(false)}>End + start new</button>
      </div>
      <button onClick={() => f.dismissSite(site)} disabled={!!f.busy} style={{
        border: 'none', background: 'none', color: C.faint, cursor: 'pointer', fontSize: '10.5px', padding: '4px 0 0', textAlign: 'left'
      }}>Not part of this target — don't ask again</button>
    </Banner>
  );
}

// End was pressed: it waits for every captured file rather than analyzing a partial set.
export function EndingBanner({ vm }) {
  if (vm.flow.phase !== 'ending') return null;
  const pending = vm.delivery?.pending || 0;
  return pending > 0 ? (
    <Banner tone="amber" title="Waiting to finish">
      <p style={bannerText}>
        {pending} file{pending === 1 ? '' : 's'} {pending === 1 ? "hasn't" : "haven't"} uploaded yet. The capture finishes as soon as
        {pending === 1 ? ' it is' : ' they are'} sent; you can keep browsing or close the popup.
      </p>
    </Banner>
  ) : (
    <Banner tone="teal" title="Finishing capture">
      <p style={bannerText}>Uploading the last files and running the final analysis. You can close the popup; this finishes on its own.</p>
    </Banner>
  );
}

export function EndedBanner({ vm }) {
  const ended = vm.ended;
  if (!ended) return null;
  return (
    <Banner tone="teal" title={ended.title} first>
      <p style={bannerText}>{ended.body}</p>
      <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
        <button onClick={vm.openWorkspace} style={smallBtn(true)}>Open in workspace <ArrowRightIcon size={11} /></button>
        <button onClick={vm.flow.dismissEnded} style={smallBtn(false)}>Done</button>
      </div>
    </Banner>
  );
}
