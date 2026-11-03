// The Findings rail's "captured, not shown" line. Findings are captured whole but only the
// meaningful ones are shown by default (backend findings/visibility); this says how many are
// held back and why, so nothing is silently missing.
const REASON_LABEL: Record<string, string> = {
  junk_or_dynamic: "junk",
  noise: "analytics",
  off_scope: "off-scope",
};

export function hiddenTotal(hidden: Record<string, number> | undefined): number {
  return Object.values(hidden ?? {}).reduce((a, b) => a + b, 0);
}

export function hiddenSummary(hidden: Record<string, number> | undefined): string {
  const parts = Object.entries(hidden ?? {})
    .filter(([, n]) => n > 0)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([reason, n]) => `${REASON_LABEL[reason] ?? reason} ${n}`);
  return parts.join(" · ");
}
