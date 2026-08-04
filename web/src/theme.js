// theme.js — design tokens lifted byte-for-byte from the "RECON Workspace" prototype.

export const C = {
  // surfaces
  app: '#0B0D13',
  panel2: '#0F121A',   // sidebar / drawers
  panel: '#11141d',    // cards
  inset: '#0c0f16',    // code / inputs
  control: '#161b26',
  control2: '#141824',
  rowActive: '#141a26',
  filterActive: '#1a2030',
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
  blue: '#6BA8FF',
  indigo: '#7C8CFF',
  pink: '#FF6B8A',
  red: '#FF4D5E',
  orange: '#FF8A47',
  amber: '#FFC73D',
  purple: '#C792EA',
  teal: '#5BD6C0',
  dep: '#E0A458'
};

export const F = {
  display: "'Space Grotesk', system-ui, sans-serif",
  body: "'IBM Plex Sans', system-ui, sans-serif",
  mono: "'JetBrains Mono', ui-monospace, monospace"
};

export const SEV = {
  critical: { c: '#FF4D5E', bg: 'rgba(255,77,94,0.13)', label: 'Critical' },
  high: { c: '#FF8A47', bg: 'rgba(255,138,71,0.13)', label: 'High' },
  medium: { c: '#FFC73D', bg: 'rgba(255,199,61,0.13)', label: 'Medium' },
  low: { c: '#6BA8FF', bg: 'rgba(107,168,255,0.13)', label: 'Low' },
  info: { c: '#7E8AA3', bg: 'rgba(126,138,163,0.13)', label: 'Info' }
};

export const TYPE = {
  secret: { label: 'SECRET', c: '#FF6B8A', bd: 'rgba(255,107,138,0.35)' },
  endpoint: { label: 'ENDPOINT', c: '#7C8CFF', bd: 'rgba(124,140,255,0.35)' },
  path: { label: 'PATH', c: '#5BD6C0', bd: 'rgba(91,214,192,0.35)' },
  param: { label: 'PARAM', c: '#C792EA', bd: 'rgba(199,146,234,0.35)' },
  dependency: { label: 'DEP', c: '#E0A458', bd: 'rgba(224,164,88,0.35)' }
};

export const STATUS = {
  new: { label: 'New', c: '#CDEB45', bg: 'rgba(205,235,69,0.13)' },
  reviewed: { label: 'Reviewed', c: '#6BA8FF', bg: 'rgba(107,168,255,0.13)' },
  confirmed: { label: 'Confirmed', c: '#FF4D5E', bg: 'rgba(255,77,94,0.13)' },
  false_positive: { label: 'False pos.', c: '#7E8AA3', bg: 'rgba(126,138,163,0.13)' }
};

export const CONF = {
  high: { label: 'High', c: '#CDEB45' },
  medium: { label: 'Medium', c: '#FFC73D' },
  low: { label: 'Low', c: '#7E8AA3' }
};

export const CLS = {
  app: { label: 'App code', c: '#CDEB45', bd: 'rgba(205,235,69,0.35)' },
  lib: { label: 'Library', c: '#6BA8FF', bd: 'rgba(107,168,255,0.35)' },
  cms: { label: 'CMS/plugin', c: '#C792EA', bd: 'rgba(199,146,234,0.35)' },
  tracker: { label: 'Tracker', c: '#7E8AA3', bd: 'rgba(126,138,163,0.35)' }
};

export const SCOPE = {
  in: { label: 'In scope', c: '#CDEB45', bd: 'rgba(205,235,69,0.35)' },
  sub: { label: 'Subdomain', c: '#5BD6C0', bd: 'rgba(91,214,192,0.35)' },
  third: { label: 'Third-party', c: '#FF8A47', bd: 'rgba(255,138,71,0.4)' }
};
