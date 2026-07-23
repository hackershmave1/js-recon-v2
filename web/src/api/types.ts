export interface SessionView { session_id: string; scope_hosts: string[]; authorization_ack: boolean; }
export interface RunRef { run_id: string; state: string; }
export interface RunStatus {
  run_id: string; state: string; stage: string | null; done: number; total: number;
  pct: number | null; eta_seconds: number | null; heartbeat_at: string | null; stalled: boolean;
}
export interface Occurrence {
  host: string | null; raw_url: string | null; source_path: string | null;
  line: number | null; col: number | null; evidence: string | null;
  engine: string | null; confidence: string | null; verified: boolean | null;
}
export interface Triage { status: string; note: string | null; actor: string | null; updated_at: string; }
export interface Finding {
  finding_hash: string; type: string; value: string | null; path: string | null;
  severity: string | null; attributes: Record<string, unknown>; first_stage: string | null;
  revealable: boolean; triage: Triage | null; occurrences: Occurrence[];
}
export interface Coverage {
  attributed: number; unattributed: number; secrets: number; secrets_engine: string | null;
  sources_recovered: number; source_map: boolean;
  files: { path: string; attributed: number; unattributed: number }[];
}
export interface FindingsResponse { run_id: string; count: number; coverage: Coverage | null; findings: Finding[]; }
export const TERMINAL_STATES = new Set(["done", "partial", "failed", "cancelled"]);
export const TRIAGE_STATUSES = ["open", "confirmed", "dismissed"] as const;
