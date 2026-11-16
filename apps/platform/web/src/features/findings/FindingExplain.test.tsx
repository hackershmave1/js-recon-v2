import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { FindingExplain } from "./FindingExplain";
import { EXPLAIN_TABLE } from "./findingExplainText";
import { TYPE_LABELS } from "../../api/findingLabels";
import type { Finding, Occurrence } from "../../api/types";

const occ = (over: Partial<Occurrence> = {}): Occurrence => ({
  host: null, raw_url: null, source_path: "app.js", line: 1, col: 0, offset_start: null,
  offset_end: null, evidence: null, engine: "vespasian", confidence: "high", verified: null,
  asset_url: null, ...over,
});
const finding = (over: Partial<Finding> = {}): Finding => ({
  finding_hash: "h", type: "endpoint", value: "/api/users", path: null, severity: "high",
  priority: 65, priority_reason: { base: 45, risk_tag: "idor", bump: 20, score: 65, label: "high" },
  attributes: { method: "GET" }, first_stage: "analyze", revealable: false, triage: null,
  spec_status: null, occurrences: [occ()], ...over,
});
const section = (name: RegExp) => screen.getByRole("region", { name });

describe("FindingExplain", () => {
  it("has a kind line and 1-3 next steps for every known finding type", () => {
    for (const type of [...Object.keys(TYPE_LABELS), "param"]) {
      const row = EXPLAIN_TABLE[type];
      expect(row, type).toBeDefined();
      expect(row.kind.length, type).toBeGreaterThan(10);
      expect(row.nextSteps.length, type).toBeGreaterThanOrEqual(1);
      expect(row.nextSteps.length, type).toBeLessThanOrEqual(3);
    }
  });

  it("shows all four sections for a resolved endpoint", () => {
    render(<FindingExplain finding={finding()} />);
    expect(within(section(/what it is/i)).getByText(/HTTP call/i)).toBeInTheDocument();
    expect(within(section(/how sure/i)).getByText(/resolved URL/i)).toBeInTheDocument();
    expect(within(section(/why this severity/i)).getByText(/base 45/i)).toBeInTheDocument();
    expect(within(section(/what to do next/i)).getAllByRole("listitem").length).toBeGreaterThan(0);
  });

  it("explains an unresolved runtime value as low certainty about the URL", () => {
    render(<FindingExplain finding={finding({ type: "endpoint_unresolved", value: "GET ${a}" })} />);
    expect(within(section(/how sure/i)).getByText(/no static path/i)).toBeInTheDocument();
  });

  it("names the risk tag that raised the severity, with the exact numbers", () => {
    render(<FindingExplain finding={finding()} />);
    const text = section(/why this severity/i).textContent ?? "";
    expect(text).toMatch(/high/);
    expect(text).toMatch(/base 45/i);
    expect(text).toMatch(/idor/);
    expect(text).toMatch(/\+20/);
    expect(text).toMatch(/65/);
  });

  it("omits the severity section when the API gave no reason (older server)", () => {
    render(<FindingExplain finding={finding({ priority_reason: undefined })} />);
    expect(screen.queryByRole("region", { name: /why this severity/i })).toBeNull();
  });

  it("omits the severity section when the stored severity disagrees with the derived label", () => {
    // A stored severity set by some other pipeline is not explained by priority_reason.
    render(<FindingExplain finding={finding({ severity: "low" })} />);
    expect(screen.queryByRole("region", { name: /why this severity/i })).toBeNull();
  });

  it("omits the confidence section when there is neither a type statement nor occurrence data", () => {
    render(<FindingExplain finding={finding({ type: "mystery", occurrences: [occ({ confidence: null, engine: null })] })} />);
    expect(screen.queryByRole("region", { name: /how sure/i })).toBeNull();
    expect(screen.queryByRole("region", { name: /what it is/i })).toBeNull();
    expect(screen.queryByRole("region", { name: /what to do next/i })).toBeNull();
  });

  it("reports engine confidence and validation only when the occurrence carries them", () => {
    render(<FindingExplain finding={finding({ type: "secret", value: "aws:sha256:ab", attributes: {},
      occurrences: [occ({ engine: "kingfisher", confidence: "medium", verified: true })] })} />);
    const text = section(/how sure/i).textContent ?? "";
    expect(text).toMatch(/kingfisher/i);
    expect(text).toMatch(/medium/i);
    expect(text).toMatch(/active/i);
  });

  it("does not claim validation when verified is null or false", () => {
    render(<FindingExplain finding={finding({ type: "secret", attributes: {},
      occurrences: [occ({ engine: "kingfisher", confidence: "high", verified: null })] })} />);
    expect(section(/how sure/i).textContent ?? "").not.toMatch(/active|validated/i);
  });

  it("says the HTTP method is unknown when it is ANY, and says nothing when absent", () => {
    const { unmount } = render(<FindingExplain finding={finding({ attributes: { method: "ANY" } })} />);
    expect(section(/how sure/i).textContent ?? "").toMatch(/method.*not.*known/i);
    unmount();
    render(<FindingExplain finding={finding({ attributes: {} })} />);
    expect(section(/how sure/i).textContent ?? "").not.toMatch(/method/i);
  });

  it("flags runtime-built query strings and path placeholders only when the attributes say so", () => {
    const { unmount } = render(<FindingExplain finding={finding({ attributes: { method: "GET", dynamic_query: true, path_params: [{}] } })} />);
    const text = section(/how sure/i).textContent ?? "";
    expect(text).toMatch(/query string/i);
    expect(text).toMatch(/placeholder/i);
    unmount();
    render(<FindingExplain finding={finding()} />);
    expect(section(/how sure/i).textContent ?? "").not.toMatch(/query string|placeholder/i);
  });

  it("adds a spec-based next step only when a spec verdict exists", () => {
    const { unmount } = render(<FindingExplain finding={finding({ spec_status: { status: "shadow", reason: null, matched_operation: null } })} />);
    expect(section(/what to do next/i).textContent ?? "").toMatch(/not in the attached spec/i);
    unmount();
    render(<FindingExplain finding={finding()} />);
    expect(section(/what to do next/i).textContent ?? "").not.toMatch(/attached spec/i);
  });

  it("mentions an extension capture sighting only when the sightings say so", () => {
    render(<FindingExplain finding={finding({ sightings: { capture: 2, platform: 0 } })} />);
    expect(section(/how sure/i).textContent ?? "").toMatch(/2 other runs.*extension/i);
  });

  it("never renders a secret's evidence or value", () => {
    render(<FindingExplain finding={finding({ type: "secret", value: "RAW-SECRET-VALUE",
      occurrences: [occ({ evidence: "RAW-SECRET-EVIDENCE" })] })} />);
    expect(document.body.textContent).not.toMatch(/RAW-SECRET/);
  });
});
