import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TenantProvider } from "../../tenant/TenantContext";
import { FindingDetail } from "./FindingDetail";
import type { Finding, Occurrence } from "../../api/types";

const occ = (over: Partial<Occurrence> = {}): Occurrence => ({
  host: null, raw_url: null, source_path: "app.js", line: 10, col: 4,
  offset_start: null, offset_end: null, evidence: null, engine: "vespasian",
  confidence: "high", verified: true, asset_url: null, ...over,
});
const finding = (over: Partial<Finding> = {}): Finding => ({
  finding_hash: "h", type: "endpoint", value: "/api/users", path: null, severity: null,
  attributes: {}, first_stage: "analyze", revealable: false, triage: null,
  spec_status: null, occurrences: [occ()], ...over,
});
const show = (f: Finding) => render(<TenantProvider><FindingDetail finding={f} runId="r" /></TenantProvider>);

describe("FindingDetail", () => {
  it("renders non-secret evidence but redacts a secret's evidence", () => {
    show(finding({ occurrences: [occ({ evidence: "GET /api/users" })] }));
    expect(screen.getByText(/GET \/api\/users/)).toBeInTheDocument();
  });

  it("suppresses evidence for secret-type findings", () => {
    show(finding({ type: "secret", value: "aws:sha256:abcd", revealable: true,
      occurrences: [occ({ evidence: "LEAKED-SECRET-MARKER-XYZ" })] }));
    expect(screen.queryByText(/LEAKED-SECRET-MARKER-XYZ/)).toBeNull();
  });

  it("attributes an occurrence to its source asset when asset_url is present", () => {
    show(finding({ occurrences: [occ({ asset_url: "https://acme.io/app.js" })] }));
    expect(screen.getByText(/https:\/\/acme\.io\/app\.js/)).toBeInTheDocument();
  });

  it("renders 'unclassified' when spec_status is null", () => {
    show(finding({ spec_status: null }));
    expect(screen.getByText("unclassified")).toHaveClass("chip-unclassified");
  });

  it("renders a distinct chip class for each real spec_status verdict", () => {
    show(finding({ spec_status: { status: "documented", reason: null, matched_operation: "GET /x" } }));
    expect(screen.getByText("documented")).toHaveClass("chip-documented");
    show(finding({ finding_hash: "h2", spec_status: { status: "shadow", reason: null, matched_operation: null } }));
    expect(screen.getByText("shadow")).toHaveClass("chip-shadow");
    show(finding({ finding_hash: "h3", spec_status: { status: "unresolved", reason: null, matched_operation: null } }));
    expect(screen.getByText("unresolved")).toHaveClass("chip-unresolved");
  });
});
