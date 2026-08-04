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

// Bottom-centred confirmation toast.
export function Toast({ message }) {
  if (!message) return null;
  return (
    <div style={{
      position: 'absolute', bottom: '12px', left: '50%', transform: 'translateX(-50%)',
      background: '#1a1f2c', border: `1px solid ${C.lineHover}`, borderRadius: '9px',
      padding: '9px 15px', fontSize: '11.5px', color: C.lime, display: 'flex',
      alignItems: 'center', gap: '7px', boxShadow: '0 10px 30px rgba(0,0,0,0.5)', zIndex: 10
    }}>
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M20 6 9 17l-5-5" />
      </svg>
      {message}
    </div>
  );
}
