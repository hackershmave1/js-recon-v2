import type { Coverage, Finding, HostRow } from "../../api/types";

export function countType(findings: Finding[], type: string): number {
  return findings.filter((f) => f.type === type).length;
}

// attributionPct is null when coverage is absent OR when both attributed+unattributed===0,
// meaning no analysis has run yet — callers must render "—", never "0%".
export function computeAttributionPct(c: Coverage | null): number | null {
  if (!c) return null;
  const total = c.attributed + c.unattributed;
  return total > 0 ? Math.round((c.attributed / total) * 100) : null;
}

// "Total reachable surface" matches the Overview Endpoints card exactly:
// confirmed API lane + promoted valid-path lane + in-scope same-origin page routes.
// page_route counts only when host-less (same-origin) or on an in-scope host, so
// out-of-scope sibling links (e.g. a .ca domain) don't inflate the surface.
export function computeEndpoints(findings: Finding[], hostRows: HostRow[]): number {
  const inScope = new Set(hostRows.filter((h) => h.in_scope).map((h) => h.host));
  const api = countType(findings, "endpoint");
  const suspected = countType(findings, "endpoint_suspected");
  const routes = findings.filter((f) => {
    if (f.type !== "page_route") return false;
    const host = f.occurrences.find((o) => o.host)?.host;
    return !host || inScope.has(host);
  }).length;
  return api + suspected + routes;
}

// Prefer the coverage.secrets scalar (Kingfisher's deduplicated count) over the
// raw finding count; fall back to counting when coverage is absent.
export function computeSecrets(c: Coverage | null, findings: Finding[]): number {
  return c ? c.secrets : countType(findings, "secret");
}

// Partial-coverage notes: one note per condition, callers display under one banner.
export function computePartialNotes(c: Coverage | null): string[] {
  const notes: string[] = [];
  if (c?.curtailed) {
    notes.push(
      "Extraction hit the analyzer's size budget on a very large bundle — some endpoints and hosts may be missing.",
    );
  }
  if (c?.source_map === "skipped") {
    notes.push(
      "A referenced source map couldn't be fetched (too large or unavailable) — recovered original sources, and any secrets in them, may be incomplete.",
    );
  }
  return notes;
}
