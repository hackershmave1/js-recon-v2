// Human labels for wire-level finding types, shared so every surface that shows a
// finding type renders it the same way (and none leak the raw wire token). Each endpoint
// lane names its confidence so two lanes never read as synonyms (QA: "API 14" next to
// "endpoint 1" looked like the same thing counted twice):
//   endpoint            -> "API"            a proven HTTP sink with a resolved URL
//   endpoint_suspected  -> "inferred API"   a valid path recovered from a generic/unresolved
//                                           sink; rolls into "total endpoints" with "API"
//   endpoint_unresolved -> "suspected call" a real sink whose URL had no static path — the
//                                           Hosts page's "Suspected" column and OPERATING.md's
//                                           "suspected" lane use the same word
//   endpoint_generic    -> "generic call"   legacy (no longer produced; older runs only)
// "page route" (`page_route`) is client-side navigation, not a backend call.
export const TYPE_LABELS: Record<string, string> = {
  endpoint: "API",
  endpoint_suspected: "inferred API",
  endpoint_unresolved: "suspected call",
  endpoint_generic: "generic call",
  page_route: "page route",
  // Opt-in low-confidence recall lane (D33-B): a suspected secret (~50% FP), the
  // recall counterpart to the precision `secret` lane. Named in full so it can't be read
  // as an endpoint lane in the shared Type facet.
  secret_suspected: "suspected secret",
  // Cleartext internal-IP info-disclosure (e.g. "10.0.0.1"): NOT a secret — shown in
  // cleartext, never redacted/revealable. Labelled "internal IP".
  internal_ip: "internal IP",
  graphql: "GraphQL",
  // Client-side data-flow sinks (D52): postMessage listener (XSS-via-message attack
  // surface) and Web Storage / cookie writes (persistence of user-controlled data).
  // Informational — NOT secrets, NOT endpoints.
  postmessage_sink: "postMessage",
  storage_sink: "storage write",
};
export const typeLabel = (t: string): string => TYPE_LABELS[t] ?? t;
