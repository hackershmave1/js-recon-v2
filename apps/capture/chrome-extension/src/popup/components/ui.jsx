// ui.jsx — small shared primitives (Switch, Dot, Toast) used across both views.
import { C } from '../theme.js';

// Toggle switch. variant 'lg' = 36x20 knob16 (settings rows); 'sm' = 30x17 knob13 (scope bar).
export function Switch({ on, variant = 'lg' }) {
  const dims = variant === 'sm'
    ? { w: 30, h: 17, knob: 13, onX: 15, offX: 2 }
    : { w: 36, h: 20, knob: 16, onX: 18, offX: 2 };
  return (
    <span style={{
      width: `${dims.w}px`, height: `${dims.h}px`, borderRadius: '20px',
      background: on ? C.lime : C.lineStrong, position: 'relative',
      transition: 'background .15s', flex: `0 0 ${dims.w}px`
    }}>
      <span style={{
        position: 'absolute', top: '2px', left: `${on ? dims.onX : dims.offX}px`,
        width: `${dims.knob}px`, height: `${dims.knob}px`, borderRadius: '50%',
        background: on ? C.onLime : C.faint, transition: 'left .15s'
      }} />
    </span>
  );
}

// Status dot, optionally pulsing.
export function Dot({ color, size = 8, pulse = false }) {
  return (
    <span style={{
      width: `${size}px`, height: `${size}px`, borderRadius: '50%',
      background: color, position: 'relative', flex: `0 0 ${size}px`,
      display: 'inline-block'
    }}>
      {pulse && (
        <span style={{
          position: 'absolute', width: `${size}px`, height: `${size}px`,
          borderRadius: '50%', background: color, animation: 'pulse 1.1s infinite'
        }} />
      )}
    </span>
  );
}

// Bottom-centred toast. `toast` is { msg, tone } — tone drives colour + icon so a failure no
// longer reads as a green success (D42): 'ok' = lime check, 'warn' = amber alert, 'error' =
// orange alert. Errors/warnings also linger longer (set by the caller's timeout).
const TOAST_TONE = {
  ok: { color: C.lime, border: C.lineHover },
  warn: { color: C.amber, border: 'rgba(240,199,94,0.45)' },
  error: { color: C.orange, border: 'rgba(255,138,71,0.45)' }
};

export function Toast({ toast }) {
  if (!toast) return null;
  const { msg, tone = 'ok' } = toast;
  const t = TOAST_TONE[tone] || TOAST_TONE.ok;
  const alert = tone === 'error' || tone === 'warn';
  return (
    <div role="status" style={{
      position: 'absolute', bottom: '12px', left: '50%', transform: 'translateX(-50%)',
      background: '#1a1f2c', border: `1px solid ${t.border}`, borderRadius: '9px',
      padding: '9px 15px', fontSize: '11.5px', color: t.color, display: 'flex',
      alignItems: 'center', gap: '7px', boxShadow: '0 10px 30px rgba(0,0,0,0.5)', zIndex: 10,
      maxWidth: '340px'
    }}>
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"
           style={{ flex: '0 0 auto' }}>
        {alert
          ? <g><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><path d="M12 9v4" /><path d="M12 17h.01" /></g>
          : <path d="M20 6 9 17l-5-5" />}
      </svg>
      {msg}
    </div>
  );
}
