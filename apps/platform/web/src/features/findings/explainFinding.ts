import type { Finding } from "../../api/types";
import { EXPLAIN_TABLE } from "./findingExplainText";

// Turns a finding into the four things the detail drawer says about it (L2 / F16). Static
// words come from findingExplainText; everything run-specific is derived ONLY from fields the
// API already returns. Rule: when the data does not support a statement, leave it out.

export interface Explanation {
  kind: string | null;
  confidence: string[];
  severityReason: string | null;
  nextSteps: string[];
}

const CONFIDENCE_RANK: Record<string, number> = { high: 3, medium: 2, low: 1 };
const ENDPOINT_TYPES = new Set(["endpoint", "endpoint_suspected", "endpoint_unresolved", "endpoint_generic"]);

// Highest engine confidence across occurrences, with the engine that reported it.
function bestEngineConfidence(f: Finding): { confidence: string; engine: string | null } | null {
  let best: { confidence: string; engine: string | null } | null = null;
  for (const o of f.occurrences) {
    if (!o.confidence) continue;
    if (!best || (CONFIDENCE_RANK[o.confidence] ?? 0) > (CONFIDENCE_RANK[best.confidence] ?? 0)) {
      best = { confidence: o.confidence, engine: o.engine };
    }
  }
  return best;
}

function severityReason(f: Finding): string | null {
  const r = f.priority_reason;
  if (!r || !f.severity) return null;
  // The reason explains the derived label only; a severity stored by another pipeline is not
  // explained by it.
  if (r.label !== f.severity) return null;
  const tag = r.risk_tag ? `, +${r.bump} for the "${r.risk_tag}" risk tag` : "";
  const cap = r.base + r.bump > r.score ? " (capped at 100)" : "";
  return `${f.severity}: score ${r.score}${cap} = base ${r.base} for this finding type${tag}.`;
}

function confidenceLines(f: Finding, certainty: string | undefined): string[] {
  const lines: string[] = [];
  if (certainty) lines.push(certainty);
  const best = bestEngineConfidence(f);
  if (best) lines.push(`${best.engine ?? "The engine"} reports ${best.confidence} confidence.`);
  if (f.occurrences.some((o) => o.verified === true)) {
    lines.push("The secret scanner's validation reported this credential as active.");
  }
  if (ENDPOINT_TYPES.has(f.type) && f.type !== "endpoint_generic") {
    const method = f.attributes.method;
    if (method === "ANY" || method === "") lines.push("The HTTP method is not known statically, so it is shown as ANY.");
  }
  if (f.attributes.dynamic_query === true) lines.push("Part of the query string is built at runtime.");
  if (Array.isArray(f.attributes.path_params) && f.attributes.path_params.length > 0) {
    lines.push("The path has placeholders ({id}-style) where the code inserts a value.");
  }
  const captured = f.sightings?.capture ?? 0;
  if (captured > 0) {
    lines.push(`Also seen in ${captured} other run${captured === 1 ? "" : "s"} captured by the extension.`);
  }
  return lines;
}

function specStep(f: Finding): string | null {
  switch (f.spec_status?.status) {
    case "shadow": return "It is not in the attached spec: treat it as undocumented and probe it first.";
    case "documented": return "It matches the attached spec: compare the documented auth and parameters with the code.";
    case "unresolved": return "It could not be matched to the attached spec: add a base URL, then re-check.";
    default: return null;
  }
}

export function explainFinding(f: Finding): Explanation {
  const row = EXPLAIN_TABLE[f.type];
  const nextSteps = row ? [...row.nextSteps] : [];
  const spec = row ? specStep(f) : null;
  if (spec) nextSteps.push(spec);
  return {
    kind: row?.kind ?? null,
    confidence: confidenceLines(f, row?.certainty),
    severityReason: severityReason(f),
    nextSteps,
  };
}
