export interface SessionView { session_id: string; scope_hosts: string[]; authorization_ack: boolean; }
export interface RunRef { run_id: string; state: string; }
export interface RunStatus {
  run_id: string; state: string; stage: string | null; done: number; total: number;
  pct: number | null; eta_seconds: number | null; heartbeat_at: string | null; stalled: boolean;
}
export interface Occurrence {
  host: string | null; raw_url: string | null; source_path: string | null;
  line: number | null; col: number | null; offset_start: number | null; offset_end: number | null; evidence: string | null;
  engine: string | null; confidence: string | null; verified: boolean | null;
  // Slice Y: which discovered asset this sighting came from; null for legacy
  // (pre-crawl, single-asset) occurrences.
  asset_url: string | null;
}
export interface Triage { status: string; note: string | null; actor: string | null; updated_at: string; }
// Shadow-API classification verdict (design §6.4) for one endpoint finding.
// Absent (Finding.spec_status === null) means "never classified" -- no spec
// attached to the session, or this finding isn't an endpoint -- the FE
// renders that case as "unclassified".
export interface SpecStatus {
  status: "documented" | "shadow" | "unresolved";
  reason: string | null;
  matched_operation: string | null;
}
export interface Finding {
  finding_hash: string; type: string; value: string | null; path: string | null;
  severity: string | null; attributes: Record<string, unknown>; first_stage: string | null;
  revealable: boolean; triage: Triage | null; spec_status: SpecStatus | null; occurrences: Occurrence[];
}
export interface Coverage {
  attributed: number; unattributed: number; secrets: number; secrets_engine: string | null;
  sources_recovered: number; source_map: boolean;
  files: { path: string; attributed: number; unattributed: number }[];
}
// Run-scoped shadow-API bucket summary (design §5.4/§6.4): null until a spec is
// attached to the run's session at all -- distinct from an attached spec whose
// buckets are all zero. Returned by both GET /runs/{id}/findings ("spec") and
// POST /runs/{id}/spec (the same 5 keys, unwrapped).
export interface SpecSummary {
  documented: number;
  shadow: number;
  unresolved: number;
  suffix_verify: number;
  base_url_incompleteness_ratio: number;
}
export interface FindingsResponse {
  run_id: string; count: number; coverage: Coverage | null; spec: SpecSummary | null; findings: Finding[];
}
// Per-asset fetch/analyze outcome (recon.domain.AssetStatus). "pending" until the
// corresponding stage has touched the asset.
export type AssetStatus = "pending" | "ok" | "failed";
export interface AssetsManifest {
  domain: string | null;
  status: "pending" | "ok" | "capped" | "timeout";
  assets: { url: string; source: string; fetch_status: AssetStatus; analyze_status: AssetStatus }[];
}
export const TERMINAL_STATES = new Set(["done", "partial", "failed", "cancelled"]);
export const TRIAGE_STATUSES = ["open", "confirmed", "dismissed"] as const;
