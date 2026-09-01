// Human labels for wire-level finding types, shared so every surface that shows a
// finding type renders it the same way (and none leak the raw wire token). The confirmed
// `endpoint` lane reads as "API" (a proven HTTP sink) and the promoted `endpoint_suspected`
// lane reads as "Endpoint" (a valid path recovered from a generic/unresolved sink) — both roll
// up into "total endpoints found". They contrast with the "page route" lane (`page_route` — a
// client-side navigation target, not a backend call). The still-unconfirmed lane is
// `endpoint_unresolved` (Tier 4 — a sink whose URL had no static path at all), shown as
// "unconfirmed". `endpoint_generic` (legacy — no longer produced; retained for older runs)
// shows as "generic call".
export const TYPE_LABELS: Record<string, string> = {
  endpoint: "API",
  endpoint_suspected: "endpoint",
  endpoint_unresolved: "unconfirmed",
  endpoint_generic: "generic call",
  page_route: "page route",
  // Opt-in low-confidence recall lane (D33-B): a suspected secret (~50% FP), the
  // recall counterpart to the precision `secret` lane. Shown as "suspected".
  secret_suspected: "suspected",
  // Cleartext internal-IP info-disclosure (e.g. "10.0.0.1"): NOT a secret — shown in
  // cleartext, never redacted/revealable. Labelled "internal IP".
  internal_ip: "internal IP",
  graphql: "GraphQL",
};
export const typeLabel = (t: string): string => TYPE_LABELS[t] ?? t;
