export interface SessionView { session_id: string; scope_hosts: string[]; authorization_ack: boolean; }
export interface RunRef { run_id: string; state: string; }
// The source run's editable config for the edit-&-re-run prefill (GET /runs/{id}/config).
// `is_upload` => the re-run re-analyzes the stored bytes: keep the target (a base-URL
// hint) editable but hide the capture toggle + fetch cap, which don't apply. `max_fetch_bytes`
// is a per-run override in BYTES (null = the global default).
export interface RunConfig {
  run_id: string; target: string | null; crawl_mode: string | null;
  scope_hosts: string[]; max_fetch_bytes: number | null; is_upload: boolean;
}
export interface RunStatus {
  run_id: string; session_id?: string; state: string; stage: string | null; done: number; total: number;
  pct: number | null; eta_seconds: number | null; heartbeat_at: string | null; stalled: boolean;
  // Cooperative-control intent (REQ-A4): requested but not necessarily effected yet.
  // Lets run-control gating survive a page reload mid-pause (ui-catch-up §10, A1).
  pause_requested: boolean; cancel_requested: boolean;
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
// Slice 4: cross-run "sightings" of a finding within its engagement -- counts of
// OTHER runs sharing this finding_hash, by origin (capture = browser extension,
// platform = crawl/upload). Finding.sightings === null means "ungrouped" (the run's
// session has no engagement, so no cross-run collapse) -- distinct from {0,0}
// (grouped, but unique to this run). Optional: absent on pre-slice-4 fixtures.
export interface Sightings { capture: number; platform: number; }
export interface Finding {
  finding_hash: string; type: string; value: string | null; path: string | null;
  severity: string | null; attributes: Record<string, unknown>; first_stage: string | null;
  revealable: boolean; triage: Triage | null; spec_status: SpecStatus | null;
  sightings?: Sightings | null; occurrences: Occurrence[];
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
  assets: { url: string; source: string; fetch_status: AssetStatus; analyze_status: AssetStatus; fetch_error?: string | null; analyze_error?: string | null }[];
}
// Cross-file base-URL rule (design REQ-C2): an analyst-supplied prefix or
// finding-set mapping that prepends a base to relative paths missing one,
// before resolve_operation compares against the attached spec. `rule.actor`
// is who added it (audit trail), not who it applies to.
export interface BaseUrlRule {
  id: string;
  kind: "prefix" | "selection";
  path_prefix: string | null;
  finding_hashes: string[];
  base_url: string;
  actor: string | null;
}
export interface BaseUrlRuleResult { rule: BaseUrlRule; summary: SpecSummary | null; }
// Taught HTTP-client wrapper (design REQ-C2 first clause): a callee whose member
// calls (`api.get('/x')`) the extractor treats as endpoints. `recovered` is the
// number of finding/occurrence rows the re-extract wrote (0 when nothing is new).
export interface WrapperRule {
  id: string;
  callee: string;
  actor: string | null;
}
export interface WrapperRuleResult { rule: WrapperRule; recovered: number; }
// One reconstructed request from GET /runs/{id}/requests (probe_router::_request_dict).
// `artifacts` is null when `probeable` is false.
export interface ReconstructedRequest {
  operation: string; method: string; path: string; hosts: string[];
  query_params: { name: string; example: string | null }[];
  body_params: string[]; content_type: string | null; example_url: string | null;
  probeable: boolean; endpoint_hashes: string[];
  artifacts: { curl: string; http: string } | null;
}
export interface RequestsResponse { run_id: string; count: number; requests: ReconstructedRequest[]; }
// One stored source file for a run (GET /runs/{id}/sources). `path` is "input.js"
// for a legacy single-bundle run, or the asset URL for a crawl asset; `kind`
// distinguishes them. `fetch_status` === "ok" means the bytes are viewable — a
// pending/failed crawl asset is listed but has no content to fetch.
// `kind:"source"` is a source-map-recovered original (e.g. webpack:/…/social.js);
// `asset_url` names the owning crawl asset for those (null for asset/upload, or for
// a legacy run-level map). See recon.probe.sources for the join keys.
export interface SourceFile { path: string; kind: "asset" | "upload" | "source"; fetch_status: string; asset_url: string | null; }
export interface SourcesResponse { run_id: string; count: number; sources: SourceFile[]; }
// A finding occurrence's "jump to its source" request (Findings drawer -> Sources).
// Any field may be null: an occurrence without a source_path/asset_url can still
// carry a line, and a legacy occurrence resolves to the "input.js" bundle.
export interface SourceJump { sourcePath: string | null; assetUrl: string | null; line: number | null; }
// One source file's decoded text (GET /runs/{id}/sources/content?path=).
// `truncated` is true when the raw blob exceeded the server's response cap.
export interface SourceContent { path: string; content: string; truncated: boolean; }
// Result of POST pause/cancel/resume. pause returns pause_requested; cancel returns
// cancel_requested; resume returns neither — all three return the authoritative state.
export interface RunControlResult { run_id: string; state: string; pause_requested?: boolean; cancel_requested?: boolean; }
// R6 Sessions surface. One card from GET /sessions (recon.sessions.service.SessionSummary):
// the session plus its LATEST run's real stats (a finding_hash recurs across runs, so
// summing would double-count). A `null` stat means "no run yet" or "analyze hasn't
// emitted" — the UI renders those as "—", never a faked number.
export interface SessionRunRef {
  run_id: string; state: string;
  created_at: string | null; started_at: string | null; ended_at: string | null; target: string | null;
}
export interface SessionSummary {
  session_id: string; external_id?: string | null; name: string | null; host: string; scope_hosts: string[];
  engagement_id: string | null; archived: boolean; created_at: string | null;
  latest_run: SessionRunRef | null;
  files: number | null; endpoints: number | null; secrets: number | null;
  coverage_pct: number | null; // attribution coverage %, null until analyze emits
}
export interface SessionsListResponse { count: number; sessions: SessionSummary[]; }
export interface SessionRunsResponse { session_id: string; count: number; runs: SessionRunRef[]; }
// The widened session view returned by POST/PATCH /sessions (superset of SessionView).
export interface SessionDetail {
  session_id: string; name: string | null; scope_hosts: string[]; authorization_ack: boolean;
  created_at: string | null; engagement_id: string | null; archived: boolean;
}
// R6 Engagement tier (recon.engagements.service.EngagementView): a named scope umbrella
// grouping sessions. Scope here is organizational metadata; a run's enforced egress
// scope still comes from its session's scope_hosts (REQ-P2), never an engagement.
export interface Engagement {
  engagement_id: string; name: string;
  in_scope_domains: string[]; out_of_scope_domains: string[];
  created_at: string; updated_at: string;
}
export interface EngagementsListResponse { count: number; engagements: Engagement[]; }
// Tech detection: one detected technology (recon.findings.queries.TechnologyView).
// `version` is null when not statically derivable (Phase 1 honesty — T12).
export interface Technology {
  name: string; categories: string[]; version: string | null; confidence: number; evidence: string[];
}
// GET /runs/{id}/technologies — per-host stack. `hosts` empty (200) is distinct
// from a 404 unknown run.
export interface TechnologiesResponse {
  run_id: string; count: number; hosts: Record<string, Technology[]>;
}

export const TERMINAL_STATES = new Set(["done", "partial", "failed", "cancelled"]);
export const TRIAGE_STATUSES = ["open", "confirmed", "dismissed"] as const;
