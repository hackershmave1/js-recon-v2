import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FindingsView } from "./FindingsView";
import { TenantProvider } from "../../tenant/TenantContext";
import type { FindingsResponse } from "../../api/types";

const data: FindingsResponse = {
  run_id: "r", count: 2, spec: null,
  coverage: { attributed: 3, unattributed: 1, secrets: 1, secrets_engine: "kingfisher", sources_recovered: 0, source_map: false, files: [] },
  findings: [
    { finding_hash: "h1", type: "endpoint", value: "/api/users", path: null, severity: "info", attributes: {}, first_stage: "analyze", revealable: false, triage: null, spec_status: null, occurrences: [{ host: null, raw_url: null, source_path: "app.js", line: 10, col: 4, offset_start: 100, offset_end: 120, evidence: "GET /api/users", engine: "vespasian", confidence: "high", verified: true, asset_url: "https://acme.io/app.js" }] },
    { finding_hash: "h2", type: "secret", value: "aws:sha256:abcd", path: null, severity: "high", attributes: {}, first_stage: "analyze", revealable: true, triage: null, spec_status: null, occurrences: [{ host: null, raw_url: null, source_path: "app.js", line: 5, col: 2, offset_start: null, offset_end: null, evidence: "LEAKED-SECRET-MARKER-XYZ", engine: "kingfisher", confidence: "high", verified: true, asset_url: null }] },
  ],
};

// Task 12: one finding per spec_status verdict (+ the base fixture's h1/h2
// above cover the null -> "unclassified" case already).
const specData: FindingsResponse = {
  run_id: "r2", count: 3,
  spec: { documented: 1, shadow: 1, unresolved: 1, suffix_verify: 0, base_url_incompleteness_ratio: 0 },
  coverage: null,
  findings: [
    { finding_hash: "d1", type: "endpoint", value: "GET /api/known", path: null, severity: "info", attributes: {}, first_stage: "analyze", revealable: false, triage: null, occurrences: [],
      spec_status: { status: "documented", reason: "documented", matched_operation: "GET /api/known" } },
    { finding_hash: "s1", type: "endpoint", value: "POST /admin/wipe", path: null, severity: "high", attributes: {}, first_stage: "analyze", revealable: false, triage: null, occurrences: [],
      spec_status: { status: "shadow", reason: "undocumented-path", matched_operation: null } },
    { finding_hash: "u1", type: "endpoint", value: "GET /api/known/extra", path: null, severity: "info", attributes: {}, first_stage: "analyze", revealable: false, triage: null, occurrences: [],
      spec_status: { status: "unresolved", reason: "suffix-verify", matched_operation: "GET /api/known" } },
  ],
};

describe("FindingsView", () => {
  it("shows coverage and groups findings by type without leaking a secret value", () => {
    render(<TenantProvider><FindingsView data={data} runId="r" /></TenantProvider>);
    expect(screen.getByText(/attributed/i)).toBeInTheDocument();
    // "endpoint"/"secret" render twice each (group header + per-finding badge).
    expect(screen.getAllByText("endpoint")).toHaveLength(2);
    expect(screen.getAllByText("secret")).toHaveLength(2);
    // Non-secret evidence IS rendered; proves the gate allows non-secrets.
    expect(screen.getByText(/GET \/api\/users/)).toBeInTheDocument();
    // Secret evidence is suppressed; proves the redaction gate blocks secrets.
    expect(screen.queryByText(/LEAKED-SECRET-MARKER-XYZ/)).toBeNull();
  });

  it("attributes each occurrence to its source asset (Slice Y), omitting it for legacy occurrences", () => {
    render(<TenantProvider><FindingsView data={data} runId="r" /></TenantProvider>);
    // h1's occurrence carries asset_url -> shown.
    expect(screen.getByText(/https:\/\/acme\.io\/app\.js/)).toBeInTheDocument();
    // h2's occurrence has asset_url: null (legacy) -> no attribution text rendered
    // for it; the only asset_url on the page is h1's.
    expect(screen.getAllByText(/https:\/\/acme\.io\/app\.js/)).toHaveLength(1);
  });

  // Task 12 (design §6.4 UI): per-finding spec_status chip + shadow-only filter.
  it("renders 'unclassified' for findings whose spec_status is null", () => {
    render(<TenantProvider><FindingsView data={data} runId="r" /></TenantProvider>);
    // h1 and h2 both carry spec_status: null.
    expect(screen.getAllByText("unclassified")).toHaveLength(2);
  });

  it("renders a distinct chip for each real spec_status verdict", () => {
    render(<TenantProvider><FindingsView data={specData} runId="r2" /></TenantProvider>);
    const documented = screen.getByText("documented");
    const shadow = screen.getByText("shadow");
    const unresolved = screen.getByText("unresolved");
    expect(documented.className).toContain("chip-documented");
    expect(shadow.className).toContain("chip-shadow");
    expect(unresolved.className).toContain("chip-unresolved");
  });

  it("shadow-only filter hides documented/unresolved findings without a new fetch", async () => {
    render(<TenantProvider><FindingsView data={specData} runId="r2" /></TenantProvider>);
    // Unfiltered: all three endpoint values are present.
    expect(screen.getByText("GET /api/known")).toBeInTheDocument();
    expect(screen.getByText("POST /admin/wipe")).toBeInTheDocument();
    expect(screen.getByText("GET /api/known/extra")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: /shadow only/i }));

    expect(screen.getByText("POST /admin/wipe")).toBeInTheDocument();
    expect(screen.queryByText("GET /api/known")).toBeNull();
    expect(screen.queryByText("GET /api/known/extra")).toBeNull();
  });

  it("wires the run's already-attached spec summary into the upload control", () => {
    render(<TenantProvider><FindingsView data={specData} runId="r2" /></TenantProvider>);
    expect(screen.getByText(/documented 1 · shadow 1 · unresolved 1/)).toBeInTheDocument();
  });
});
