// theme.js — exact design tokens lifted from the original "RECON Capture"
// popup design mockup. Keep these hex values byte-for-byte with the design.

export const C = {
  // surfaces
  card: '#0F121A',
  panel: '#11141d',
  inset: '#0c0f16',
  control: '#161b26',
  // borders
  line: '#1c2230',
  lineStrong: '#232b3b',
  lineHover: '#2a3243',
  // text
  text: '#ECEFF6',
  textSoft: '#C7D0E0',
  muted: '#98A2B8',
  dim: '#7E8AA3',
  faint: '#5C6680',
  // accents
  lime: '#CDEB45',
  onLime: '#0B0D13',
  teal: '#5BD6C0',
  pink: '#FF6B8A',
  orange: '#FF8A47',
  amber: '#FFC73D',
  blue: '#6BA8FF',
  purple: '#C792EA'
};

export const F = {
  display: "'Space Grotesk', system-ui, sans-serif",
  body: "'IBM Plex Sans', system-ui, sans-serif",
  mono: "'JetBrains Mono', ui-monospace, monospace"
};

// asset classification → swatch colour + short label (mirrors prototype CC/CL maps)
export const CLASS_COLOR = { app: C.lime, lib: C.blue, cms: C.purple, tracker: C.dim };
export const CLASS_LABEL = { app: 'app', lib: 'lib', cms: 'plugin', tracker: 'tracker' };

