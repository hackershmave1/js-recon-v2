import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FindingsView } from "./FindingsView";
import { TenantProvider } from "../../tenant/TenantContext";
import type { FindingsResponse } from "../../api/types";

const data: FindingsResponse = {
  run_id: "r", count: 2,
  coverage: { attributed: 3, unattributed: 1, secrets: 1, secrets_engine: "kingfisher", sources_recovered: 0, source_map: false, files: [] },
  findings: [
    { finding_hash: "h1", type: "endpoint", value: "/api/users", path: null, severity: "info", attributes: {}, first_stage: "analyze", revealable: false, triage: null, occurrences: [{ host: null, raw_url: null, source_path: "app.js", line: 10, col: 4, offset_start: 100, offset_end: 120, evidence: "GET /api/users", engine: "vespasian", confidence: "high", verified: true }] },
    { finding_hash: "h2", type: "secret", value: "aws:sha256:abcd", path: null, severity: "high", attributes: {}, first_stage: "analyze", revealable: true, triage: null, occurrences: [{ host: null, raw_url: null, source_path: "app.js", line: 5, col: 2, offset_start: null, offset_end: null, evidence: "AKIAFAKEFAKEFAKESECRET", engine: "kingfisher", confidence: "high", verified: true }] },
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
    expect(screen.queryByText(/AKIAFAKEFAKEFAKESECRET/)).toBeNull();
  });
});
