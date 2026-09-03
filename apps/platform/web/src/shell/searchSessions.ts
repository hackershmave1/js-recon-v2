import type { SessionSummary } from "../api/types";

// D54: the global search's matcher — pure so it's unit-testable apart from the input/dropdown.
// Matches a session by name, host, external id, or any scope host; empty query => no results
// (the dropdown stays closed), capped so the dropdown stays small.
export function matchSessions(sessions: SessionSummary[], query: string, limit = 8): SessionSummary[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  return sessions
    .filter((s) =>
      [s.name, s.host, s.external_id, ...(s.scope_hosts || [])]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(needle)),
    )
    .slice(0, limit);
}
