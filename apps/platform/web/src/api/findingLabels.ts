// Human labels for wire-level finding types, shared so every surface that shows a
// finding type renders it the same way (and none leak the raw wire token). The confirmed
// `endpoint` lane reads as "API" to contrast it with the "page route" lane (`page_route` —
// a client-side navigation target, not a backend call). The unconfirmed lane rides the wire
// as two distinct confidence tiers: `endpoint_unresolved` (Tier 4 — a network sink we
// detected but whose URL wasn't statically resolvable) shows as "unconfirmed", and
// `endpoint_generic` (Tier 5 — a verb call on an unrecognised but HTTP-client-shaped
// receiver, a suspected untaught client) shows as "generic call".
export const TYPE_LABELS: Record<string, string> = {
  endpoint: "API",
  endpoint_unresolved: "unconfirmed",
  endpoint_generic: "generic call",
  page_route: "page route",
};
export const typeLabel = (t: string): string => TYPE_LABELS[t] ?? t;
