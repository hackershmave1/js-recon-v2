// Human labels for wire-level finding types, shared so every surface that shows a
// finding type renders it the same way (and none leak the raw wire token). The
// unconfirmed lane (Tier 4) rides the wire as `endpoint_unresolved` — a network sink
// we detected but whose URL wasn't statically resolvable — and is shown as "unconfirmed".
export const TYPE_LABELS: Record<string, string> = { endpoint_unresolved: "unconfirmed" };
export const typeLabel = (t: string): string => TYPE_LABELS[t] ?? t;
