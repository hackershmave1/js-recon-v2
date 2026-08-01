import type { ReactNode } from "react";

// Small stroke-icon set for the shell nav + top bar. Kept inline (no icon
// dependency) and drawn on a 24-grid with currentColor so each icon inherits
// the nav item's colour + active-state lime for free.
const PATHS: Record<string, ReactNode> = {
  grid: (<><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /></>),
  alert: (<><path d="M12 3.6 21 19H3z" /><path d="M12 10v4" /><path d="M12 16.5h.01" /></>),
  code: (<><path d="M8 6l-6 6 6 6" /><path d="M16 6l6 6-6 6" /></>),
  shield: (<><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" /><path d="M9 12l2 2 4-4" /></>),
  folder: (<path d="M3 7a2 2 0 012-2h3.5l2 2H19a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />),
  layers: (<><path d="M12 3l9 5-9 5-9-5z" /><path d="M3 13l9 5 9-5" /></>),
  search: (<><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></>),
  download: (<><path d="M12 3v12" /><path d="M7 11l5 5 5-5" /><path d="M4 20h16" /></>),
  plus: (<><path d="M12 5v14" /><path d="M5 12h14" /></>),
};

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name] ?? null}
    </svg>
  );
}
